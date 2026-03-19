# F1 On-Demand Intelligence Dashboard

A free, serverless Formula 1 analytics dashboard that fetches live timing data on-demand and presents a leaderboard, anomaly detection alerts, and overtake predictions — all powered by Python, FastF1, and Flask.

**Live demo architecture:** The app sleeps on Render's free tier and wakes up when someone visits the URL. No always-running server needed.

---

## Features

- **Auto-detecting leaderboard** — Finds the latest completed F1 session and displays driver positions, best laps, and gaps
- **Anomaly detection** — Flags sudden pace losses using rolling-average analysis (with severity levels: CRITICAL / HIGH / MEDIUM / LOW)
- **Overtake predictions** — Calculates gap-closing rates between drivers and estimates laps until DRS range
- **Manual session selector** — Pick any year/round/session type to explore historical data
- **Auto-refresh** — Page reloads every 60 seconds during live sessions
- **Dark F1-themed UI** — Clean, responsive design that looks great on desktop and mobile

---

## Project Structure

```
f1-dashboard/
├── app.py              # Flask web server — routes and page rendering
├── data_handler.py     # FastF1 data fetching, caching, leaderboard building
├── anomaly.py          # Lap-time anomaly detection algorithm
├── predictor.py        # Overtake prediction system
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment blueprint
├── .gitignore          # Files to exclude from Git
├── README.md           # This file
└── templates/
    └── dashboard.html  # Full HTML/CSS/JS dashboard template
```

---

## File-by-File Explanation

### `app.py` — The Main Application
This is the entry point. It creates a Flask web server with three routes:
- `GET /` — Renders the dashboard (auto-detects or accepts query params for year/round/session)
- `GET /api/data` — Returns JSON (for potential AJAX refresh)
- `GET /health` — Health check endpoint for Render

When a request comes in, it calls `data_handler` to fetch data, then runs it through `anomaly.py` and `predictor.py`, and finally injects everything into the HTML template.

### `data_handler.py` — Data Fetching & Processing
Handles all communication with FastF1:
- `get_latest_session_info()` — Scans the F1 calendar to find the most recent completed session
- `load_session()` — Downloads lap data via FastF1 (with in-memory caching so repeat visits are fast)
- `build_leaderboard()` — Groups laps by driver, finds best lap times, calculates gaps to the leader
- `format_laptime()` / `format_gap()` — Convert raw numbers to display strings like "1:23.456"

### `anomaly.py` — Anomaly Detection
Scans each driver's laps to find sudden pace drops:
1. Computes a 5-lap rolling average for each driver
2. Compares each lap time against the rolling average
3. If a lap is >1 second slower, it's flagged as an anomaly
4. Pit laps are filtered out (they're expected to be slow)
5. Results are sorted by severity (biggest pace loss first)

Severity levels: CRITICAL (>3s), HIGH (>2s), MEDIUM (>1.5s), LOW (>1s)

### `predictor.py` — Overtake Predictions
Analyzes gap trends between consecutive drivers:
1. For each driver pair (P2 vs P1, P3 vs P2, etc.), get their recent lap times
2. Calculate the "closing rate" — how much time the chaser gains per lap
3. Estimate laps until the gap drops below 1 second (DRS threshold)
4. Assign confidence: HIGH (strong closing + small gap + lots of data), MEDIUM, or LOW

### `templates/dashboard.html` — The UI
A single-file HTML document with embedded CSS and JavaScript:
- **CSS** — Dark theme with CSS variables for easy retheming
- **Layout** — Header, stats row, two-column grid (alerts + predictions), full-width leaderboard
- **JavaScript** — Auto-refresh timer, custom session loading, Enter-key support

---

## Local Setup (Step by Step)

### Prerequisites
- Python 3.9 or higher installed ([download](https://www.python.org/downloads/))
- Git installed ([download](https://git-scm.com/downloads))
- A terminal / command prompt

### Step 1: Clone or Download the Project
```bash
# If you have the files already, skip this.
# Otherwise, after pushing to GitHub:
git clone https://github.com/YOUR_USERNAME/f1-dashboard.git
cd f1-dashboard
```

### Step 2: Create a Virtual Environment (Recommended)
```bash
# Create
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
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
Open your browser to `http://localhost:5000`. The dashboard will auto-detect the latest F1 session and display results.

### Step 5: Try a Specific Session
Add query parameters to the URL:
```
http://localhost:5000/?year=2024&round=1&session_type=Race
```

---

## Deployment on Render (Step by Step)

### Step 1: Push Code to GitHub

```bash
# Initialize a Git repo (if you haven't already)
cd f1-dashboard
git init
git add .
git commit -m "Initial commit: F1 Intelligence Dashboard"

# Create a repo on GitHub (https://github.com/new), then:
git remote add origin https://github.com/YOUR_USERNAME/f1-dashboard.git
git branch -M main
git push -u origin main
```

### Step 2: Create a Render Account
1. Go to [https://render.com](https://render.com)
2. Sign up for free (no credit card required)
3. Connect your GitHub account

### Step 3: Create a New Web Service
1. Click **"New +"** in the top right
2. Select **"Web Service"**
3. Connect your GitHub repository (`f1-dashboard`)
4. Configure:

| Setting | Value |
|---------|-------|
| **Name** | `f1-intelligence-dashboard` |
| **Region** | Choose closest to you |
| **Branch** | `main` |
| **Runtime** | `Python` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
| **Plan** | `Free` |

5. Add environment variables:

| Key | Value |
|-----|-------|
| `PYTHON_VERSION` | `3.11.0` |
| `FASTF1_CACHE` | `/tmp/fastf1_cache` |

6. Click **"Create Web Service"**

### Step 4: Wait for Deployment
- Render will install dependencies and start your app
- This takes 2-5 minutes on the first deploy
- You'll get a public URL like: `https://f1-intelligence-dashboard.onrender.com`

### Step 5: Visit Your Dashboard
Open the URL. The first load may take 30-60 seconds (free tier cold start + data download). Subsequent loads within the same wake cycle will be much faster thanks to caching.

---

## Testing During a Live Race

1. Open your dashboard URL during or shortly after a race session
2. The app auto-detects the latest session — if a race just finished, it will load that data
3. Watch for:
   - **Anomaly alerts** — drivers who had sudden pace drops
   - **Overtake predictions** — which battles were closest
4. The page auto-refreshes every 60 seconds
5. Note: FastF1 data typically becomes available 1-2 hours after a session ends

---

## Customization Ideas

- Change the color scheme by editing CSS variables in `dashboard.html`
- Adjust anomaly sensitivity by changing `PACE_LOSS_THRESHOLD` in `anomaly.py`
- Add more stats (sector times, tyre strategies) by extending `data_handler.py`
- Add charts using Chart.js or Plotly (include via CDN in the HTML)

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No completed F1 session found" | It's the off-season or pre-season. Use the manual selector to pick a past race (e.g., year=2024, round=24, Race) |
| First load is very slow | Normal — FastF1 downloads ~10-30MB of data on first load. Cached after that. |
| Render shows "Build failed" | Check that `requirements.txt` has no typos. Check Render build logs. |
| Data looks wrong | FastF1 data depends on the F1 API. Some sessions may have incomplete data. |
| App crashes with memory error | Render free tier has 512MB RAM. The app is optimized for this, but very large sessions may struggle. Try Qualifying instead of Race. |

---

## Tech Stack

| Technology | Purpose | Cost |
|-----------|---------|------|
| Python 3.11 | Core language | Free |
| Flask | Web framework | Free |
| FastF1 | F1 data API | Free |
| Pandas | Data processing | Free |
| NumPy | Numerical computation | Free |
| Gunicorn | Production server | Free |
| Render | Cloud hosting | Free tier |

---

## License

This project is open-source and free to use for any purpose.
Data is provided by the FastF1 library, which sources it from the official F1 timing API.
