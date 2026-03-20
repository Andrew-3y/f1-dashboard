# F1 Strategy Intelligence Dashboard

A free, on-demand Formula 1 strategy analytics platform that fetches real timing data and delivers a full race intelligence suite — leaderboard, tire degradation modeling, pit strategy simulation, battle detection, overtake predictions, and anomaly alerts — all powered by Python, FastF1, and Flask.

**Live demo architecture:** The app sleeps on Render's free tier and wakes up when someone visits the URL. No always-running server, no paid services, no databases.

---

## Features

- **Auto-detecting leaderboard** — Finds the latest completed F1 session and displays driver positions, best laps, and gaps (race finishing order for Race sessions, fastest lap for Qualifying)
- **Tire degradation modeling** — Calculates each driver's degradation rate (seconds lost per lap) using linear regression on clean stint laps. Classifies pace as IMPROVING / STABLE / MODERATE / HIGH / CRITICAL
- **Pit window prediction** — Estimates when cumulative tire time loss approaches pit stop cost and flags windows as OPEN / APPROACHING / CLOSED
- **Pit strategy simulator** — Simulates "what if this driver pits now?", predicts rejoin position, identifies traffic risk, and evaluates undercut potential vs. the car ahead. Outputs PIT NOW / CONSIDER PIT / STAY OUT / HOLD recommendations
- **Battle detection** — Scans the grid for drivers within 2 seconds of each other, tracks whether the gap is shrinking, and classifies battles as INTENSE / CLOSE / WATCHING with DRS-active flags
- **Overtake predictions** — Calculates gap-closing rates between consecutive drivers and estimates laps until DRS range
- **Anomaly detection** — Flags sudden pace drops using rolling-average analysis with severity levels: CRITICAL / HIGH / MEDIUM / LOW
- **Manual session selector** — Pick any year/round/session type to explore historical data
- **Auto-refresh** — Configurable refresh: OFF / Live 30s / Session 60s / Casual 5min
- **Dark F1-themed UI** — Organized into sections with color-coded compound badges, strategy tags, and intensity indicators

---

## Project Structure

```
f1-dashboard/
├── app.py              # Flask web server — routes, orchestrates all modules
├── data_handler.py     # FastF1 data fetching, caching, leaderboard building
├── anomaly.py          # Lap-time anomaly detection (rolling average)
├── predictor.py        # Overtake prediction (gap-closing rate analysis)
├── degradation.py      # Tire degradation modeling + pit window prediction
├── strategy.py         # Pit strategy simulator (undercut/overcut/traffic)
├── battle_detector.py  # On-track battle detection system
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment blueprint
├── .gitignore          # Files to exclude from Git
└── templates/
    └── dashboard.html  # Full HTML/CSS/JS dashboard (single file)
```

---

## File-by-File Explanation

### `app.py` — The Main Application
Creates a Flask web server with three routes: `/` (main dashboard), `/api/data` (JSON endpoint), and `/health` (Render health check). On each request it calls `data_handler` to fetch session data, then runs all five analysis modules through a shared `_run_full_analysis()` helper. Each module is wrapped in try/except so a failure in one never crashes the whole dashboard.

### `data_handler.py` — Data Fetching & Processing
All FastF1 communication lives here. `get_latest_session_info()` scans the F1 calendar to find the most recent completed session. `load_session()` downloads lap data with in-memory caching (fast on repeat visits). `build_leaderboard()` uses actual finishing positions for Race sessions and fastest lap for Qualifying.

### `anomaly.py` — Anomaly Detection
Computes a 5-lap rolling average per driver, filters out pit laps, and flags any lap more than 1 second slower than average. Severity: CRITICAL (>3s), HIGH (>2s), MEDIUM (>1.5s), LOW (>1s).

### `predictor.py` — Overtake Predictions
For each consecutive driver pair, calculates the average closing rate over recent laps (how much time the chaser gains per lap). Predicts laps until DRS range (< 1 second gap).

### `degradation.py` — Tire Degradation + Pit Window
Identifies each driver's current stint, filters outlier laps (safety car, traffic, mistakes) using median deviation, then fits a linear regression to find the degradation slope (seconds lost per additional lap on the tyre). Classifies the trend and estimates when the pit window opens based on cumulative time loss vs. pit stop cost (~23s).

### `strategy.py` — Pit Strategy Simulator
Builds a snapshot of the current race state (positions + cumulative times), then simulates each driver pitting by adding pit loss to their time and recalculating where they'd rejoin. Checks for undercut feasibility (fresh tyre pace advantage vs. current gap) and traffic risk (emerging within 2s of another car).

### `battle_detector.py` — Battle Detection
Scans consecutive driver pairs for gaps under 2 seconds, calculates closing rate from recent laps, and classifies intensity. INTENSE = within 1s or closing fast, CLOSE = within 1.5s, WATCHING = within 2s but stable.

### `templates/dashboard.html` — The UI
Single-file dashboard with embedded CSS and JavaScript. Organized into four sections: Stats Row → Leaderboard → Strategy Intelligence (Degradation + Pit Strategy) → Race Action (Battles + Overtake Predictions) → Anomaly Detection. Color-coded tyre compound badges (red/yellow/white for Soft/Medium/Hard), strategy recommendation tags, and pit window indicators.

---

## Local Setup

### Prerequisites
- Python 3.9+ — [download](https://www.python.org/downloads/)
- Git — [download](https://git-scm.com/downloads)
- VS Code (recommended) — [download](https://code.visualstudio.com/)

### Step 1: Clone the Repository
```bash
git clone https://github.com/Andrew-3y/f1-dashboard.git
cd f1-dashboard
```

### Step 2: Create a Virtual Environment
```bash
python -m venv venv

# Activate on Windows
venv\Scripts\activate

# Activate on Mac/Linux
source venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Run Locally
```bash
python app.py
```
Open `http://localhost:5000`. The dashboard auto-detects the latest F1 session.

### Step 5: Try a Specific Session
```
http://localhost:5000/?year=2024&round=1&session_type=Race
```

---

## Deployment on Render (Free Tier)

### Push Changes to GitHub
```bash
git add .
git commit -m "describe your change here"
git push origin main
```
Render auto-redeploys every time you push to GitHub — no manual steps needed.

### First-Time Render Setup
1. Go to [render.com](https://render.com) → sign up free → connect GitHub
2. Click **New + → Web Service** → select `f1-dashboard`
3. Configure:

| Setting | Value |
|---------|-------|
| Runtime | `Python` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
| Plan | `Free` |

4. Add environment variable: `FASTF1_CACHE` = `/tmp/fastf1_cache`
5. Click **Create Web Service**

---

## Testing During a Live Race

1. Open your dashboard during or shortly after a session
2. Set Auto-Refresh to **Live (30s)** using the dropdown in the top bar
3. Watch for:
   - **Pit Windows** turning OPEN — drivers becoming vulnerable
   - **Battle Watch** — who's fighting on track right now
   - **Strategy recommendations** — PIT NOW signals for potential undercuts
   - **Anomaly alerts** — sudden pace drops indicating issues
4. FastF1 data typically becomes available 1-2 hours after a session ends

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No completed session found" | Off-season. Use manual selector (e.g. year=2024, round=24, Race) |
| First load is slow (30-60s) | Normal — FastF1 downloads session data on first load, cached after |
| Degradation shows N/A | Stint too short (<4 clean laps). Happens early in a race |
| Strategy shows no data | Requires cumulative race time — only available in Race sessions |
| Render build failed | Check build logs. Usually a pip dependency issue |
| Push rejected by GitHub | Run `git pull origin main --rebase` then `git push origin main` |

---

## Tech Stack

| Technology | Purpose | Cost |
|-----------|---------|------|
| Python 3.11 | Core language | Free |
| Flask | Web framework | Free |
| FastF1 | F1 timing data API | Free |
| Pandas | Data processing | Free |
| NumPy | Numerical computation (linear regression) | Free |
| Gunicorn | Production WSGI server | Free |
| Render | Cloud hosting | Free tier |

---

## Portfolio Description

> **F1 Strategy Intelligence Dashboard** — An on-demand Formula 1 analytics platform built with Python and Flask. Features include real-time tire degradation modeling using linear regression, pit strategy simulation with undercut/overcut analysis, on-track battle detection, overtake prediction via gap-closing rate analysis, and lap-time anomaly detection. Deployed on Render's free tier using FastF1's public timing API. Designed for performance on constrained infrastructure with in-memory caching and vectorized Pandas operations.
