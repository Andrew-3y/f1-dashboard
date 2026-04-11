# F1 Strategy Intelligence Dashboard

A free Formula 1 analytics dashboard built with Python, Flask, Pandas, and FastF1. It turns official timing data into session-specific insights for races, qualifying, sprint weekends, practice sessions, and now season-level form tracking.

Architecture: on-demand and zero-cost. The app sleeps on Render's free tier, wakes on request, and does not depend on a database or background workers.

---

## Features

### Race and Sprint Analysis
- Live leaderboard with finishing positions, best laps, and gaps
- Tire degradation modeling from clean stint laps
- Pit window prediction based on cumulative tire loss vs pit stop cost
- Pit strategy simulator with rejoin position, traffic risk, and undercut signals
- Battle detection for close on-track fights
- Overtake prediction from recent gap-closing rate
- Anomaly detection for meaningful pace drop-offs

### Qualifying Analysis
- Official classification with sector breakdown
- Tyre compound used on each driver's best lap
- Q1, Q2, and Q3 elimination tracker
- Close-call cutoff analysis
- Teammate head-to-head comparison
- Team qualifying pace ranking
- Theoretical best lap analysis
- Lap improvement progression
- Track evolution breakdown
- Tyre usage analysis
- Projected race finish from qualifying plus practice context
- FP3 projection accuracy vs actual qualifying result

### Sprint Shootout Analysis
- Dedicated Sprint Shootout page
- Official sprint shootout classification
- SQ1, SQ2, and SQ3 elimination tracking
- Projected sprint finish
- Sprint projection accuracy after the sprint is complete

### Practice Analysis
- Single-lap pace ranking
- Long-run pace ranking
- FP3 qualifying projection
- Race pace prediction
- Compound comparison
- Tyre degradation curves by compound
- Team pace ranking
- Driver consistency ranking
- Theoretical best lap
- Sector analysis
- Track evolution breakdown
- Driver programme summary

### Season Form Tracker
- Dedicated season page at `/season`
- Driver form over the last N completed rounds
- Team form over the last N completed rounds
- Season-level teammate head-to-head tracking
- Momentum-focused summary cards for the hottest driver, hottest team, and strongest recent qualifiers

### Navigation and UI
- Weekend navigation bar for fast session switching
- Manual session selector for historical rounds
- Auto-refresh with multiple refresh speeds
- Session data-quality audit
- Mobile-friendly layout
- Dark F1-inspired interface

---

## Project Structure

```text
f1-dashboard/
|-- app.py                    # Flask app and route wiring
|-- data_handler.py           # FastF1 loading, caching, leaderboard building
|-- anomaly.py                # Race anomaly detection
|-- predictor.py              # Overtake prediction
|-- degradation.py            # Tire degradation and pit window logic
|-- strategy.py               # Pit strategy simulation
|-- battle_detector.py        # On-track battle detection
|-- season_form.py            # Season-level form tracking
|-- qualifying.py             # Qualifying analysis pipeline
|-- practice.py               # Practice analysis pipeline
|-- race_projection.py        # Pre-race and pre-sprint projection logic
|-- prediction_accuracy.py    # Projection vs result scoring
|-- validation.py             # Session validation checks
|-- requirements.txt          # Python dependencies
|-- render.yaml               # Render deployment config
`-- templates/
    |-- dashboard.html        # Main dashboard template
    `-- season.html           # Season form tracker template
```

---

## Routes

- `GET /` - Main dashboard
- `GET /api/data` - JSON data endpoint used by the UI refresh flow
- `GET /season` - Season form tracker
- `GET /health` - Health check

---

## How It Works

```text
User request
  -> app.py
  -> data_handler.py loads the selected session
  -> app.py picks the correct analysis path

Race or Sprint:
  -> anomaly.py
  -> predictor.py
  -> degradation.py
  -> strategy.py
  -> battle_detector.py

Qualifying or Sprint Shootout:
  -> qualifying.py
  -> race_projection.py
  -> prediction_accuracy.py

Practice:
  -> practice.py

Season page:
  -> season_form.py

All views:
  -> validation.py where relevant
  -> Jinja templates render the final page
```

Each analysis module is isolated with defensive error handling so one failure does not take down the whole dashboard.

---

## Module Reference

### `app.py` - Application Entry Point
Creates the Flask app, routes requests, chooses the correct analysis pipeline, and renders the dashboard or season page.

### `data_handler.py` - Data Layer
Handles FastF1 schedule lookup, session loading, in-memory caching, and leaderboard construction.

### `qualifying.py` - Qualifying Intelligence
Builds sector analysis, elimination tracking, close calls, teammate battles, team pace, theoretical best laps, improvement analysis, track evolution, and tyre usage.

### `practice.py` - Practice Intelligence
Builds short-run pace, long-run pace, qualifying projections, race pace predictions, compound analysis, team ranking, consistency metrics, track evolution, and driver programme summaries.

### `race_projection.py` - Pre-Race Projection
Uses qualifying plus available practice context to estimate projected race or sprint finishing order.

### `prediction_accuracy.py` - Accuracy Tracking
Compares projected results against official finishing or qualifying orders and reports exact-match rate, average error, and top-group overlap.

### `validation.py` - Data Quality Audit
Runs context-aware pass, warn, and fail checks against derived outputs and official tables.

### `season_form.py` - Season Form Tracker
Aggregates completed qualifying and race results across rounds to compute driver momentum, team momentum, and teammate battle trends.

---

## Local Setup

### Prerequisites
- Python 3.9+
- Git

### Quick Start

```bash
git clone https://github.com/Andrew-3y/f1-dashboard.git
cd f1-dashboard
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

---

## Example URLs

```text
http://localhost:5000/?year=2024&round=24&session_type=Race
http://localhost:5000/?year=2024&round=24&session_type=Qualifying
http://localhost:5000/?year=2024&round=24&session_type=Practice+1
http://localhost:5000/?year=2024&round=21&session_type=Sprint+Shootout
http://localhost:5000/season?year=2025&window=5
```

---

## Deployment on Render

### Auto-Deploy

```bash
git add .
git commit -m "describe your change"
git push origin main
```

### Render Settings

| Setting | Value |
|---|---|
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
| Plan | Free |

Optional environment variable:

- `FASTF1_CACHE=/tmp/fastf1_cache`

---

## Usage Guide

### During a Live Race
1. Open the race session.
2. Turn on auto-refresh.
3. Watch for pit windows, undercut signals, battle alerts, and anomaly flags.

### Reviewing Qualifying
1. Open the qualifying session.
2. Start with projected race finish.
3. Check FP3 projection accuracy.
4. Use elimination, close-call, teammate, and theoretical best panels to understand the session story.

### Using the Season Form Tracker
1. Open `/season`.
2. Choose a season year and round window.
3. Review driver momentum, team momentum, and teammate trends.
4. Use it to add context before a new weekend or after a recent run of races.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| No completed session found | Use the manual selector for a past round |
| First load is slow | Normal on a cold FastF1 cache |
| Cold start shows a warming message | Wait a few seconds and reload |
| Some panels are empty | The selected session may not contain enough data for that analysis |
| Older sessions have missing sector data | FastF1 coverage can vary by session |
| Render build failed | Check the deployment logs for dependency or startup issues |

---

## Tech Stack

| Technology | Purpose |
|---|---|
| Python | Core application language |
| Flask | Web framework |
| FastF1 | Official timing and results source |
| Pandas | Data processing and aggregation |
| NumPy | Numerical support |
| Gunicorn | Production web server |
| Render | Hosting |

---

## Architecture Decisions

| Decision | Rationale |
|---|---|
| Session-specific analysis routing | Race, qualifying, practice, and season views need different logic |
| In-memory session cache | Repeated requests stay fast during the same app wake cycle |
| On-demand architecture | Keeps the project free to run |
| Separate analysis modules | Makes features easier to extend without rewriting the app |
| Season page as a dedicated route | Adds a new product dimension without cluttering the main session dashboard |

---

## Portfolio Description

> F1 Strategy Intelligence Dashboard is a full-stack Formula 1 analytics platform built with Python and Flask. It combines FastF1 timing data with custom analysis modules for race strategy, qualifying intelligence, practice analysis, projection accuracy tracking, and season-level momentum tracking, all inside a lightweight on-demand web app.
