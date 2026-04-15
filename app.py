"""
NBA Player Stats Web App
Requires: pip install flask nba_api
Run:      python app.py
"""

from flask import Flask, jsonify, render_template_string, request
from nba_api.stats.static import players
from nba_api.stats.endpoints import (
    playercareerstats,
    playergamelog,
    playerprofilev2,
    commonplayerinfo,
    leaguedashplayerstats,
    leaguegamelog,
    shotchartdetail,
)
import math, traceback, time
import requests as _requests

# Spoof a real browser to avoid NBA.com blocking cloud server IPs
_NBA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Referer': 'https://www.nba.com/',
    'Origin': 'https://www.nba.com',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
}
try:
    import nba_api.library.http as nba_http
    # Try different attribute names across nba_api versions
    if hasattr(nba_http, 'requests_session'):
        nba_http.requests_session.headers.update(_NBA_HEADERS)
    elif hasattr(nba_http, 'NBAStatsHTTP'):
        nba_http.NBAStatsHTTP.headers = _NBA_HEADERS
    else:
        # Patch the requests module directly as fallback
        _orig_get = _requests.get
        def _patched_get(url, **kwargs):
            kwargs.setdefault('headers', {}).update(_NBA_HEADERS)
            return _orig_get(url, **kwargs)
        _requests.get = _patched_get
except Exception as _e:
    print(f"[headers] could not set NBA headers: {_e}", flush=True)

app = Flask(__name__)

TIMEOUT = 90
CURRENT_SEASON = "2025-26"
SLEEP = 1.0   # seconds between NBA.com calls

def nba_call(fn, retries=3, sleep=SLEEP):
    """Call an nba_api endpoint with retries on empty/failed responses."""
    for attempt in range(retries):
        try:
            result = fn()
            time.sleep(sleep)
            return result
        except Exception as e:
            print(f"[nba_call] attempt {attempt+1} failed: {e}", flush=True)
            if attempt < retries - 1:
                time.sleep(sleep * (attempt + 2))
    raise RuntimeError(f"All {retries} attempts failed")

# Stats used for look-alike similarity (all numeric per-game stats)
SIMILARITY_STATS = [
    "PTS","REB","AST","STL","BLK","TOV",
    "FGM","FGA","FG_PCT","FG3M","FG3A","FG3_PCT",
    "FTM","FTA","FT_PCT","OREB","DREB","MIN",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_players(query: str) -> list:
    query = query.strip().lower()
    all_players = players.get_players()
    matches = [p for p in all_players if query in p["full_name"].lower()]
    matches.sort(key=lambda p: (not p["is_active"], p["full_name"]))
    return matches[:10]


def result_set_to_dicts(endpoint_obj, set_name: str) -> list:
    try:
        all_sets = endpoint_obj.get_dict().get("resultSets", [])
        for s in all_sets:
            if s.get("name", "").lower() == set_name.lower():
                return [dict(zip(s["headers"], row)) for row in s.get("rowSet", [])]
    except Exception:
        traceback.print_exc()
    return []


def get_career_stats(player_id: int) -> dict:
    def _career_call():
        try:
            return playercareerstats.PlayerCareerStats(
                player_id=player_id, per_mode_simple="PerGame", timeout=TIMEOUT)
        except TypeError:
            return playercareerstats.PlayerCareerStats(
                player_id=player_id, per_mode36="PerGame", timeout=TIMEOUT)
    career = nba_call(_career_call)
    return {
        "seasons": result_set_to_dicts(career, "SeasonTotalsRegularSeason"),
        "career":  result_set_to_dicts(career, "CareerTotalsRegularSeason"),
    }


def get_recent_games(player_id: int, season: str = CURRENT_SEASON) -> list:
    log = nba_call(lambda: playergamelog.PlayerGameLog(
        player_id=player_id, season=season,
        season_type_all_star="Regular Season", timeout=TIMEOUT))
    return result_set_to_dicts(log, "PlayerGameLog")[:5]


def get_career_highs(player_id: int) -> dict:
    def dedup_highs(rows: list) -> list:
        seen = set()
        out = []
        for r in rows:
            stat = r.get("STAT") or r.get("STAT_CATEGORY") or r.get("PT_CATEGORY")
            if stat and stat not in seen:
                seen.add(stat)
                out.append(r)
        return out

    try:
        profile = nba_call(lambda: playerprofilev2.PlayerProfileV2(
            player_id=player_id, timeout=TIMEOUT))
        return {
            "game_highs":   dedup_highs(result_set_to_dicts(profile, "CareerHighs")),
            "season_highs": dedup_highs(result_set_to_dicts(profile, "SeasonHighs")),
        }
    except Exception as e:
        print(f"[highs] failed for player {player_id}: {e}", flush=True)
        return {"game_highs": [], "season_highs": []}


def get_player_info(player_id: int) -> dict:
    info = nba_call(lambda: commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=TIMEOUT))
    rows = result_set_to_dicts(info, "CommonPlayerInfo")
    return rows[0] if rows else {}


# Cache league stats and player data in memory for the session
_league_cache: dict = {}
_player_cache: dict = {}

def get_league_stats(season: str = CURRENT_SEASON) -> list:
    cache_key = f"{season}_pergame"
    if cache_key in _league_cache:
        return _league_cache[cache_key]
    attempts = [{"per_mode_simple": "PerGame"}, {"per_mode36": "PerGame"}, {}]
    for kwargs in attempts:
        try:
            league = nba_call(lambda kw=kwargs: leaguedashplayerstats.LeagueDashPlayerStats(
                season=season, timeout=TIMEOUT, **kw))
            rows = result_set_to_dicts(league, "LeagueDashPlayerStats")
            if not rows:
                all_sets = league.get_dict().get("resultSets", [])
                for s in all_sets:
                    if s.get("rowSet"):
                        rows = [dict(zip(s["headers"], r)) for r in s["rowSet"]]
                        break
            if rows:
                _league_cache[cache_key] = rows
                return rows
        except Exception as e:
            print(f"[league] attempt failed: {e}", flush=True)
    return []


def get_league_advanced_stats(season: str = CURRENT_SEASON) -> list:
    """Fetch advanced stats (PER, TS%, etc) per player for the season."""
    cache_key = f"{season}_advanced"
    if cache_key in _league_cache:
        return _league_cache[cache_key]
    attempts = [
        {"per_mode_simple": "PerGame", "measure_type_detailed_defense": "Advanced"},
        {"per_mode36": "PerGame", "measure_type_detailed_defense": "Advanced"},
        {"measure_type_detailed_defense": "Advanced"},
    ]
    for kwargs in attempts:
        try:
            league = nba_call(lambda kw=kwargs: leaguedashplayerstats.LeagueDashPlayerStats(
                season=season, timeout=TIMEOUT, **kw))
            rows = result_set_to_dicts(league, "LeagueDashPlayerStats")
            if not rows:
                all_sets = league.get_dict().get("resultSets", [])
                for s in all_sets:
                    if s.get("rowSet"):
                        rows = [dict(zip(s["headers"], r)) for r in s["rowSet"]]
                        break
            if rows and any(r.get("PIE") is not None or r.get("EFG_PCT") is not None for r in rows[:5]):
                _league_cache[cache_key] = rows
                print(f"[advanced] got {len(rows)} rows, sample keys: {list(rows[0].keys())[:12]}", flush=True)
                return rows
        except Exception as e:
            print(f"[advanced] attempt failed: {e}", flush=True)
    return []


def get_season_ranks(player_id: int, season: str = CURRENT_SEASON) -> dict:
    RANK_STATS = ["PTS","REB","AST","STL","BLK","FG_PCT","FG3_PCT","FT_PCT"]
    MIN_GP = 15
    all_rows = get_league_stats(season)
    if not all_rows:
        return {}
    qualified = [r for r in all_rows if (r.get("GP") or 0) >= MIN_GP]
    player_row = next((r for r in qualified if int(r.get("PLAYER_ID",-1)) == int(player_id)), None)
    if not player_row:
        return {}
    ranks = {"total_players": len(qualified)}
    for stat in RANK_STATS:
        val = player_row.get(stat)
        if val is None: continue
        ranks[stat] = sum(1 for r in qualified if (r.get(stat) or 0) > val) + 1

    # Add PER from advanced stats
    adv_rows = get_league_advanced_stats(season)
    if adv_rows:
        adv_qualified = [r for r in adv_rows if (r.get("GP") or 0) >= MIN_GP]
        adv_player = next((r for r in adv_qualified if int(r.get("PLAYER_ID",-1)) == int(player_id)), None)
        if adv_player:
            per_key = next((k for k in ["PIE","PER","EFG_PCT"] if adv_player.get(k) is not None), None)
            if per_key:
                per_val = adv_player.get(per_key)
                ranks["PER"] = sum(1 for r in adv_qualified if (r.get(per_key) or 0) > per_val) + 1
                ranks["PER_val"] = round(per_val * 100 if per_key == "PIE" else per_val, 1)
                ranks["PER_key"] = per_key
    return ranks



PCT_STATS_SET = {"FG_PCT", "FG3_PCT", "FT_PCT"}

def _per_game_stats(row: dict) -> dict:
    """Convert a league stats row to per-game averages for SIMILARITY_STATS keys."""
    gp = row.get("GP") or 1
    result = {}
    for s in SIMILARITY_STATS:
        v = row.get(s)
        if v is None:
            continue
        result[s] = v if s in PCT_STATS_SET else round(v / gp, 3)
    return result


def get_lookalike(player_id: int, season: str = CURRENT_SEASON) -> dict:
    """
    Find the most statistically similar player this season using
    normalized Euclidean distance across per-game averages + PIE (efficiency).
    All counting stats are divided by GP before comparison so that
    games played differences don't skew the similarity score.
    PIE is included as an additional dimension with 2x weight.
    Excludes the player themselves. Only considers players with >= 15 GP.
    """
    MIN_GP = 15
    all_rows = get_league_stats(season)
    if not all_rows:
        return {}

    qualified = [r for r in all_rows if (r.get("GP") or 0) >= MIN_GP]
    target = next((r for r in qualified if int(r.get("PLAYER_ID",-1)) == int(player_id)), None)
    if not target:
        return {}

    # Build a lookup of advanced stats by player_id for PIE injection
    adv_rows = get_league_advanced_stats(season)
    adv_by_id = {}
    per_key = None
    if adv_rows:
        per_key = next((k for k in ["PIE","PER","EFG_PCT"]
                        if any(r.get(k) is not None for r in adv_rows[:5])), None)
        if per_key:
            adv_by_id = {int(r.get("PLAYER_ID",-1)): r for r in adv_rows}

    # Convert a row to a per-game feature vector, optionally including PIE
    def to_pg(row):
        gp = row.get("GP") or 1
        vec = {
            s: (row[s] if s in PCT_STATS_SET else row[s] / gp)
            for s in SIMILARITY_STATS if row.get(s) is not None
        }
        if per_key:
            pid = int(row.get("PLAYER_ID", -1))
            adv = adv_by_id.get(pid)
            if adv and adv.get(per_key) is not None:
                vec["_PIE"] = adv[per_key]  # store as normalized 0-1 value
        return vec

    target_pg = to_pg(target)
    valid_stats = list(target_pg.keys())

    # Compute per-stat std dev across per-game vectors for normalization
    def std(stat):
        vals = [to_pg(r).get(stat, 0) for r in qualified]
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        return math.sqrt(variance) or 1.0

    stds = {s: std(s) for s in valid_stats}

    # PIE gets 2x weight since it's a holistic efficiency metric
    PIE_WEIGHT = 2.0

    best_player, best_dist = None, float("inf")
    for row in qualified:
        if int(row.get("PLAYER_ID", -1)) == int(player_id):
            continue
        row_pg = to_pg(row)
        dist = math.sqrt(sum(
            ((target_pg.get(s, 0) - row_pg.get(s, 0)) ** 2)
            / stds[s] ** 2
            * (PIE_WEIGHT if s == "_PIE" else 1.0)
            for s in valid_stats
            if s in row_pg
        ))
        if dist < best_dist:
            best_dist = dist
            best_player = row

    if not best_player:
        return {}

    # Similarity score 0-100 (lower distance = higher similarity)
    similarity = max(0, round(100 - (best_dist / 10) * 100))

    stats = _per_game_stats(best_player)
    # Add PIE to the displayed stats
    if per_key:
        pid = int(best_player.get("PLAYER_ID", -1))
        adv = adv_by_id.get(pid)
        if adv and adv.get(per_key) is not None:
            stats["PIE"] = round(adv[per_key] * 100, 1)

    return {
        "player_id":   int(best_player.get("PLAYER_ID", 0)),
        "player_name": best_player.get("PLAYER_NAME", ""),
        "team":        best_player.get("TEAM_ABBREVIATION", ""),
        "similarity":  similarity,
        "stats":       stats,
        "gp":          best_player.get("GP"),
    }


def get_shot_chart(player_id: int, season: str = CURRENT_SEASON) -> list:
    try:
        chart = nba_call(lambda: shotchartdetail.ShotChartDetail(
            team_id=0, player_id=player_id,
            season_nullable=season,
            season_type_all_star="Regular Season",
            context_measure_simple="FGA",
            timeout=TIMEOUT,
        ))
        rows = result_set_to_dicts(chart, "Shot_Chart_Detail")
        return [{"x": r.get("LOC_X"), "y": r.get("LOC_Y"),
                 "made": r.get("SHOT_MADE_FLAG"), "type": r.get("SHOT_TYPE"),
                 "action": r.get("ACTION_TYPE"), "dist": r.get("SHOT_DISTANCE"),
                 "date": r.get("GAME_DATE"), "zone": r.get("SHOT_ZONE_BASIC")}
                for r in rows]
    except Exception as e:
        print(f"[shotchart] error: {e}", flush=True)
        return []


def get_compare_data(player_id: int) -> dict:
    """Slim data package for comparison view -- current season stats + info."""
    info = get_player_info(player_id)
    time.sleep(0.2)
    all_rows = get_league_stats(CURRENT_SEASON)
    season_row = next(
        (r for r in all_rows if int(r.get("PLAYER_ID", -1)) == int(player_id)), None)
    ranks = get_season_ranks(player_id, CURRENT_SEASON) if season_row else {}
    lookalike = get_lookalike(player_id, CURRENT_SEASON)
    return {
        "info":       info,
        "season_row": season_row,
        "ranks":      ranks,
        "lookalike":  lookalike,
    }


def get_league_leaders(season: str = CURRENT_SEASON) -> dict:
    """
    Returns top 5 per-game leaders (min 65% of max GP) for PTS/REB/AST/STL/BLK,
    plus top 5 individual single-game performances for each category.
    """
    LEAD_STATS = ["PTS", "REB", "AST", "STL", "BLK"]
    ADV_STATS  = ["PIE"]  # used for PER tile
    TOP_N = 5

    # ── Per-game leaders ──────────────────────────────────────────────────
    all_rows = get_league_stats(season)
    if not all_rows:
        return {}

    max_gp = max((r.get("GP") or 0) for r in all_rows)
    min_gp = int(max_gp * 0.65)
    qualified = [r for r in all_rows if (r.get("GP") or 0) >= min_gp]

    pg_leaders = {}
    for stat in LEAD_STATS:
        gp_key = "GP"
        sorted_rows = sorted(
            qualified,
            key=lambda r: (r.get(stat) or 0) / max((r.get(gp_key) or 1), 1),
            reverse=True
        )[:TOP_N]
        pg_leaders[stat] = [
            {
                "player_id":   int(r.get("PLAYER_ID", 0)),
                "player_name": r.get("PLAYER_NAME", ""),
                "team":        r.get("TEAM_ABBREVIATION", ""),
                "gp":          r.get("GP"),
                "value":       round((r.get(stat) or 0) / max((r.get("GP") or 1), 1), 1),
            }
            for r in sorted_rows
        ]

    # ── Top individual game performances ─────────────────────────────────
    try:
        log = nba_call(lambda: leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star="Regular Season",
            player_or_team_abbreviation="P",
            timeout=TIMEOUT,
        ))
        game_rows = result_set_to_dicts(log, "LeagueGameLog")
    except Exception as e:
        print(f"[leaders] game log error: {e}", flush=True)
        game_rows = []

    top_games = {}
    for stat in LEAD_STATS:
        sorted_games = sorted(
            [r for r in game_rows if r.get(stat) is not None],
            key=lambda r: r.get(stat) or 0,
            reverse=True
        )[:TOP_N]
        top_games[stat] = [
            {
                "player_name": r.get("PLAYER_NAME", ""),
                "team":        r.get("TEAM_ABBREVIATION", ""),
                "matchup":     r.get("MATCHUP", ""),
                "game_date":   r.get("GAME_DATE", ""),
                "value":       r.get(stat),
            }
            for r in sorted_games
        ]

    # ── PER leaders (from advanced stats) ────────────────────────────────
    adv_rows = get_league_advanced_stats(season)
    per_leaders = []
    if adv_rows:
        adv_qualified = [r for r in adv_rows if (r.get("GP") or 0) >= min_gp]
        # Try PIE first (NBA's version), fall back to EFG_PCT
        per_key = "PIE" if any(r.get("PIE") is not None for r in adv_qualified[:5]) else "EFG_PCT"
        sorted_adv = sorted(adv_qualified, key=lambda r: r.get(per_key) or 0, reverse=True)[:TOP_N]
        per_leaders = [
            {
                "player_id":   int(r.get("PLAYER_ID", 0)),
                "player_name": r.get("PLAYER_NAME", ""),
                "team":        r.get("TEAM_ABBREVIATION", ""),
                "gp":          r.get("GP"),
                "value":       round((r.get(per_key) or 0) * 100 if per_key == "PIE" else (r.get(per_key) or 0), 1),
                "per_key":     per_key,
            }
            for r in sorted_adv
        ]

    return {
        "season":      season,
        "min_gp":      min_gp,
        "max_gp":      max_gp,
        "pg_leaders":  pg_leaders,
        "top_games":   top_games,
        "per_leaders": per_leaders,
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2: return jsonify([])
    return jsonify(find_players(q))


@app.route("/api/player/<int:player_id>")
def api_player(player_id):
    # Return core player data quickly -- no league stats or lookalike
    cache_key = f"core_{player_id}"
    if cache_key in _player_cache:
        print(f"[cache] serving player {player_id} core from cache", flush=True)
        return jsonify(_player_cache[cache_key])
    try:
        info        = get_player_info(player_id)
        career_data = get_career_stats(player_id)
        highs       = get_career_highs(player_id)

        recent = []
        for season in [CURRENT_SEASON, "2024-25", "2023-24"]:
            recent = get_recent_games(player_id, season)
            if recent: break

        seasons = career_data["seasons"]
        current_season_row = None
        if seasons:
            last = seasons[-1]
            if last.get("SEASON_ID","") == CURRENT_SEASON or last.get("SEASON_ID","").startswith(CURRENT_SEASON[:4]):
                current_season_row = last

        result = {
            "info":              info,
            "seasons":           seasons,
            "career_totals":     career_data["career"],
            "recent_games":      recent,
            "game_highs":        highs["game_highs"],
            "season_highs":      highs["season_highs"],
            "current_season":    current_season_row,
            "current_season_id": CURRENT_SEASON,
            "season_ranks":      {},
            "lookalike":         {},
        }
        _player_cache[cache_key] = result
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/player/<int:player_id>/extras")
def api_player_extras(player_id):
    # Return rankings + lookalike -- slower, loads after core
    cache_key = f"extras_{player_id}"
    if cache_key in _player_cache:
        print(f"[cache] serving player {player_id} extras from cache", flush=True)
        return jsonify(_player_cache[cache_key])
    try:
        core = _player_cache.get(f"core_{player_id}", {})
        has_current = core.get("current_season") is not None

        season_ranks = get_season_ranks(player_id) if has_current else {}
        lookalike    = get_lookalike(player_id)    if has_current else {}

        result = {
            "season_ranks": season_ranks,
            "lookalike":    lookalike,
        }
        _player_cache[cache_key] = result
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare")
def api_compare():
    """
    Returns slim season stat packages for two players + their look-alikes.
    ?p1=<id>&p2=<id>
    """
    try:
        p1 = int(request.args.get("p1", 0))
        p2 = int(request.args.get("p2", 0))
        if not p1 or not p2:
            return jsonify({"error": "p1 and p2 player IDs required"}), 400
        d1 = get_compare_data(p1)
        d2 = get_compare_data(p2)
        return jsonify({"player1": d1, "player2": d2, "season": CURRENT_SEASON})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/shotchart/<int:player_id>")
def api_shotchart(player_id):
    try:
        season = request.args.get("season", CURRENT_SEASON)
        cache_key = f"shots_{player_id}_{season}"
        if cache_key in _player_cache:
            return jsonify(_player_cache[cache_key])
        shots = get_shot_chart(player_id, season)
        result = {"shots": shots, "season": season}
        _player_cache[cache_key] = result
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/leaders")
def api_leaders():
    try:
        data = get_league_leaders(CURRENT_SEASON)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug/league")
def api_debug_league():
    try:
        league = leaguedashplayerstats.LeagueDashPlayerStats(season=CURRENT_SEASON, timeout=TIMEOUT)
        time.sleep(0.6)
        return jsonify({"season": CURRENT_SEASON, "result_sets": [
            {"name": s["name"], "row_count": len(s.get("rowSet",[])), "headers": s.get("headers",[])[:10]}
            for s in league.get_dict().get("resultSets", [])
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug/highs/<int:player_id>")
def api_debug_highs(player_id):
    """Dump raw CareerHighs result set keys and first 3 rows."""
    try:
        profile = nba_call(lambda: playerprofilev2.PlayerProfileV2(
            player_id=player_id, timeout=TIMEOUT))
        all_sets = profile.get_dict().get("resultSets", [])
        out = {}
        for s in all_sets:
            if "high" in s.get("name","").lower():
                rows = s.get("rowSet", [])
                out[s["name"]] = {
                    "headers": s.get("headers", []),
                    "sample":  rows[:3],
                }
        return jsonify(out)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

HTML = open("templates/index.html", encoding="utf-8").read()

@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
