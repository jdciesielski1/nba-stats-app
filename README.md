# 🏀 NBA Player Stats App

A full-stack NBA analytics web app built with Python and Flask that pulls live data from the NBA API.

## Features

**Player Stats**
- Search any active or historical NBA player
- Career averages, season-by-season splits, and 5 most recent games
- Career and season highs (deduplicated by stat category)
- Interactive SVG shot chart with make/miss filtering and hover tooltips

**Season Rankings**
- Current season per-game averages with league rank badges (top 5 / top 10 / top 25)
- PIE (Player Impact Estimate) efficiency rating and rank
- Minimum 15 GP threshold to filter noise

**Player Comparison**
- Side-by-side head-to-head stat bars for two players
- Dual shot chart comparison
- Statistical look-alike finder for each player

**Look-alike Engine**
- Finds the most statistically similar player in the league using normalized Euclidean distance
- Compares across 18 per-game metrics (PTS, REB, AST, STL, BLK, TOV, shooting splits, etc.)
- Includes PIE efficiency with 2x weight as a holistic performance signal
- Displays look-alike's per-game averages and shot chart

**League Leaders**
- Top 5 per-game leaders for PTS, REB, AST, STL, BLK, and PIE
- Min 65% of league-high GP to qualify
- Top 5 single-game performances for each category this season

## Stack

| Layer | Tech |
|---|---|
| Backend | Python / Flask |
| Data | nba_api |
| Frontend | Vanilla JS + CSS + SVG (no build step) |

## Setup
```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5050 in your browser.

## Notes

- All data pulled live from NBA.com via nba_api
- League stats are cached in memory per session to reduce API calls
- Shot charts load asynchronously after the main player data
- Includes retry logic with backoff for NBA.com rate limiting
