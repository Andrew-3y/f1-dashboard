# F1 Strategy Intelligence Dashboard

A comprehensive, free Formula 1 analytics platform that delivers session-specific intelligence for **races**, **qualifying**, and **practice sessions**. Fetches real timing data via FastF1 and transforms it into actionable insights — whether you're watching live or catching up on a session you missed.

**Architecture:** On-demand, zero-cost. The app sleeps on Render's free tier and wakes on request. No always-running server, no paid services, no database.

---

## Features

### Race & Sprint Analysis
- **Live leaderboard** with finishing positions, best laps, and gaps
- **Tire degradation modeling** — linear regression on clean stint laps, classified IMPROVING / STABLE / MODERATE / HIGH / CRITICAL
- **Pit window prediction** — estimates when cumulative tire loss exceeds pit stop cost (OPEN / APPROACHING / CLOSED)
- **Pit strategy simulator** — simulates "what if this driver pits now?", predicts rejoin position, traffic risk, and undercut feasibility (PIT NOW / CONSIDER PIT / STAY OUT / HOLD)
- **Battle detection** — identifies drivers within 2s, tracks closing rates, classifies intensity (INTENSE / CLOSE / WATCHING) with DRS flags
- **Overtake predictions** — gap-closing rate analysis with estimated laps to DRS range
- **Anomaly detection** — rolling-average pace analysis, severity levels CRITICAL / HIGH / MEDIUM / LOW

### Qualifying Analysis
- **Full results with sector breakdown** — S1, S2, S3 times color-coded (purple = best in session, green = within 0.1s, yellow = within 0.3s, orange = off pace)
- **Tyre compound per driver** on their best lap
- **Q1 / Q2 / Q3 elimination tracker** — who got knocked out, gap to the cutoff line
- **Close calls** — exact margin between last-safe and first-eliminated at each cutoff
- **Teammate head-to-head** — who beat who within each team, gap in seconds and percentage
- **Team qualifying pace ranking** — teams ordered by best driver, intra-team gap shown
- **Theoretical best lap** — best S1 + best S2 + best S3 per driver, showing time left on the table vs. actual best
- **Lap improvement progression** — how much each driver improved from first flying lap to best lap, with attempt count
- **Track evolution** — how the circuit got faster through the session in 4 phases (Early / Mid-Early / Mid-Late / Late)
- **Tyre strategy breakdown** — compounds used per driver, lap counts per compound, best time on each

### Practice Session Analysis
- **Single-lap pace ranking** — qualifying simulation with best lap, top-3 average, compound, consistency (std dev), and gap to fastest
- **Long run pace** — sustained stints (5+ laps) ranked by fuel-corrected average, with first-to-last-lap trend and degradation rate
- **Race pace prediction** — estimated race-day order from fuel-corrected long run aggregation
- **Compound comparison** — best/avg/median pace per tyre type, driver and lap counts
- **Tyre degradation curves** — per-compound degradation slope aggregated across stints (STABLE / LOW / MODERATE / HIGH)
- **Team pace ranking** — teams ordered by best representative time, both drivers shown
- **Driver consistency** — std-dev ranking classified EXCELLENT / GOOD / AVERAGE / INCONSISTENT
- **Theoretical best lap** — best S1 + S2 + S3 combined, time left on the table
- **Sector analysis** — best sector times per driver, color-coded by delta to overall session best
- **Track evolution** — 5-phase breakdown (Opening / Early / Middle / Late / Final) showing how the track rubbered in
- **Driver programme summary** — total laps, stint count, long-run vs short-run split, time on track, compounds used

### Navigation & UI
- **Weekend navigation bar** — one-click switching between FP1, FP2, FP3, Qualifying, Sprint, and Race for the current round
- **Session selector** — manual year/round/session picker for historical data (supports all session types)
- **Auto-refresh** — configurable: OFF / Live (30s) / Session (60s) / Casual (5min)
- **Dark F1-themed interface** — color-coded compound badges, sector classifications, strategy tags, intensity indicators

---

## Project Structure

```
f1-dashboard/
├── app.py                # Flask server — routes requests to session-specific analysis
├── data_handler.py       # FastF1 integration, caching, leaderboard building
├── anomaly.py            # Lap-time anomaly detection (rolling average)
├── predictor.py          # Overtake prediction (gap-closing rate analysis)
├── degradation.py        # Tire degradation modeling + pit window prediction
├── strategy.py           # Pit strategy simulator (undercut/overcut/traffic)
├── battle_detector.py    # On-track battle detection
├── qualifying.py         # Qualifying-specific analysis (9 modules)
├── practice.py           # Practice-specific analysis (11 modules)
├── requirements.txt      # Python dependencies
├── render.yaml           # Render deployment blueprint
├── .gitignore
└── templates/
    └── dashboard.html    # Full HTML/CSS/JS dashboard (single file, ~1400 lines)
```

---

## How It Works

```
User visits URL
       │
       ▼
   Flask receives GET /
       │
       ▼
   data_handler.py fetches session via FastF1
       │
       ├── Detects session type
       │
       ▼
   ┌─────────────────────────────────────┐
   │  Session Router (app.py)            │
   │                                     │
   │  Race/Sprint ──► anomaly.py         │
   │                  predictor.py       │
   │                  degradation.py     │
   │                  strategy.py        │
   │                  battle_detector.py │
   │                                     │
   │  Qualifying ───► qualifying.py      │
   │                  (9 analysis modules)│
   │                                     │
   │  Practice ─────► practice.py        │
   │                  (11 analysis modules)│
   └─────────────────────────────────────┘
       │
       ▼
   Results injected into dashboard.html
       │
       ▼
   Rendered page returned to user
```

Each analysis module is wrapped in try/except — a failure in one never crashes the dashboard.

---

## Module Reference

### `app.py` — Application Entry Point
Routes requests to the correct analysis pipeline based on session type. Classifies sessions into three categories (race, qualifying, practice) and runs only the relevant modules. Serves three endpoints: `/` (dashboard), `/api/data` (JSON), `/health` (Render health check).

### `data_handler.py` — Data Layer
All FastF1 communication. `get_latest_session_info()` scans the F1 calendar for the most recent completed session. `load_session()` downloads and caches lap data in memory. `build_leaderboard()` uses finishing positions for races and fastest lap for qualifying/practice.

### `qualifying.py` — Qualifying Intelligence (9 modules)
| Module | Algorithm |
|--------|-----------|
| Sector Breakdown | Extracts S1/S2/S3 from each driver's fastest lap, computes delta to session-best sector, classifies as best/good/ok/slow |
| Elimination Tracker | Reconstructs Q1/Q2/Q3 phases from sorted best laps (top 10 = Q3, 11-15 = Q2 eliminated, 16-20 = Q1 eliminated) |
| Close Calls | Computes exact margin between last-safe and first-eliminated at each cutoff boundary |
| Teammate Battles | Groups drivers by team, compares best laps, computes gap in seconds and percentage |
| Team Pace | Ranks teams by best driver's time, shows intra-team gap |
| Theoretical Best | Combines each driver's personal best S1 + S2 + S3 from any lap, compares to actual best |
| Improvement | Tracks delta from first flying lap to best lap per driver |
| Track Evolution | Splits session into 4 time phases, shows fastest lap and average per phase |
| Tyre Strategy | Maps compound usage per driver with lap counts and best times per compound |

### `practice.py` — Practice Intelligence (11 modules)
| Module | Algorithm |
|--------|-----------|
| Short Run Pace | Best lap per driver with top-3 average, consistency (std dev), compound |
| Long Run Pace | Stints of 5+ laps, fuel-corrected average (0.06s/lap fuel effect), linear regression for degradation slope |
| Race Pace Prediction | Aggregates fuel-corrected long run data per driver to predict race-day pace order |
| Compound Comparison | Best/avg/median pace per tyre type across all drivers |
| Tyre Deg Curves | Per-compound degradation slope aggregated across multiple stints, classified STABLE/LOW/MODERATE/HIGH |
| Team Ranking | Teams ranked by best driver's time, both drivers shown |
| Consistency | Std-dev of cleaned lap times, rated EXCELLENT (<0.3s) / GOOD (<0.5s) / AVERAGE (<0.8s) / INCONSISTENT |
| Theoretical Best | Best S1 + S2 + S3 combined per driver |
| Sector Analysis | Best sector times per driver with delta classification |
| Track Evolution | 5-phase session breakdown showing rubber build-up progression |
| Driver Programmes | Total laps, stint count, long-run vs short-run split, time on track, compounds used |

### Race Analysis Modules
| Module | File | Algorithm |
|--------|------|-----------|
| Anomaly Detection | `anomaly.py` | 5-lap rolling average, flags laps >1s slower. Severity: CRITICAL (>3s) / HIGH (>2s) / MEDIUM (>1.5s) / LOW (>1s) |
| Overtake Prediction | `predictor.py` | Gap-closing rate over last 8 laps, estimates laps to DRS range (<1s) |
| Tire Degradation | `degradation.py` | Linear regression on clean stint laps (excludes pits, outliers >3s from median). Pit window = cumulative loss vs. ~23s pit cost |
| Pit Strategy | `strategy.py` | Simulates pit stop: adds 23s, recalculates rejoin position, checks undercut (1.5s/lap advantage over 3 laps) and traffic risk (within 2s) |
| Battle Detection | `battle_detector.py` | Scans consecutive pairs <2s apart, calculates closing rate from last 5 laps. INTENSE (<1s) / CLOSE (<1.5s) / WATCHING (<2s) |

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

Open `http://localhost:5000`. The dashboard auto-detects the latest completed F1 session.

### Try Different Sessions
```
http://localhost:5000/?year=2024&round=24&session_type=Race
http://localhost:5000/?year=2024&round=24&session_type=Qualifying
http://localhost:5000/?year=2024&round=24&session_type=Practice+1
```

Or use the **Weekend Navigation Bar** to switch between sessions with one click.

---

## Deployment on Render (Free Tier)

### Auto-Deploy
Push to GitHub and Render redeploys automatically:
```bash
git add .
git commit -m "describe your change"
git push origin main
```

### First-Time Setup
1. Sign up at [render.com](https://render.com) (free) and connect GitHub
2. **New + → Web Service** → select `f1-dashboard`
3. Configure:

| Setting | Value |
|---------|-------|
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
| Plan | Free |

4. Add environment variable: `FASTF1_CACHE` = `/tmp/fastf1_cache`
5. Deploy

---

## Usage Guide

### During a Live Race
1. Open the dashboard during or after a session
2. Set Auto-Refresh to **Live (30s)**
3. Watch for pit windows turning OPEN, battle intensity changes, PIT NOW strategy signals, and anomaly alerts
4. FastF1 data typically becomes available 1-2 hours after a session ends

### Catching Up on Qualifying You Missed
1. Navigate to the qualifying session via the Weekend bar or session selector
2. Check the **Elimination Tracker** to see who got knocked out and where
3. Look at **Teammate Battles** to see which driver had the edge in each team
4. Check **Close Calls** for the most dramatic cutoff margins
5. Review **Theoretical Best** to see who had untapped pace
6. Look at **Track Evolution** to understand session conditions

### Scouting Practice Before the Race
1. Load the practice session (FP1/FP2/FP3)
2. **Race Pace Prediction** gives you the expected race-day pecking order
3. **Tyre Deg Curves** shows which compound will struggle and which will last
4. **Long Run Pace** reveals who ran race simulations and how they compared
5. **Driver Programmes** shows who did the most running and on which tyres
6. **Compound Comparison** helps predict optimal race strategy

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No completed session found" | Off-season. Use manual selector (e.g. year=2024, round=24, Race) |
| First load is slow (30-60s) | Normal — FastF1 downloads data on first request, cached after |
| Degradation shows N/A | Stint too short (<4 clean laps). Common early in a race |
| Strategy shows no data | Requires cumulative race time — only available in Race sessions |
| Practice panels empty | Ensure you selected the correct session type (Practice 1/2/3) |
| No sector data | Some older sessions lack sector timing in FastF1 |
| Render build failed | Check build logs — usually a pip dependency issue |

---

## Tech Stack

| Technology | Purpose | Cost |
|-----------|---------|------|
| Python 3.11 | Core language | Free |
| Flask 3.1 | Web framework | Free |
| FastF1 | Official F1 timing data | Free |
| Pandas | Data processing & analysis | Free |
| NumPy | Numerical computation | Free |
| Gunicorn | Production WSGI server | Free |
| Render | Cloud hosting | Free tier |

**Total cost: $0**

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| Session-specific analysis routing | Race/qualifying/practice have fundamentally different data patterns — running pit strategy analysis on a qualifying session is meaningless |
| Module-level try/except isolation | One module failing never crashes the dashboard — graceful degradation |
| In-memory session cache | Repeat requests within the same Render wake cycle are instant |
| Linear regression over ML | Simple math runs fast on Render's constrained CPU; no model training needed |
| Single HTML file | No build tools, no CDN dependencies, zero frontend complexity |
| Fuel correction in practice | Long run times are misleading without accounting for ~0.06s/lap fuel burn-off |

---

## Portfolio Description

> **F1 Strategy Intelligence Dashboard** — A full-stack Formula 1 analytics platform built with Python and Flask. Delivers session-specific intelligence across races, qualifying, and practice with 25+ analysis modules including tire degradation modeling via linear regression, pit strategy simulation, on-track battle detection, qualifying elimination tracking with close-call analysis, theoretical best lap computation, race pace prediction from fuel-corrected long run data, and tyre degradation curves per compound. Features a weekend navigation system for seamless session switching and a dark, responsive F1-themed interface. Deployed on Render's free tier using FastF1's public timing API with zero infrastructure cost.
