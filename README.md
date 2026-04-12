# F1 Strategy Intelligence Dashboard

A comprehensive, free Formula 1 analytics platform that delivers session-specific intelligence for **races**, **qualifying**, **sprint qualifying**, and **practice sessions**. Fetches real timing data via FastF1 and transforms it into actionable insights - whether you're watching live or catching up on a session you missed.

**Architecture:** On-demand, zero-cost. The app sleeps on Render's free tier and wakes on request. No always-running server, no paid services, no database.

Built and maintained by **Andrew**.

## Project Links

- **Author:** Andrew
- **GitHub:** [Andrew-3y](https://github.com/Andrew-3y)
- **Repository:** [Andrew-3y/f1-dashboard](https://github.com/Andrew-3y/f1-dashboard)
- **Live Demo:** [f1-dashboard-l1yl.onrender.com](https://f1-dashboard-l1yl.onrender.com)
- **License:** [MIT](./LICENSE)

---

## Features

### Race & Sprint Analysis
- **Live leaderboard** with finishing positions, best laps, and gaps
- **Tire degradation modeling** - linear regression on clean stint laps, classified IMPROVING / STABLE / MODERATE / HIGH / CRITICAL
- **Pit window prediction** - estimates when cumulative tire loss exceeds pit stop cost (OPEN / APPROACHING / CLOSED)
- **Pit strategy simulator** - simulates "what if this driver pits now?", predicts rejoin position, clearer place-loss wording, traffic risk, and undercut feasibility (PIT NOW / CONSIDER PIT / STAY OUT / HOLD)
- **Battle detection** - identifies drivers within 2s, tracks closing rates, classifies intensity (INTENSE / CLOSE / WATCHING) with DRS flags
- **Overtake predictions** - gap-closing rate analysis with estimated laps to DRS range
- **Anomaly detection** - rolling-average pace analysis, severity levels CRITICAL / HIGH / MEDIUM / LOW
- **Cleaner anomaly filtering** - pace anomalies now ignore pit laps and non-green/neutralized track-status laps more aggressively, reducing false alerts from safety-car, VSC, and caution periods

### Qualifying Analysis
- **Full results with sector breakdown** - S1, S2, S3 times color-coded (purple = best in session, green = within 0.1s, yellow = within 0.3s, orange = off pace)
- **Tyre compound per driver** on their best lap
- **Projected race finish** - qualifying-page pre-race forecast that blends grid position with available practice long-run pace and qualifying-form signals
- **Projection accuracy tracking** - compares FP3 qualifying projections with actual qualifying and pre-race finish projections with the official race result using exact-match rate, average position error, and top-10 overlap
- **Corrected accuracy movement wording** - projection accuracy cards now describe whether a driver finished higher or lower than predicted using the actual official finishing position direction
- **Q1 / Q2 / Q3 elimination tracker** - who got knocked out, gap to the cutoff line
- **Close calls** - exact margin between last-safe and first-eliminated at each cutoff
- **Teammate head-to-head** - who beat who within each team, gap in seconds and percentage
- **Team qualifying pace ranking** - teams ordered by best driver, intra-team gap shown
- **Theoretical best lap** - best S1 + best S2 + best S3 per driver, showing time left on the table vs. actual best
- **Lap improvement progression** - how much each driver improved from first flying lap to best lap, with attempt count
- **Track evolution** - how the circuit got faster through the session in 4 phases (Early / Mid-Early / Mid-Late / Late)
- **Tyre strategy breakdown** - compounds used per driver, lap counts per compound, best time on each

### Sprint Qualifying Analysis
- **Dedicated Sprint Shootout page** - sprint weekends now have their own qualifying-style page instead of jumping straight from FP1 to the sprint race
- **Full sprint shootout classification** - official SQ result with sector breakdown and compound on the best lap
- **SQ1 / SQ2 / SQ3 elimination tracking** - same qualifying detail, but labeled correctly for sprint weekends
- **Projected sprint finish** - sprint-specific forecast weighted more heavily toward shootout position, with available practice pace as supporting context
- **Sprint projection accuracy** - once the sprint is complete, the Sprint page compares the sprint-shootout projection against the official sprint result

### Practice Session Analysis
- **Single-lap pace ranking** - qualifying simulation with best lap, top-3 average from representative short-run laps, compound, consistency (std dev), and gap to fastest
- **Long run pace** - sustained stints (5+ laps) ranked by fuel-corrected average, with first-to-last-lap trend and degradation rate
- **Projected qualifying order (FP3)** - final pre-quali forecast using weighted FP1/FP2/FP3 short-run pace, theoretical best, and sector strength, while filtering to the latest active practice field so reserve-driver sessions do not inflate counts
- **Race pace prediction** - estimated race-day order from fuel-corrected long run aggregation
- **Compound comparison** - best/avg/median pace per tyre type, driver and lap counts
- **Tyre degradation curves** - per-compound degradation slope aggregated across stints (STABLE / LOW / MODERATE / HIGH)
- **Team pace ranking** - teams ordered by best representative time, both drivers shown
- **Driver consistency** - std-dev ranking classified EXCELLENT / GOOD / AVERAGE / INCONSISTENT
- **Theoretical best lap** - best S1 + S2 + S3 combined, time left on the table
- **Sector analysis** - best sector times per driver, color-coded by delta to overall session best
- **Track evolution** - 5-phase breakdown (Opening / Early / Middle / Late / Final) showing how the track rubbered in
- **Driver programme summary** - total laps, stint count, long-run vs short-run split, time on track, compounds used

### Season Form Analysis
- **Season Form Tracker page** - dedicated `/season` view for cross-weekend momentum analysis
- **Driver form ranking** - ranks drivers over the last N completed rounds using recent qualifying, race finishing position, points, and positions gained
- **Team form ranking** - aggregates both cars to show which teams are trending strongest across recent rounds
- **Season teammate head-to-head** - compares qualifying and race results between teammates across the selected recent window
- **Momentum summary cards** - highlights the hottest driver, hottest team, best recent average qualifier, best average position gain, and biggest average position loss over the selected round window
- **Season-page background warmup** - first loads now return a friendly loading state while recent qualifying and race results are warmed in the background, reducing Render timeouts on `/season`
- **Season-page polish pass** - teammate battle summaries now handle tied scorelines cleanly, fallback states use cleaner ASCII separators, and the route safely returns an empty season view if upstream round loads fail instead of erroring

### Circuit Intelligence
- **Circuit Intelligence page** - dedicated `/circuit` view for pre-weekend track context
- **Curated track profile** - lap length, corner count, DRS zones, track style, and what matters most at that venue
- **Circuit difficulty summary** - overtaking, tyre stress, degradation risk, qualifying importance, and strategy bias
- **Recent history table** - recent winners and pole sitters for the same Grand Prix
- **Circuit-page background warmup** - first loads warm recent race and qualifying history in the background to reduce Render timeouts on `/circuit`
- **Beginner-friendlier circuit wording** - circuit notes now avoid more internal or niche phrasing where possible, with clearer labels like `Degradation Risk`, `Strategy Style`, and plainer descriptions of pit-stop timing and high-speed S-curves

### Driver Intelligence
- **Driver Intelligence page** - dedicated `/driver` view for a single-driver season read
- **Driver selector with recent-window filter** - choose the driver and last-N-round window directly on the page
- **Grid comparison** - shows where the selected driver ranks across recent form, qualifying, race finishes, positions gained, points, and consistency
- **Teammate context block** - compares recent qualifying and race record versus the most relevant teammate in the selected window
- **Round-by-round results** - recent event-by-event qualifying result, race result, grid spot, position change, points, and classification status
- **Driver-read insights** - plain-language strengths and watchouts built from recent trend, qualifying pace, race execution, consistency, and teammate context
- **Driver-page background warmup** - first loads return a friendly loading state while recent qualifying and race results are warmed in the background

### Weekend Outlook
- **Weekend Outlook page** - dedicated `/outlook` view that combines circuit context with recent season form for one selected round
- **Upcoming-weekend default** - when no round is specified, the page tries to open the next current/upcoming Grand Prix instead of only the latest completed round
- **Weekend briefing cards** - highlights the likely favorite driver, team to beat, passing outlook, tyre pressure, and expected strategy shape
- **Main storylines** - turns season and circuit context into a short pre-weekend briefing instead of another raw stats table
- **Drivers and teams to watch** - spotlights the strongest recent performers with one-line reasons for why they matter this weekend
- **Midfield picture** - summarizes the current battle outside the front-running teams
- **Teammate focus** - surfaces one recent intra-team battle worth watching for the selected weekend
- **Weekend-outlook background warmup** - first loads return a friendly loading state while circuit and recent-form context are warmed in the background

### Navigation & UI
- **Weekend navigation bar** - one-click switching between FP1, FP2, FP3, Qualifying, Sprint Qualifying, Sprint, and Race for the current round
- **Session selector** - manual year/round/session picker for historical data (supports all session types, including Sprint Shootout)
- **Season Form Tracker shortcut** - direct button from the main dashboard to the dedicated season page
- **Circuit Intelligence shortcut** - direct button from the main dashboard to the dedicated circuit page for the selected round
- **Driver Intelligence shortcut** - direct button from the main dashboard, plus cross-links from the season and circuit pages
- **Weekend Outlook shortcut** - direct button from the main dashboard, plus cross-links from the season, circuit, and driver pages
- **Auto-refresh** - configurable: OFF / Live (30s) / Session (60s) / Casual (5min), persists across reloads
- **F1-style lap-time formatting** - lap and pace times are shown as `M:SS.mmm` instead of raw seconds
- **Ordered projection inputs** - projection cards list sessions in weekend order (FP1 -> FP2 -> FP3, then Qualifying where applicable)
- **Readable projection explanations** - projection cards show plain-language reasons and clearer driver labels instead of raw shorthand where possible
- **Terminology polish pass** - season, circuit, driver, and race-strategy labels now use clearer user-facing wording so cards and tables read more consistently at a glance
- **Session-aware wording** - sprint-specific panels and validation checks now say "Sprint" where appropriate instead of reusing race labels
- **Clearer warmup/error copy** - warmup and fallback states now use cleaner user-facing wording instead of exposing internal-looking debug phrasing
- **Background warmup for manual round loads** - explicit year/round/session searches warm in the background first so cold session fetches are less likely to fail on first load
- **Session data-quality audit** - every session now shows pass/warn/fail validation checks over official tables and derived modules
  Main-race projection accuracy is treated as race-only, so sprint sessions no longer show a misleading warning for that check.
- **Mobile-friendly layout** - responsive tweaks on small screens without changing desktop layout
- **Dark F1-themed interface** - color-coded compound badges, sector classifications, strategy tags, intensity indicators

---

## Project Structure

```text
f1-dashboard/
|-- app.py                 # Flask server - routes requests to session-specific and season-level analysis
|-- data_handler.py        # FastF1 integration, caching, leaderboard building
|-- anomaly.py             # Lap-time anomaly detection (rolling average)
|-- predictor.py           # Overtake prediction (gap-closing rate analysis)
|-- degradation.py         # Tire degradation modeling + pit window prediction
|-- strategy.py            # Pit strategy simulator (undercut/overcut/traffic)
|-- battle_detector.py     # On-track battle detection
|-- qualifying.py          # Qualifying-specific analysis (9 modules)
|-- practice.py            # Practice-specific analysis (11 modules)
|-- race_projection.py     # Pre-race finish projection for the qualifying page
|-- prediction_accuracy.py # Projection-vs-result comparison metrics
|-- validation.py          # Session data quality audit checks
|-- season_form.py         # Season-level momentum and teammate trend analysis
|-- circuit_intel.py       # Circuit profiles and recent event history
|-- driver_intel.py        # Driver-focused season profile and grid ranking view
|-- weekend_outlook.py     # Weekly briefing page combining track context and recent form
|-- LICENSE                # MIT license for reuse and attribution
|-- requirements.txt       # Python dependencies
|-- render.yaml            # Render deployment blueprint
|-- .gitignore
|-- docs/
|   `-- screenshots/       # Screenshot assets for future README/demo updates
`-- templates/
    |-- dashboard.html     # Full HTML/CSS/JS dashboard
    |-- season.html        # Dedicated season form tracker page
    |-- circuit.html       # Dedicated circuit intelligence page
    |-- driver.html        # Dedicated driver intelligence page
    `-- outlook.html       # Dedicated weekend outlook page
```

---

## How It Works

```text
User visits URL
       |
       v
   Flask receives GET /
       |
       v
   data_handler.py fetches session via FastF1
       |
       |-- Detects session type
       |
       v
   +-------------------------------------+
   |  Session Router (app.py)            |
   |                                     |
   |  Race/Sprint -> anomaly.py          |
   |                  predictor.py       |
   |                  degradation.py     |
   |                  strategy.py        |
   |                  battle_detector.py |
   |                                     |
   |  Qualifying -> qualifying.py        |
   |                 (9 analysis modules)|
   |                                     |
   |  Practice ----> practice.py         |
   |                 (11 analysis modules)|
   |                                     |
   |  Season ------> season_form.py      |
   |                                     |
   |  Circuit -----> circuit_intel.py    |
   |                                     |
   |  Driver ------> driver_intel.py     |
   |                                     |
   |  Outlook -----> weekend_outlook.py  |
   +-------------------------------------+
       |
       v
   Results injected into dashboard.html / season.html / circuit.html / driver.html / outlook.html
       |
       v
   Rendered page returned to user
```

Each analysis module is wrapped in try/except - a failure in one never crashes the dashboard.

---

## Module Reference

### `app.py` - Application Entry Point
Routes requests to the correct analysis pipeline based on session type. Classifies sessions into three categories (race, qualifying, practice) and runs only the relevant modules. Serves seven endpoints: `/` (dashboard), `/season` (season form tracker), `/circuit` (circuit intelligence), `/driver` (driver intelligence), `/outlook` (weekend outlook), `/api/data` (JSON), `/health` (Render health check). Includes cold-start warmup to avoid timeouts on initial loads.

### `data_handler.py` - Data Layer
All FastF1 communication. `get_latest_session_info()` scans the F1 calendar for the most recent completed session using FastF1's actual named session slots and UTC timestamps, so sprint weekends and timezone boundaries are handled correctly. `load_session()` downloads and caches lap data in memory. `build_leaderboard()` uses finishing positions for races and fastest lap for qualifying/practice, normalizes official race-result gaps so direct gap-to-winner values are not misread as full elapsed race times, and prefers final classified lap times for same-lap finishers while falling back to lap-deficit labels for lapped cars.

### `qualifying.py` - Qualifying Intelligence (9 modules)
| Module | Algorithm |
|--------|-----------|
| Sector Breakdown | Extracts S1/S2/S3 from each driver's fastest lap, computes delta to session-best sector, classifies as best/good/ok/slow |
| Elimination Tracker | Uses FastF1's qualifying-session split to read Q1/Q2/Q3 as separate phases, then computes knockout order and gap to cutoff from the real session segments |
| Close Calls | Computes exact Q1 and Q2 cutoff margins from the real split qualifying phases rather than the combined final classification |
| Teammate Battles | Groups drivers by team, compares best laps, computes gap in seconds and percentage |
| Team Pace | Ranks teams by best driver's time, shows intra-team gap |
| Theoretical Best | Combines each driver's personal best S1 + S2 + S3 from any lap, compares to actual best |
| Improvement | Tracks delta from each driver's first competitive lap to best lap so setup or traffic-affected early laps do not exaggerate the gain |
| Track Evolution | Splits session into 4 time phases, shows fastest lap and average per phase |
| Tyre Strategy | Maps compound usage per driver with lap counts and best times per compound |

### `race_projection.py` - Qualifying-Page Pre-Race Projection
| Module | Algorithm |
|--------|-----------|
| Projected Race Finish | Aggregates available practice race-pace rankings, blends them with qualifying grid position, theoretical-best underperformance, session improvement, and tyre usage hints to estimate a projected finishing order for the race |
| Projected Sprint Finish | Uses sprint-shootout position as the strongest signal, then blends in available practice pace and qualifying-form clues to estimate the sprint finishing order |

### `prediction_accuracy.py` - Projection Accuracy
| Module | Algorithm |
|--------|-----------|
| Prediction Accuracy | Compares projected and official ordered results driver-by-driver, reporting exact-match rate, mean absolute position error, top-3/top-10 overlap, and pole/winner hit rate using only shared drivers present in both lists, while showing whether each driver finished higher or lower than predicted |

### `validation.py` - Session Audit
| Module | Algorithm |
|--------|-----------|
| Session Data Quality | Runs context-aware pass/warn/fail sanity checks over leaderboard ordering, gap values, anomaly math, and accuracy-block integrity so suspicious values are surfaced instead of trusted silently without flagging expected session-specific behavior |

### `practice.py` - Practice Intelligence (12 modules)
| Module | Algorithm |
|--------|-----------|
| Short Run Pace | Best lap per driver with top-3 average and consistency calculated from representative short-run laps near each driver's quickest effort, plus compound |
| Long Run Pace | Stints of 5+ clean laps with pit-in/pit-out laps excluded, fuel-corrected average (0.06s/lap fuel effect), linear regression for degradation slope |
| Projected Qualifying Order | FP3-only projection that blends weighted FP1/FP2/FP3 short-run positions, gap-to-best, consistency, theoretical-best ranking, and sector ranking to estimate the likely qualifying order, limited to the latest active practice field so earlier reserve-driver appearances do not overcount the grid |
| Race Pace Prediction | Aggregates fuel-corrected long run data per driver from clean stints only to predict race-day pace order |
| Compound Comparison | Best/avg/median pace per tyre type across all drivers |
| Tyre Deg Curves | Per-compound degradation slope aggregated across multiple stints, classified STABLE/LOW/MODERATE/HIGH |
| Team Ranking | Teams ranked by best driver's time, both drivers shown |
| Consistency | Std-dev of cleaned lap times, rated EXCELLENT (<0.3s) / GOOD (<0.5s) / AVERAGE (<0.8s) / INCONSISTENT |
| Theoretical Best | Best S1 + S2 + S3 combined per driver |
| Sector Analysis | Best sector times per driver with delta classification |
| Track Evolution | 5-phase session breakdown showing rubber build-up progression |
| Driver Programmes | Total laps, stint count, long-run vs short-run split, time on track, compounds used |

### `season_form.py` - Season Form Tracker
| Module | Algorithm |
|--------|-----------|
| Driver Form | Aggregates official qualifying and race results across completed rounds, then blends race finish, qualifying position, points, and positions gained into a recent-form index |
| Team Form | Combines both drivers' recent qualifying and race results to rank which teams are strongest over the selected round window |
| Teammate Head-to-Head | Tracks qualifying and race wins between teammates across the selected recent rounds |

### `circuit_intel.py` - Circuit Intelligence
| Module | Algorithm |
|--------|-----------|
| Circuit Profile | Matches the selected Grand Prix to a curated circuit profile describing overtaking, tyre stress, degradation, qualifying importance, and strategy bias |
| Recent History | Loads recent official race and qualifying results for the same event to show winners and pole sitters from prior visits |
| Track Pattern Summary | Converts the profile plus recent history into plain-language notes about what usually matters at that venue |

### `driver_intel.py` - Driver Intelligence
| Module | Algorithm |
|--------|-----------|
| Driver Snapshot | Aggregates recent official qualifying and race results for one selected driver, using the same form-scoring blend as the season page |
| Grid Comparison | Ranks the selected driver against the active recent sample across form, average qualifying, average finish, average position change, points, and consistency |
| Teammate Context | Compares the selected driver's recent qualifying and race record against the most relevant teammate in the same selected window |
| Driver Read | Turns the recent metrics into plain-language strengths and watchouts so the page reads like a profile, not just a table |

### `weekend_outlook.py` - Weekend Outlook
| Module | Algorithm |
|--------|-----------|
| Weekend Summary | Combines circuit-intelligence output with season-form output for one selected round to summarize favorite driver, team to beat, passing outlook, tyre pressure, and strategy shape |
| Main Storylines | Converts track profile, recent patterns, driver form, team form, and teammate battles into a short weekend briefing |
| Watchlists | Selects the leading drivers and teams to watch based on recent form, trend, and supporting context |
| Midfield and Garage Focus | Highlights the current midfield battle and one close teammate fight to watch going into the weekend |

### Race Analysis Modules
| Module | File | Algorithm |
|--------|------|-----------|
| Anomaly Detection | `anomaly.py` | 5-lap rolling average, flags laps >1s slower, and suppresses same-lap field-wide slowdowns so safety-car or neutralized periods are less likely to appear as personal anomalies. Severity: CRITICAL (>3s) / HIGH (>2s) / MEDIUM (>1.5s) / LOW (>1s) |
| Overtake Prediction | `predictor.py` | Gap-closing rate over last 8 laps, estimates laps to DRS range (<1s) |
| Tire Degradation | `degradation.py` | Linear regression on clean stint laps (excludes pits, outliers >3s from median). Pit window = cumulative loss vs. ~23s pit cost |
| Pit Strategy | `strategy.py` | Simulates pit stop: adds 23s, recalculates rejoin position, checks pit-timing edge potential (1.5s/lap fresh-tyre gain over 3 laps) and traffic risk (within 2s) |
| Battle Detection | `battle_detector.py` | Scans consecutive classified pairs using same-lap cumulative times only, ignores mismatched or negative-gap rows, and calculates closing rate from the last 5 shared laps. INTENSE (<1s) / CLOSE (<1.5s) / WATCHING (<2s) |

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
```text
http://localhost:5000/?year=2024&round=24&session_type=Race
http://localhost:5000/?year=2024&round=24&session_type=Qualifying
http://localhost:5000/?year=2024&round=24&session_type=Practice+1
http://localhost:5000/?year=2024&round=21&session_type=Sprint+Shootout
http://localhost:5000/season?year=2025&window=5
http://localhost:5000/circuit?year=2025&round=14
http://localhost:5000/driver?year=2025&window=5&driver=VER
http://localhost:5000/outlook?year=2025&round=14&window=5
```

Or use the **Weekend Navigation Bar** to switch between sessions with one click, open the **Season Form Tracker** for recent multi-round trends, use **Circuit Intelligence** for pre-weekend track context, open **Driver Intelligence** for a one-driver season read, or open **Weekend Outlook** for the full weekly briefing.

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
2. **New + -> Web Service** -> select `f1-dashboard`
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

### Sprint Weekend Workflow
1. Open the **Sprint Shootout** session on a sprint weekend
2. Start with **Projected Sprint Finish** for the pre-sprint outlook
3. Use the **Elimination Tracker** and **Close Calls** for SQ1/SQ2/SQ3 context
4. After the sprint is complete, open the **Sprint** page and check **Sprint Projection Accuracy**

### Scouting Practice Before the Race
1. Load the practice session (FP1/FP2/FP3)
2. In **FP3**, check **Projected Qualifying Order** for the final practice-based quali forecast
3. **Race Pace Prediction** gives you the expected race-day pecking order
4. **Tyre Deg Curves** shows which compound will struggle and which will last
5. **Long Run Pace** reveals who ran race simulations and how they compared
6. **Driver Programmes** shows who did the most running and on which tyres
7. **Compound Comparison** helps predict optimal race strategy

### Tracking Season Momentum
1. Open the **Season Form Tracker** from the dashboard or visit `/season`
2. Choose a year and recent-round window
3. If the page is cold, let the warmup screen refresh automatically once the season data is ready
4. Use **Driver Form** to see who is trending strongest
5. Use **Team Form** to understand which teams are improving or fading
6. Check **Teammate Head-to-Head** for the recent intra-team battle picture

### Using Circuit Intelligence
1. Open **Circuit Intel** from the dashboard or visit `/circuit`
2. Choose the season and round you want to preview
3. Use the summary cards to understand overtaking, tyre stress, degradation risk, qualifying importance, and strategy bias
4. Read the **What Matters Here** notes to get the venue's race-shaping themes
5. Check the recent history table for recent winners and pole sitters at the same event

### Reading a Driver Profile
1. Open **Driver Intel** from the dashboard or visit `/driver`
2. Choose the season, recent-round window, and driver you want to inspect
3. Start with the summary cards for form rank, trend, average qualifying, average finish, average position change, and window points
4. Use **Grid Comparison** to see how that driver compares with the rest of the recent field
5. Check **Teammate Context** to understand whether the driver is winning the intra-team fight
6. Use **Round-by-Round Results** to see where the numbers are coming from event by event

### Reading the Weekend Outlook
1. Open **Weekend Outlook** from the dashboard or visit `/outlook`
2. Choose the season, round, and recent-round window you want to use for the briefing
3. Start with the summary cards to see the likely favorite driver, team to beat, passing outlook, tyre pressure, and strategy shape
4. Read **Main Storylines** first to understand what is shaping the weekend
5. Use **Drivers to Watch** and **Teams to Watch** to see who is coming in with the strongest recent form
6. Check **Midfield Picture** and **Teammate Focus** for the likely side battles around the main story

### Reviewing Race Accuracy
1. Open the finished race session
2. Check **Race Projection Accuracy** beneath the official classification
3. Use exact matches, average position error, and top-10 overlap together instead of relying on a single percentage

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No completed session found" | Off-season. Use manual selector (e.g. year=2024, round=24, Race) |
| First load is slow (30-60s) | Normal - FastF1 downloads data on first request, cached after |
| Cold start shows warming message | Expected - wait a few seconds and the page will auto-refresh |
| Raw browser "Internal Server Error" page | Latest builds are wired to fall back to the dashboard error screen, and if that template render also fails the app now returns a minimal branded error page instead of the raw browser 500 screen |
| Degradation shows N/A | Stint too short (<4 clean laps). Common early in a race |
| Strategy shows no data | Requires cumulative race time - only available in Race sessions |
| Practice panels empty | Ensure you selected the correct session type (Practice 1/2/3) |
| No sector data | Some older sessions lack sector timing in FastF1 |
| Render build failed | Check build logs - usually a pip dependency issue |

---

## Accuracy Notes

- Latest-session auto-detection reads the actual FastF1 schedule slots (`Session1` to `Session5`) with UTC timestamps instead of assuming every weekend follows the same practice/qualifying/sprint ordering.
- Qualifying elimination and close-call panels use FastF1's split-session support for Q1, Q2, and Q3 when timing status data is available, which is more accurate than inferring knockout order from the combined final lap table.
- Practice long-run and race-pace calculations exclude pit-in and pit-out laps from stint construction so in-laps and out-laps do not skew race-simulation pace.
- A session-level data-quality audit now highlights suspicious leaderboard gaps, ordering issues, and invalid derived metrics instead of silently treating them as trustworthy.
- The qualifying-page projected race finish is a pre-race forecast, not a simulation of the actual race. It is strongest when practice long-run data is available and falls back to lower-confidence qualifying-led signals when it is not.
- The season form page is a momentum view built from recent official results, not an official championship standings replacement.
- The season form route only loads the recent rounds needed for trend analysis and warms heavy requests in the background so the page is more reliable on Render's free tier.
- The circuit intelligence page combines curated track characteristics with recent official event history, so it is a context tool rather than a live performance model.
- The driver intelligence page reuses the season page's recent official qualifying and race results, but reorganizes them around one driver to make strengths, weaknesses, and teammate context easier to read.
- The weekend outlook page is a synthesis layer that combines recent form with circuit context. It is meant as a briefing page, not as a full race simulation or betting model.

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
| Session-specific analysis routing | Race/qualifying/practice have fundamentally different data patterns - running pit strategy analysis on a qualifying session is meaningless |
| Module-level try/except isolation | One module failing never crashes the dashboard - graceful degradation |
| In-memory session cache | Repeat requests within the same Render wake cycle are instant |
| Linear regression over ML | Simple math runs fast on Render's constrained CPU; no model training needed |
| Qualifying page hosts the pre-race forecast | Qualifying is the final pre-race checkpoint, so it is the natural place to present a weekend-wide projected finishing order |
| Single HTML file | No build tools, no CDN dependencies, zero frontend complexity |
| Fuel correction in practice | Long run times are misleading without accounting for ~0.06s/lap fuel burn-off |
| Separate season route | Season-level analysis adds a new product layer without overcrowding the live session dashboard |
| Separate circuit route | Track intelligence serves a different pre-weekend use case than the session or season pages, so it benefits from its own dedicated screen |
| Separate driver route | A driver-first profile is more useful as its own page than as another table inside the season overview |
| Separate outlook route | A weekly briefing works best as a synthesis layer that can pull from season and circuit context without overloading either page |

---

## Roadmap

- Team intelligence page
- Deeper comparison tools for drivers and constructors
- Screenshot gallery for the README and project showcase
- Optional changelog file if release cadence becomes more formal

---

## Recent Changelog

- Added Season Form Tracker
- Added Circuit Intelligence page
- Added Driver Intelligence page
- Added Weekend Outlook page
- Improved warmup handling on Render
- Improved wording consistency and feature-level polish across intelligence pages

---

## Portfolio Description

> **F1 Strategy Intelligence Dashboard** - A full-stack Formula 1 analytics platform built with Python and Flask. It delivers session-specific intelligence across races, qualifying, and practice with 28+ analysis modules, including tire degradation modeling via linear regression, pit strategy simulation, on-track battle detection, qualifying elimination tracking with close-call analysis, theoretical best lap computation, projected race-finish forecasting from qualifying plus pre-race weekend context, projected qualifying order from weighted FP1/FP2/FP3 practice data, projection accuracy benchmarking against official results, race-pace prediction from fuel-corrected long-run data, tyre degradation curves per compound, a dedicated season form tracker for recent driver and team momentum, a circuit intelligence page for pre-weekend track context, a driver intelligence page for one-driver profile analysis versus the recent grid, and a weekend outlook page that turns track context plus recent form into a pre-weekend briefing. The project includes seamless weekend navigation and a dark, responsive F1-themed interface, and it is deployed on Render's free tier using FastF1's public timing API with zero infrastructure cost.
