"""
app.py — Flask Application (Main Entry Point)
==============================================

This is the file that Render will run.  It creates a Flask web server
that serves a single page: the F1 Intelligence Dashboard.

HOW IT WORKS (on-demand architecture):
  1. User opens the URL → Flask receives a GET request.
  2. Flask calls data_handler to fetch the latest F1 session data.
  3. The data is passed through anomaly.py and predictor.py.
  4. Everything is injected into an HTML template and returned.
  5. The server does NOTHING between requests (Render's free tier
     spins it down after ~15 min of inactivity).

ROUTES:
  GET /            → Main dashboard (auto-detects latest session)
  GET /session     → Dashboard for a specific year/round/type
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

    # If there was an error, render error page
    if data["error"]:
        elapsed = round(time.time() - start_time, 2)
        return render_template(
            "dashboard.html",
            error=data["error"],
            session_info=None,
            leaderboard=[],
            alerts=[],
            alert_summary={},
            predictions=[],
            prediction_summary={},
            load_time=elapsed,
        )

    # Run anomaly detection
    alerts = detect_anomalies(data["laps"])
    alert_summary = get_anomaly_summary(alerts)

    # Run overtake predictions
    predictions = predict_overtakes(data["laps"])
    prediction_summary = get_prediction_summary(predictions)

    elapsed = round(time.time() - start_time, 2)
    logger.info("Dashboard rendered in %.2fs", elapsed)

    return render_template(
        "dashboard.html",
        error=None,
        session_info=data["session_info"],
        leaderboard=data["leaderboard"],
        alerts=alerts,
        alert_summary=alert_summary,
        predictions=predictions,
        prediction_summary=prediction_summary,
        load_time=elapsed,
    )


# ---------------------------------------------------------------------------
# ROUTE: JSON API (for AJAX auto-refresh)
# ---------------------------------------------------------------------------
@app.route("/api/data")
def api_data():
    """
    Returns all dashboard data as JSON.  The front-end JavaScript can
    call this endpoint every 30 seconds to refresh the page without
    a full reload.

    Same query parameters as the main route.
    """
    start_time = time.time()

    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)

    data = get_dashboard_data(year, round_num, session_type)

    if data["error"]:
        return jsonify({"error": data["error"]}), 500

    alerts = detect_anomalies(data["laps"])
    predictions = predict_overtakes(data["laps"])

    elapsed = round(time.time() - start_time, 2)

    return jsonify(
        {
            "session_info": data["session_info"],
            "leaderboard": [
                {k: v for k, v in d.items() if k != "best_lap"}
                for d in data["leaderboard"]
            ],
            "alerts": alerts,
            "alert_summary": get_anomaly_summary(alerts),
            "predictions": predictions,
            "prediction_summary": get_prediction_summary(predictions),
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
    # Render will use gunicorn instead (see Procfile / render.yaml)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
