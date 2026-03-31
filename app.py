"""
app.py — Flask Application (Main Entry Point)
==============================================

This is the file that Render will run.  It creates a Flask web server
that serves the F1 Intelligence Dashboard with session-specific analysis.

HOW IT WORKS (on-demand architecture):
  1. User opens the URL → Flask receives a GET request.
  2. Flask calls data_handler to fetch the latest F1 session data.
  3. Based on session type, the data is routed to the right analysis:
     RACE / SPRINT:
       - anomaly.py, predictor.py, degradation.py, strategy.py, battle_detector.py
     QUALIFYING:
       - qualifying.py (sectors, elimination, improvement, team pace)
     PRACTICE:
       - practice.py (long runs, short runs, compounds, team ranking, consistency)
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
import threading
import logging
import html
import pandas as pd
from flask import Flask, render_template, request, jsonify

# Our custom modules
from data_handler import get_dashboard_data, load_session
from anomaly import detect_anomalies, get_anomaly_summary
from predictor import predict_overtakes, get_prediction_summary
from degradation import analyze_degradation, get_degradation_summary
from strategy import simulate_strategies, get_strategy_summary
from battle_detector import detect_battles, get_battle_summary
from qualifying import analyze_qualifying, get_qualifying_summary
from practice import analyze_practice, get_practice_summary, analyze_qualifying_projection
from race_projection import project_race_finish, project_sprint_finish
from prediction_accuracy import compare_predictions, empty_accuracy
from validation import validate_session, empty_validation

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


@app.template_filter("lapfmt")
def _format_lap_seconds(seconds):
    """Format float seconds as M:SS.mmm for dashboard display."""
    if seconds is None:
        return "—"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if seconds < 0:
        seconds = abs(seconds)
    minutes = int(seconds // 60)
    rem = seconds - (minutes * 60)
    return f"{minutes}:{rem:06.3f}"


def _base_dashboard_context():
    """Return a stable context for all dashboard renders."""
    return {
        "error": None,
        "session_info": None,
        "session_category": "race",
        "leaderboard": [],
        "load_time": 0,
        "validation_report": empty_validation(),
        **_empty_race(),
        **_empty_qualifying(),
        **_empty_practice(),
    }


def _render_plain_error(message, status_code=500):
    """Final fallback if the dashboard template itself cannot render."""
    safe_message = html.escape(str(message or "Unknown error"))
    return (
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>F1 Strategy Intelligence</title>
  <style>
    body {{
      margin: 0;
      font-family: Inter, Arial, sans-serif;
      background: #0a0a0f;
      color: #e8e8f0;
      display: flex;
      min-height: 100vh;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      width: min(680px, 100%);
      background: #1a1a2e;
      border: 1px solid #2a2a40;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.35);
    }}
    h1 {{
      margin: 0 0 12px;
      color: #e10600;
      font-size: 28px;
    }}
    p {{
      margin: 0 0 16px;
      color: #b6b6ca;
      line-height: 1.6;
    }}
    .detail {{
      background: #12121a;
      border: 1px solid #2a2a40;
      border-radius: 10px;
      color: #ff6d00;
      padding: 14px;
      font-family: Consolas, monospace;
      font-size: 14px;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    a {{
      color: #ffffff;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>F1 Strategy Intelligence</h1>
    <p>We hit a load problem for this session, but the app stayed up. Try reloading or choosing a different round, then come back.</p>
    <div class="detail">{safe_message}</div>
    <p style="margin-top:16px;"><a href="/">Return to dashboard</a></p>
  </div>
</body>
</html>""",
        status_code,
    )


def _render_dashboard(status_code=200, **context):
    """Render the dashboard safely and never fall through to a raw browser 500."""
    payload = _base_dashboard_context()
    payload.update(context)
    try:
        return render_template("dashboard.html", **payload), status_code
    except Exception as exc:
        logger.exception("Dashboard template render failed")
        fallback_message = payload.get("error") or str(exc)
        return _render_plain_error(fallback_message, status_code=status_code)


@app.errorhandler(Exception)
def _handle_unexpected_error(exc):
    logger.exception("Unhandled error")
    return _render_dashboard(500, error=str(exc))

# ---------------------------------------------------------------------------
# Warmup cache (avoid cold-start timeouts)
# ---------------------------------------------------------------------------
_warm_cache = {
    "key": None,
    "data": None,
    "analysis": None,
    "session_category": None,
    "error": None,
    "in_progress": False,
    "updated_at": None,
}
_warm_lock = threading.Lock()


def _read_warm_cache():
    """Return a snapshot of the warm cache."""
    with _warm_lock:
        return {
            "key": _warm_cache["key"],
            "data": _warm_cache["data"],
            "analysis": _warm_cache["analysis"],
            "session_category": _warm_cache["session_category"],
            "error": _warm_cache["error"],
            "in_progress": _warm_cache["in_progress"],
            "updated_at": _warm_cache["updated_at"],
        }


def _start_warmup(year, round_num, session_type):
    """Warm up a requested session in a background thread."""
    with _warm_lock:
        requested_key = (year, round_num, session_type)
        if _warm_cache["in_progress"] and _warm_cache["key"] == requested_key:
            return
        _warm_cache["key"] = requested_key
        _warm_cache["data"] = None
        _warm_cache["analysis"] = None
        _warm_cache["session_category"] = _session_category(session_type)
        _warm_cache["in_progress"] = True
        _warm_cache["error"] = None

    def _worker():
        try:
            data = get_dashboard_data(year, round_num, session_type)
            actual_type = data.get("session_info", {}).get("session_type", session_type) if data.get("session_info") else session_type
            category = _session_category(actual_type)
            if data.get("error"):
                analysis = _base_dashboard_context()
            elif category == "qualifying":
                analysis = {**_empty_race(), **_run_qualifying_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"), leaderboard=data.get("leaderboard")), **_empty_practice()}
            elif category == "practice":
                analysis = {**_empty_race(), **_empty_qualifying(), **_run_practice_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"))}
            else:
                analysis = {**_run_race_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"), leaderboard=data.get("leaderboard")), **_empty_qualifying(), **_empty_practice()}
            if not data.get("error"):
                analysis = _attach_validation(category, data.get("leaderboard", []), analysis, session_info=data.get("session_info"))
            with _warm_lock:
                _warm_cache.update(
                    {
                        "key": (year, round_num, session_type),
                        "data": data,
                        "analysis": analysis,
                        "session_category": category,
                        "error": data.get("error"),
                        "updated_at": time.time(),
                    }
                )
        except Exception as exc:
            with _warm_lock:
                _warm_cache["error"] = str(exc)
        finally:
            with _warm_lock:
                _warm_cache["in_progress"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _render_warmup(session_type=None):
    """Render the warmup state for a requested session."""
    category = _session_category(session_type)
    return _render_dashboard(
        session_category=category,
        error="WARMUP: Loading the requested session data. This can take ~30s on a cold start. The page will refresh automatically.",
    )


# ---------------------------------------------------------------------------
# Helper: classify session type
# ---------------------------------------------------------------------------
def _session_category(session_type):
    """Return 'race', 'qualifying', or 'practice' based on session type string."""
    if not session_type:
        return "race"
    st = session_type.lower()
    if st in ("race", "sprint"):
        return "race"
    elif st in ("qualifying", "sprint shootout"):
        return "qualifying"
    elif st.startswith("practice") or st.startswith("fp") or st in ("practice 1", "practice 2", "practice 3"):
        return "practice"
    return "race"


# ---------------------------------------------------------------------------
# Helper: run race analysis modules
# ---------------------------------------------------------------------------
def _run_race_analysis(laps, session=None, session_info=None, leaderboard=None):
    """Run race-specific analysis modules."""
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

    # 4. Pit strategy simulation
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

    try:
        race_projection_accuracy = _build_race_projection_accuracy(
            session_info,
            session,
            _leaderboard_accuracy_rows(leaderboard),
        )
    except Exception as exc:
        logger.warning("Race projection accuracy failed: %s", exc)
        race_projection_accuracy = empty_accuracy()

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
        "race_projection_accuracy": race_projection_accuracy,
    }


# ---------------------------------------------------------------------------
# Helper: run qualifying analysis
# ---------------------------------------------------------------------------
def _load_practice_context(session_info):
    """Load available practice sessions for the same weekend."""
    if not session_info:
        return []

    practice_sessions = []
    for session_type in ("Practice 1", "Practice 2", "Practice 3"):
        try:
            practice_session, practice_laps = load_session(
                session_info["year"],
                session_info["round_number"],
                session_type,
            )
        except Exception as exc:
            logger.info("Skipping %s context: %s", session_type, exc)
            continue

        if practice_laps is None or practice_laps.empty:
            continue

        practice_sessions.append(
            {
                "session_type": session_type,
                "session": practice_session,
                "laps": practice_laps,
            }
        )

    return practice_sessions


def _leaderboard_accuracy_rows(leaderboard):
    """Normalize leaderboard rows for prediction-accuracy comparisons."""
    normalized = []
    for row in leaderboard or []:
        position = row.get("position")
        driver = row.get("driver")
        if position is None or not driver:
            continue
        normalized.append(
            {
                "driver": driver,
                "driver_display": driver,
                "position": position,
                "team": row.get("team", "Unknown"),
            }
        )
    return normalized


def _official_session_accuracy_rows(session, session_type):
    """Build official position rows directly from FastF1 session results."""
    if session is None:
        return []

    try:
        results = session.results
    except Exception:
        return []

    if results is None or getattr(results, "empty", True):
        return []

    rows = results.reset_index()
    if "Position" not in rows.columns:
        return []

    rows = rows.dropna(subset=["Position"]).copy()
    if rows.empty:
        return []

    normalized = []
    for _, row in rows.iterrows():
        driver = row.get("Abbreviation") or row.get("BroadcastName") or row.get("DriverNumber")
        if not driver:
            continue

        position = row.get("Position")
        if pd.isna(position):
            continue

        normalized.append(
            {
                "driver": str(driver),
                "driver_display": str(driver),
                "position": int(position),
                "team": row.get("TeamName", "Unknown"),
            }
        )

    return sorted(normalized, key=lambda row: row["position"])


def _build_quali_projection_accuracy(session_info, session):
    """Compare the FP3 qualifying projection with actual qualifying results."""
    if not session_info or session_info.get("session_type") != "Qualifying":
        return empty_accuracy()

    actual_rows = _official_session_accuracy_rows(session, "Qualifying")
    if not actual_rows:
        return empty_accuracy()

    practice_sessions = _load_practice_context(session_info)
    if not practice_sessions:
        return empty_accuracy()

    projection = analyze_qualifying_projection(practice_sessions)
    return compare_predictions(
        projection.get("projected_order", []),
        actual_rows,
    )


def _build_sprint_projection_accuracy(session_info):
    """Compare the sprint-shootout page projection against the official sprint result."""
    if not session_info or session_info.get("session_type") != "Sprint Shootout":
        return empty_accuracy()

    try:
        shootout_session, shootout_laps = load_session(
            session_info["year"],
            session_info["round_number"],
            "Sprint Shootout",
        )
        sprint_session, _ = load_session(
            session_info["year"],
            session_info["round_number"],
            "Sprint",
        )
    except Exception as exc:
        logger.info("Skipping sprint projection accuracy: %s", exc)
        return empty_accuracy()

    if shootout_laps is None or shootout_laps.empty:
        return empty_accuracy()

    actual_rows = _official_session_accuracy_rows(sprint_session, "Sprint")
    if not actual_rows:
        return empty_accuracy()

    shootout_analysis = analyze_qualifying(shootout_laps, session=shootout_session)
    practice_sessions = _load_practice_context(
        {
            "year": session_info["year"],
            "round_number": session_info["round_number"],
            "session_type": "Sprint Shootout",
        }
    )
    projection = project_sprint_finish(
        shootout_analysis,
        practice_sessions=practice_sessions,
        session=shootout_session,
    )

    return compare_predictions(
        projection.get("projected_finish", []),
        actual_rows,
    )


def _build_race_projection_accuracy(session_info, session, actual_rows):
    """Compare the qualifying-page race projection with the official race result."""
    if not session_info or session_info.get("session_type") not in ("Race", "Sprint"):
        return empty_accuracy()

    session_type = session_info.get("session_type")
    actual_rows = _official_session_accuracy_rows(session, session_type) or actual_rows
    if not actual_rows:
        return empty_accuracy()

    if session_type == "Sprint":
        shootout_session, shootout_laps = load_session(
            session_info["year"],
            session_info["round_number"],
            "Sprint Shootout",
        )
        if shootout_laps is None or shootout_laps.empty:
            return empty_accuracy()

        shootout_analysis = analyze_qualifying(shootout_laps, session=shootout_session)
        practice_sessions = _load_practice_context(
            {
                "year": session_info["year"],
                "round_number": session_info["round_number"],
                "session_type": "Sprint Shootout",
            }
        )
        projection = project_sprint_finish(
            shootout_analysis,
            practice_sessions=practice_sessions,
            session=shootout_session,
        )
    else:
        qualifying_session, qualifying_laps = load_session(
            session_info["year"],
            session_info["round_number"],
            "Qualifying",
        )
        if qualifying_laps is None or qualifying_laps.empty:
            return empty_accuracy()

        qualifying_analysis = analyze_qualifying(qualifying_laps, session=qualifying_session)
        practice_sessions = _load_practice_context(
            {
                "year": session_info["year"],
                "round_number": session_info["round_number"],
                "session_type": "Qualifying",
            }
        )
        projection = project_race_finish(
            qualifying_analysis,
            practice_sessions=practice_sessions,
            session=qualifying_session,
        )

    return compare_predictions(
        projection.get("projected_finish", []),
        actual_rows,
    )


def _attach_validation(session_category, leaderboard, analysis, session_info=None):
    """Attach a validation report without mutating upstream inputs."""
    analysis = {**analysis}
    try:
        analysis["validation_report"] = validate_session(session_category, leaderboard, analysis, session_info=session_info)
    except Exception as exc:
        logger.warning("Validation audit failed: %s", exc)
        analysis["validation_report"] = empty_validation()
    return analysis


def _empty_projection():
    return {
        "projected_finish": [],
        "summary": {
            "predicted_winner": "-",
            "biggest_riser": "-",
            "confidence": "LOW",
            "practice_sessions_used": [],
            "has_practice_pace": False,
        },
    }


def _run_qualifying_analysis(laps, session=None, session_info=None, leaderboard=None):
    """Run qualifying-specific analysis modules."""
    session_type = (session_info or {}).get("session_type")
    is_sprint_shootout = session_type == "Sprint Shootout"
    try:
        quali_analysis = analyze_qualifying(laps, session=session)
        quali_summary = get_qualifying_summary(quali_analysis)
    except Exception as exc:
        logger.warning("Qualifying analysis failed: %s", exc)
        quali_analysis = {"sectors": [], "elimination": {"q1_eliminated": [], "q2_eliminated": [], "q3_drivers": []}, "improvement": [], "team_pace": [], "theoretical_best": [], "teammate_battles": [], "track_evolution": [], "close_calls": [], "tyre_usage": [], "race_projection": []}
        quali_summary = {
            "race_projection": _empty_projection()["summary"],
            "qualifying_projection_accuracy": empty_accuracy(),
        }
        return {
            "quali_analysis": quali_analysis,
            "quali_summary": quali_summary,
        }

    try:
        practice_sessions = _load_practice_context(session_info)
    except Exception as exc:
        logger.warning("Practice context load failed: %s", exc)
        practice_sessions = []

    try:
        if is_sprint_shootout:
            projection = project_sprint_finish(quali_analysis, practice_sessions=practice_sessions, session=session)
        else:
            projection = project_race_finish(quali_analysis, practice_sessions=practice_sessions, session=session)
    except Exception as exc:
        logger.warning("%s projection failed: %s", "Sprint" if is_sprint_shootout else "Race", exc)
        projection = _empty_projection()

    try:
        if is_sprint_shootout:
            qualifying_projection_accuracy = empty_accuracy()
        else:
            qualifying_projection_accuracy = _build_quali_projection_accuracy(
                session_info,
                session,
            )
    except Exception as exc:
        logger.warning("%s projection accuracy failed: %s", "Sprint" if is_sprint_shootout else "Qualifying", exc)
        qualifying_projection_accuracy = empty_accuracy()

    quali_analysis["race_projection"] = projection["projected_finish"]
    quali_summary["race_projection"] = projection["summary"]
    quali_summary["qualifying_projection_accuracy"] = qualifying_projection_accuracy

    return {
        "quali_analysis": quali_analysis,
        "quali_summary": quali_summary,
    }


# ---------------------------------------------------------------------------
# Helper: run practice analysis
# ---------------------------------------------------------------------------
def _run_practice_analysis(laps, session=None, session_info=None):
    """Run practice-specific analysis modules."""
    try:
        practice_sessions = []
        if session_info and session_info.get("session_type") == "Practice 3":
            practice_sessions.append({"session_type": "Practice 3", "session": session, "laps": laps})
            for session_type in ("Practice 1", "Practice 2"):
                try:
                    earlier_session, earlier_laps = load_session(session_info["year"], session_info["round_number"], session_type)
                except Exception as exc:
                    logger.info("Skipping %s context: %s", session_type, exc)
                    continue
                if earlier_laps is not None and not earlier_laps.empty:
                    practice_sessions.append({"session_type": session_type, "session": earlier_session, "laps": earlier_laps})
        practice_analysis = analyze_practice(laps, session_info=session_info, practice_sessions=practice_sessions)
        practice_summary = get_practice_summary(practice_analysis)
    except Exception as exc:
        logger.warning("Practice analysis failed: %s", exc)
        practice_analysis = {"long_runs": [], "short_runs": [], "compounds": [], "team_ranking": [], "consistency": [], "programmes": [], "theoretical_best": [], "sectors": [], "track_evolution": [], "tyre_deg_curves": [], "race_pace_prediction": [], "qualifying_projection": [], "qualifying_projection_summary": {}}
        practice_summary = {}

    return {
        "practice_analysis": practice_analysis,
        "practice_summary": practice_summary,
    }


# ---------------------------------------------------------------------------
# Helper: empty defaults for all analysis types
# ---------------------------------------------------------------------------
def _empty_race():
    return {
        "alerts": [], "alert_summary": {},
        "predictions": [], "prediction_summary": {},
        "degradation": [], "degradation_summary": {},
        "strategies": [], "strategy_summary": {},
        "battles": [], "battle_summary": {},
        "race_projection_accuracy": empty_accuracy(),
    }

def _empty_qualifying():
    return {
        "quali_analysis": {"sectors": [], "elimination": {"q1_eliminated": [], "q2_eliminated": [], "q3_drivers": []}, "improvement": [], "team_pace": [], "theoretical_best": [], "teammate_battles": [], "track_evolution": [], "close_calls": [], "tyre_usage": [], "race_projection": []},
        "quali_summary": {
            "race_projection": _empty_projection()["summary"],
            "qualifying_projection_accuracy": empty_accuracy(),
        },
    }

def _empty_practice():
    return {
        "practice_analysis": {"long_runs": [], "short_runs": [], "compounds": [], "team_ranking": [], "consistency": [], "programmes": [], "theoretical_best": [], "sectors": [], "track_evolution": [], "tyre_deg_curves": [], "race_pace_prediction": [], "qualifying_projection": [], "qualifying_projection_summary": {}},
        "practice_summary": {},
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
        session_type — 'Race', 'Qualifying', 'Sprint', 'Practice 1', etc.

    If no parameters are given, auto-detects the latest session.
    """
    # Render health checks use HEAD; respond fast to avoid expensive loads.
    if request.method == "HEAD":
        return ("", 200)
    start_time = time.time()

    # Check for manual session selection
    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)

    # Known problematic session: avoid hard 500s and show a friendly message.
    if year == 2026 and round_num == 1 and (session_type or "").lower() == "qualifying":
        return _render_dashboard(
            session_category="qualifying",
            error="Qualifying data for 2026 Round 1 is intermittently unavailable from the upstream feed. Please try again later or select another session.",
        )

    requested_key = (year, round_num, session_type)
    warm_state = _read_warm_cache()

    if warm_state["key"] == requested_key and warm_state["data"] and warm_state["analysis"]:
        if warm_state["error"]:
            return _render_dashboard(
                session_category=warm_state["session_category"],
                error=warm_state["error"],
            )
        return _render_dashboard(
            error=None,
            session_info=warm_state["data"]["session_info"],
            session_category=warm_state["session_category"],
            leaderboard=warm_state["data"]["leaderboard"],
            load_time=0,
            **warm_state["analysis"],
        )

    # Cold-start warmup path for both auto-detected and explicit requests.
    if warm_state["in_progress"] and warm_state["key"] == requested_key:
        return _render_warmup(session_type)

    if not (year and round_num and session_type):
        if not warm_state["in_progress"]:
            _start_warmup(year, round_num, session_type)
        return _render_warmup(session_type)

    # Explicit session requests now warm in the background first so a slow
    # FastF1 load cannot dump users onto a raw 500 page on Render.
    _start_warmup(year, round_num, session_type)
    return _render_warmup(session_type)


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
    if request.method == "HEAD":
        return ("", 200)
    start_time = time.time()

    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)

    try:
        data = get_dashboard_data(year, round_num, session_type)
    except Exception as exc:
        logger.exception("API request failed")
        return jsonify({"error": str(exc)}), 500

    if data["error"]:
        return jsonify({"error": data["error"]}), 500

    actual_type = data.get("session_info", {}).get("session_type", session_type)
    category = _session_category(actual_type)

    if category == "qualifying":
        analysis = _run_qualifying_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"), leaderboard=data.get("leaderboard"))
    elif category == "practice":
        analysis = _run_practice_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"))
    else:
        analysis = _run_race_analysis(data["laps"], session=data.get("session"), session_info=data.get("session_info"), leaderboard=data.get("leaderboard"))
    analysis = _attach_validation(category, data.get("leaderboard", []), analysis, session_info=data.get("session_info"))

    elapsed = round(time.time() - start_time, 2)

    return jsonify(
        {
            "session_info": data["session_info"],
            "session_category": category,
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
