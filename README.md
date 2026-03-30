# F1 Strategy Intelligence Dashboard

A comprehensive, free Formula 1 analytics platform that delivers session-specific intelligence for **races**, **qualifying**, and **practice sessions**. Fetches real timing data via FastF1 and transforms it into actionable insights â€” whether you're watching live or catching up on a session you missed.

**Architecture:** On-demand, zero-cost. The app sleeps on Render's free tier and wakes on request. No always-running server, no paid services, no database.

---

## Features

### Race & Sprint Analysis
- **Live leaderboard** with finishing positions, best laps, and gaps
- **Tire degradation modeling** â€” linear regression on clean stint laps, classified IMPROVING / STABLE / MODERATE / HIGH / CRITICAL
- **Pit window prediction** â€” estimates when cumulative tire loss exceeds pit stop cost (OPEN / APPROACHING / CLOSED)
- **Pit strategy simulator** â€” simulates "what if this driver pits now?", predicts rejoin position, traffic risk, and undercut feasibility (PIT NOW / CONSIDER PIT / STAY OUT / HOLD)
- **Battle detection** â€” identifies drivers within 2s, tracks closing rates, classifies intensity (INTENSE / CLOSE / WATCHING) with DRS flags
- **Overtake predictions** â€” gap-closing rate analysis with estimated laps to DRS range
- **Anomaly detection** â€” rolling-average pace analysis, severity levels CRITICAL / HIGH / MEDIUM / LOW

### Qualifying Analysis
- **Full results with sector breakdown** â€” S1, S2, S3 times color-coded (purple = best in session, green = within 0.1s, yellow = within 0.3s, orange = off pace)
- **Tyre compound per driver** on their best lap
- **Projected race finish** â€” qualifying-page pre-race forecast that blends grid position with available practice long-run pace and qualifying-form signals
- **Projection accuracy tracking** â€” compares FP3 qualifying projections with actual qualifying and pre-race finish projections with the official race result using exact-match rate, average position error, and top-10 overlap
- **Q1 / Q2 / Q3 elimination tracker** â€” who got knocked out, gap to the cutoff line
- **Close calls** â€” exact margin between last-safe and first-eliminated at each cutoff
- **Teammate head-to-head** â€” who beat who within each team, gap in seconds and percentage
- **Team qualifying pace ranking** â€” teams ordered by best driver, intra-team gap shown
- **Theoretical best lap** â€” best S1 + best S2 + best S3 per driver, showing time left on the table vs. actual best
- **Lap improvement progression** â€” how much each driver improved from first flying lap to best lap, with attempt count
- **Track evolution** â€” how the circuit got faster through the session in 4 phases (Early / Mid-Early / Mid-Late / Late)
- **Tyre strategy breakdown** â€” compounds used per driver, lap counts per compound, best time on each

### Practice Session Analysis
- **Single-lap pace ranking** â€” qualifying simulation with best lap, top-3 average, compound, consistency (std dev), and gap to fastest
- **Long run pace** â€” sustained stints (5+ laps) ranked by fuel-corrected average, with first-to-last-lap trend and degradation rate
- **Projected qualifying order (FP3)** â€” final pre-quali forecast using weighted FP1/FP2/FP3 short-run pace, theoretical best, and sector strength
- **Race pace prediction** â€” estimated race-day order from fuel-corrected long run aggregation
- **Compound comparison** â€” best/avg/median pace per tyre type, driver and lap counts
- **Tyre degradation curves** â€” per-compound degradation slope aggregated across stints (STABLE / LOW / MODERATE / HIGH)
- **Team pace ranking** â€” teams ordered by best representative time, both drivers shown
- **Driver consistency** â€” std-dev ranking classified EXCELLENT / GOOD / AVERAGE / INCONSISTENT
- **Theoretical best lap** â€” best S1 + S2 + S3 combined, time left on the table
- **Sector analysis** â€” best sector times per driver, color-coded by delta to overall session best
- **Track evolution** â€” 5-phase breakdown (Opening / Early / Middle / Late / Final) showing how the track rubbered in
- **Driver programme summary** â€” total laps, stint count, long-run vs short-run split, time on track, compounds used

### Navigation & UI
- **Weekend navigation bar** â€” one-click switching between FP1, FP2, FP3, Qualifying, Sprint, and Race for the current round
- **Session selector** â€” manual year/round/session picker for historical data (supports all session types)
- **Auto-refresh** â€” configurable: OFF / Live (30s) / Session (60s) / Casual (5min), persists across reloads
- **F1-style lap-time formatting** â€” lap and pace times are shown as `M:SS.mmm` instead of raw seconds
- **Ordered projection inputs** â€” projection cards list sessions in weekend order (FP1 â†’ FP2 â†’ FP3, then Qualifying where applicable)
- **Readable projection explanations** â€” projection cards show plain-language reasons and clearer driver labels instead of raw shorthand where possible
- **Session data-quality audit** â€” every session now shows pass/warn/fail validation checks over official tables and derived modules
- **Mobile-friendly layout** â€” responsive tweaks on small screens without changing desktop layout
- **Dark F1-themed interface** â€” color-coded compound badges, sector classifications, strategy tags, intensity indicators

---

## Project Structure

```
f1-dashboard/
â”œâ”€â”€ app.py                # Flask server â€” routes requests to session-specific analysis
â”œâ”€â”€ data_handler.py       # FastF1 integration, caching, leaderboard building
â”œâ”€â”€ anomaly.py            # Lap-time anomaly detection (rolling average)
â”œâ”€â”€ predictor.py          # Overtake prediction (gap-closing rate analysis)
â”œâ”€â”€ degradation.py        # Tire degradation modeling + pit window prediction
â”œâ”€â”€ strategy.py           # Pit strategy simulator (undercut/overcut/traffic)
â”œâ”€â”€ battle_detector.py    # On-track battle detection
â”œâ”€â”€ qualifying.py         # Qualifying-specific analysis (9 modules)
â”œâ”€â”€ practice.py           # Practice-specific analysis (11 modules)
â”œâ”€â”€ race_projection.py    # Pre-race finish projection for the qualifying page
â”œâ”€â”€ prediction_accuracy.py # Projection-vs-result comparison metrics
â”œâ”€â”€ validation.py         # Session data quality audit checks
â”œâ”€â”€ requirements.txt      # Python dependencies
â”œâ”€â”€ render.yaml           # Render deployment blueprint
â”œâ”€â”€ .gitignore
â””â”€â”€ templates/
    â””â”€â”€ dashboard.html    # Full HTML/CSS/JS dashboard (single file, ~1400 lines)
```

---

## How It Works

```
User visits URL
       â”‚
       â–¼
   Flask receives GET /
       â”‚
       â–¼
   data_handler.py fetches session via FastF1
       â”‚
       â”œâ”€â”€ Detects session type
       â”‚
       â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚  Session Router (app.py)            â”‚
   â”‚                                     â”‚
   â”‚  Race/Sprint â”€â”€â–º anomaly.py         â”‚
   â”‚                  predictor.py       â”‚
   â”‚                  degradation.py     â”‚
   â”‚                  strategy.py        â”‚
   â”‚                  battle_detector.py â”‚
   â”‚                                     â”‚
   â”‚  Qualifying â”€â”€â”€â–º qualifying.py      â”‚
   â”‚                  (9 analysis modules)â”‚
   â”‚                                     â”‚
   â”‚  Practice â”€â”€â”€â”€â”€â–º practice.py        â”‚
   â”‚                  (11 analysis modules)â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â–¼
   Results injected into dashboard.html
       â”‚
       â–¼
   Rendered page returned to user
```

Each analysis module is wrapped in try/except â€” a failure in one never crashes the dashboard.

---

## Module Reference

### `app.py` â€” Application Entry Point
Routes requests to the correct analysis pipeline based on session type. Classifies sessions into three categories (race, qualifying, practice) and runs only the relevant modules. Serves three endpoints: `/` (dashboard), `/api/data` (JSON), `/health` (Render health check). Includes cold-start warmup to avoid timeouts on initial loads.

### `data_handler.py` â€” Data Layer
All FastF1 communication. `get_latest_session_info()` scans the F1 calendar for the most recent completed session using FastF1's actual named session slots and UTC timestamps, so sprint weekends and timezone boundaries are handled correctly. `load_session()` downloads and caches lap data in memory. `build_leaderboard()` uses finishing positions for races and fastest lap for qualifying/practice, normalizes official race-result gaps so direct gap-to-winner values are not misread as full elapsed race times, and prefers final classified lap times for same-lap finishers while falling back to lap-deficit labels for lapped cars.

### `qualifying.py` â€” Qualifying Intelligence (9 modules)
| Module | Algorithm |
|--------|-----------|
| Sector Breakdown | Extracts S1/S2/S3 from each driver's fastest lap, computes delta to session-best sector, classifies as best/good/ok/slow |
| Elimination Tracker | Uses FastF1's qualifying-session split to read Q1/Q2/Q3 as separate phases, then computes knockout order and gap to cutoff from the real session segments |
| Close Calls | Computes exact Q1 and Q2 cutoff margins from the real split qualifying phases rather than the combined final classification |
| Teammate Battles | Groups drivers by team, compares best laps, computes gap in seconds and percentage |
| Team Pace | Ranks teams by best driver's time, shows intra-team gap |
| Theoretical Best | Combines each driver's personal best S1 + S2 + S3 from any lap, compares to actual best |
| Improvement | Tracks delta from first flying lap to best lap per driver |
| Track Evolution | Splits session into 4 time phases, shows fastest lap and average per phase |
| Tyre Strategy | Maps compound usage per driver with lap counts and best times per compound |

### `race_projection.py` â€” Qualifying-Page Pre-Race Projection
| Module | Algorithm |
|--------|-----------|
| Projected Race Finish | Aggregates available practice race-pace rankings, blends them with qualifying grid position, theoretical-best underperformance, session improvement, and tyre usage hints to estimate a projected finishing order for the race |

### `prediction_accuracy.py` â€” Projection Accuracy
| Module | Algorithm |
|--------|-----------|
| Prediction Accuracy | Compares projected and official ordered results driver-by-driver, reporting exact-match rate, mean absolute position error, top-3/top-10 overlap, and pole/winner hit rate using only shared drivers present in both lists |

### `validation.py` â€” Session Audit
| Module | Algorithm |
|--------|-----------|
| Session Data Quality | Runs pass/warn/fail sanity checks over leaderboard ordering, gap values, anomaly math, timing sort order, and accuracy-block integrity so suspicious values are surfaced instead of trusted silently |

### `practice.py` â€” Practice Intelligence (12 modules)
| Module | Algorithm |
|--------|-----------|
| Short Run Pace | Best lap per driver with top-3 average, consistency (std dev), compound |
| Long Run Pace | Stints of 5+ clean laps with pit-in/pit-out laps excluded, fuel-corrected average (0.06s/lap fuel effect), linear regression for degradation slope |
| Projected Qualifying Order | FP3-only projection that blends weighted FP1/FP2/FP3 short-run positions, gap-to-best, consistency, theoretical-best ranking, and sector ranking to estimate the likely qualifying order |
| Race Pace Prediction | Aggregates fuel-corrected long run data per driver from clean stints only to predict race-day pace order |
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
2. **New + â†’ Web Service** â†’ select `f1-dashboard`
3. Configure:

| Setting | Value |
|---------|-------|
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
| Plan | Free |

4. Add environment variable: `FASTF1_CACHE` = `/tmp/fastf1_cache` (optional; defaults to OS temp directory)
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
2. Start with **Projected Race Finish** for the pre-race outlook built from the full weekend context
3. Use **FP3 Projection Accuracy** to see how the practice-based qualifying forecast compared with the official qualifying result
4. Check the **Elimination Tracker** to see who got knocked out and where
5. Look at **Teammate Battles** to see which driver had the edge in each team
6. Check **Close Calls** for the most dramatic cutoff margins
7. Review **Theoretical Best** to see who had untapped pace
8. Look at **Track Evolution** to understand session conditions

### Scouting Practice Before the Race
1. Load the practice session (FP1/FP2/FP3)
2. In **FP3**, check **Projected Qualifying Order** for the final practice-based quali forecast
3. **Race Pace Prediction** gives you the expected race-day pecking order
4. **Tyre Deg Curves** shows which compound will struggle and which will last
5. **Long Run Pace** reveals who ran race simulations and how they compared
6. **Driver Programmes** shows who did the most running and on which tyres
7. **Compound Comparison** helps predict optimal race strategy

### Reviewing Race Accuracy
1. Open the finished race session
2. Check **Race Projection Accuracy** beneath the official classification
3. Use exact matches, average position error, and top-10 overlap together instead of relying on a single percentage

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No completed session found" | Off-season. Use manual selector (e.g. year=2024, round=24, Race) |
| First load is slow (30-60s) | Normal â€” FastF1 downloads data on first request, cached after |
| Cold start shows warming message | Expected â€” wait a few seconds and the page will auto-refresh |
| Degradation shows N/A | Stint too short (<4 clean laps). Common early in a race |
| Strategy shows no data | Requires cumulative race time â€” only available in Race sessions |
| Practice panels empty | Ensure you selected the correct session type (Practice 1/2/3) |
| No sector data | Some older sessions lack sector timing in FastF1 |
| Render build failed | Check build logs â€” usually a pip dependency issue |

---

## Accuracy Notes

- Latest-session auto-detection reads the actual FastF1 schedule slots (`Session1` to `Session5`) with UTC timestamps instead of assuming every weekend follows the same practice/qualifying/sprint ordering.
- Qualifying elimination and close-call panels use FastF1's split-session support for Q1, Q2, and Q3 when timing status data is available, which is more accurate than inferring knockout order from the combined final lap table.
- Practice long-run and race-pace calculations exclude pit-in and pit-out laps from stint construction so in-laps and out-laps do not skew race-simulation pace.
- A session-level data-quality audit now highlights suspicious leaderboard gaps, ordering issues, and invalid derived metrics instead of silently treating them as trustworthy.
- The qualifying-page projected race finish is a pre-race forecast, not a simulation of the actual race. It is strongest when practice long-run data is available and falls back to lower-confidence qualifying-led signals when it is not.

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
| Session-specific analysis routing | Race/qualifying/practice have fundamentally different data patterns â€” running pit strategy analysis on a qualifying session is meaningless |
| Module-level try/except isolation | One module failing never crashes the dashboard â€” graceful degradation |
| In-memory session cache | Repeat requests within the same Render wake cycle are instant |
| Linear regression over ML | Simple math runs fast on Render's constrained CPU; no model training needed |
| Qualifying page hosts the pre-race forecast | Qualifying is the final pre-race checkpoint, so it is the natural place to present a weekend-wide projected finishing order |
| Single HTML file | No build tools, no CDN dependencies, zero frontend complexity |
| Fuel correction in practice | Long run times are misleading without accounting for ~0.06s/lap fuel burn-off |

---

## Portfolio Description

> **F1 Strategy Intelligence Dashboard** â€” A full-stack Formula 1 analytics platform built with Python and Flask. Delivers session-specific intelligence across races, qualifying, and practice with 28+ analysis modules including tire degradation modeling via linear regression, pit strategy simulation, on-track battle detection, qualifying elimination tracking with close-call analysis, theoretical best lap computation, projected race finish forecasting from qualifying plus pre-race weekend context, projected qualifying order from weighted FP1/FP2/FP3 practice data, projection accuracy benchmarking against official results, race pace prediction from fuel-corrected long run data, and tyre degradation curves per compound. Features a weekend navigation system for seamless session switching and a dark, responsive F1-themed interface. Deployed on Render's free tier using FastF1's public timing API with zero infrastructure cost.

