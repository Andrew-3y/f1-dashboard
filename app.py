"""
app.py — Flask Application (Main Entry Point)
==============================================

This is the file that Render will run.  It creates a Flask web server
that serves a single page: the F1 Intelligence Dashboard.

HOW IT WORKS (on-demand architecture):
  1. User opens the URL → Flask receives a GET request.
  2. Flask calls data_handler to fetch the latest F1 session data.
  3. The data is passed through ALL analysis modules:
     - anomaly.py         → pace anomaly detection
     - predictor.py       → overtake prediction
     - degradation.py     → tire degradation + pit window
     - strategy.py        → pit strategy simulation
     - battle_detector.py → on-track battle detection
  4. Everything is injected into an HTML template and returned.
  5. The server does NOTHING between requests (Render's free tier
     spins it down after ~15 min of inactivity).

ROUTES:
  GET /            → Main dashboard (auto-detects latest session)
  GET /api/data    → JSON API endpoint (for AJAX refresh)
  GET /health      → Health check (Render uses this to know we're alive)
"""

import os
import time
import logging
from flask import Flask, render_template, request, jsonify

# Our custom modules
from data_handler import get_dashboard_data
from anomaly import detect_anomalies, get_anomaly_summary
from predictor import predict_overtakes, get_prediction_summary
from degradation import analyze_degradation, get_degradation_summary
from strategy import simulate_strategies, get_strategy_summary
from battle_detector import detect_battles, get_battle_summary

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Configure logging so we can see what's happening in Render's logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: run all analysis modules on the loaded data
# ---------------------------------------------------------------------------
def _run_full_analysis(laps):
    """
    Run every analysis module and return their outputs in a dict.
    Each module is wrapped in a try/except so a failure in one
    doesn't crash the entire dashboard.

    Parameters
    ----------
    laps : pandas.DataFrame
        The laps DataFrame from a loaded FastF1 session.

    Returns
    -------
    dict with keys for each module's outputs + summaries.
    """
    # 1. Anomaly detection
    try:
        alerts = detect_anomalies(laps)
        alert_summary = get_anomaly_summary(alerts)
    except Exception as exc:
        logger.warning("Anomaly detection failed: %s", exc)
        alerts, alert_summary = [], {}

    # 2. Overtake predictions
    try:
        predictions = predict_overtakes(laps)
        prediction_summary = get_prediction_summary(predictions)
    except Exception as exc:
        logger.warning("Overtake prediction failed: %s", exc)
        predictions, prediction_summary = [], {}

    # 3. Tire degradation + pit window
    try:
        degradation = analyze_degradation(laps)
        degradation_summary = get_degradation_summary(degradation)
    except Exception as exc:
        logger.warning("Degradation analysis failed: %s", exc)
        degradation, degradation_summary = [], {}

    # 4. Pit strategy simulation (uses degradation data if available)
    try:
        strategies = simulate_strategies(laps, degradation_data=degradation)
        strategy_summary = get_strategy_summary(strategies)
    except Exception as exc:
        logger.warning("Strategy simulation failed: %s", exc)
        strategies, strategy_summary = [], {}

    # 5. Battle detection
    try:
        battles = detect_battles(laps)
        battle_summary = get_battle_summary(battles)
    except Exception as exc:
        logger.warning("Battle detection failed: %s", exc)
        battles, battle_summary = [], {}

    return {
        "alerts": alerts,
        "alert_summary": alert_summary,
        "predictions": predictions,
        "prediction_summary": prediction_summary,
        "degradation": degradation,
        "degradation_summary": degradation_summary,
        "strategies": strategies,
        "strategy_summary": strategy_summary,
        "battles": battles,
        "battle_summary": battle_summary,
    }


# ---------------------------------------------------------------------------
# ROUTE: Main Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """
    Main dashboard page.

    Query parameters (all optional):
        year         — e.g. 2025
        round        — round number, e.g. 3
        session_type — 'Race', 'Qualifying', 'Sprint'

    If no parameters are given, auto-detects the latest session.
    """
    start_time = time.time()

    # Check for manual session selection
    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)

    # Fetch data (on-demand!)
    logger.info("Dashboard request: year=%s round=%s type=%s", year, round_num, session_type)
    data = get_dashboard_data(year, round_num, session_type)

    # If there was an error, render error page with empty data for all sections
    if data["error"]:
        elapsed = round(time.time() - start_time, 2)
        return render_template(
            "dashboard.html",
            error=data["error"],
            session_info=None,
            leaderboard=[],
            alerts=[], alert_summary={},
            predictions=[], prediction_summary={},
            degradation=[], degradation_summary={},
            strategies=[], strategy_summary={},
            battles=[], battle_summary={},
            load_time=elapsed,
        )

    # Run all analysis modules
    analysis = _run_full_analysis(data["laps"])

    elapsed = round(time.time() - start_time, 2)
    logger.info("Dashboard rendered in %.2fs", elapsed)

    return render_template(
        "dashboard.html",
        error=None,
        session_info=data["session_info"],
        leaderboard=data["leaderboard"],
        load_time=elapsed,
        **analysis,  # unpack all analysis results into template context
    )


# ---------------------------------------------------------------------------
# ROUTE: JSON API (for AJAX auto-refresh)
# ---------------------------------------------------------------------------
@app.route("/api/data")
def api_data():
    """
    Returns all dashboard data as JSON.  The front-end JavaScript can
    call this endpoint to refresh the page without a full reload.

    Same query parameters as the main route.
    """
    start_time = time.time()

    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)

    data = get_dashboard_data(year, round_num, session_type)

    if data["error"]:
        return jsonify({"error": data["error"]}), 500

    analysis = _run_full_analysis(data["laps"])

    elapsed = round(time.time() - start_time, 2)

    return jsonify(
        {
            "session_info": data["session_info"],
            "leaderboard": [
                {k: v for k, v in d.items() if k != "best_lap"}
                for d in data["leaderboard"]
            ],
            **{k: v for k, v in analysis.items()},
            "load_time": elapsed,
        }
    )


# ---------------------------------------------------------------------------
# ROUTE: Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    """
    Simple health check.  Render pings this to verify the app is running.
    """
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Run the app
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # When running locally: python app.py
    # Render will use gunicorn instead (see render.yaml)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
