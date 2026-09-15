"""
app.py — Flask Application (Main Entry Point)
==============================================

This is the file that Render will run. It serves a post-race Formula 1
review built from completed race data.

HOW IT WORKS (on-demand architecture):
  1. User opens the URL → Flask receives a GET request.
  2. Flask calls data_handler to fetch the latest completed Grand Prix.
  3. It builds the official classification, post-race summary, notable pace
     losses, and the pre-race projection accuracy report.
  4. The review is injected into an HTML template and returned.
  5. The server does NOTHING between requests (Render's free tier
     spins it down after ~15 min of inactivity).

ROUTES:
  GET /            → Main dashboard (auto-detects latest completed race)
  GET /api/data    → JSON API endpoint
  GET /health      → Health check (Render uses this to know we're alive)
"""

import os
import time
import threading
import logging
import html
import datetime
import pandas as pd
from flask import Flask, render_template, request, jsonify
from werkzeug.exceptions import HTTPException

# Our custom modules
from data_handler import get_dashboard_data, get_latest_session_info, load_session
from anomaly import detect_anomalies, get_anomaly_summary
from qualifying import analyze_qualifying
from race_projection import project_race_finish, project_sprint_finish
from prediction_accuracy import compare_predictions, empty_accuracy
from season_form import build_season_form, empty_season_form
from circuit_intel import build_circuit_intelligence, empty_circuit_intelligence
from driver_intel import build_driver_intelligence, empty_driver_intelligence

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
        "race_summary": {},
        **_empty_race(),
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
  <title>F1 Post-Race Review</title>
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
    <h1>F1 Post-Race Review</h1>
    <p>We could not load this completed race. Try reloading or choosing a different round.</p>
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
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("Unhandled error")
    return _render_dashboard(500, error=str(exc))


@app.route("/favicon.ico")
def favicon():
    """Avoid turning the browser's optional favicon request into an error."""
    return ("", 204)

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

_season_warm_cache = {
    "key": None,
    "data": None,
    "error": None,
    "in_progress": False,
    "updated_at": None,
}
_season_warm_lock = threading.Lock()

_circuit_warm_cache = {
    "key": None,
    "data": None,
    "error": None,
    "in_progress": False,
    "updated_at": None,
}
_circuit_warm_lock = threading.Lock()

_driver_warm_cache = {
    "key": None,
    "data": None,
    "error": None,
    "in_progress": False,
    "updated_at": None,
}
_driver_warm_lock = threading.Lock()

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
    """Warm up a requested completed race in a background thread."""
    with _warm_lock:
        requested_key = (year, round_num, session_type)
        if _warm_cache["in_progress"] and _warm_cache["key"] == requested_key:
            return
        _warm_cache["key"] = requested_key
        _warm_cache["data"] = None
        _warm_cache["analysis"] = None
        _warm_cache["session_category"] = "race"
        _warm_cache["in_progress"] = True
        _warm_cache["error"] = None

    def _worker():
        try:
            data = get_dashboard_data(year, round_num, session_type)
            if data.get("error"):
                analysis = _base_dashboard_context()
            else:
                analysis = _run_race_analysis(
                    data["laps"],
                    session=data.get("session"),
                    session_info=data.get("session_info"),
                    leaderboard=data.get("leaderboard"),
                )
            with _warm_lock:
                _warm_cache.update(
                    {
                        "key": (year, round_num, session_type),
                        "data": data,
                        "analysis": analysis,
                        "session_category": "race",
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
    """Render the warmup state for a requested completed race."""
    return _render_dashboard(
        session_category="race",
        error="WARMUP: Loading the completed race review. This can take ~30s on a cold start. The page will refresh automatically.",
    )


def _read_season_warm_cache():
    """Return a snapshot of the season warm cache."""
    with _season_warm_lock:
        return {
            "key": _season_warm_cache["key"],
            "data": _season_warm_cache["data"],
            "error": _season_warm_cache["error"],
            "in_progress": _season_warm_cache["in_progress"],
            "updated_at": _season_warm_cache["updated_at"],
        }


def _start_season_warmup(year, window):
    """Warm up season analysis in a background thread."""
    with _season_warm_lock:
        requested_key = (year, window)
        if _season_warm_cache["in_progress"] and _season_warm_cache["key"] == requested_key:
            return
        _season_warm_cache["key"] = requested_key
        _season_warm_cache["data"] = None
        _season_warm_cache["error"] = None
        _season_warm_cache["in_progress"] = True

    def _worker():
        try:
            data = build_season_form(year, window=window)
            with _season_warm_lock:
                _season_warm_cache.update(
                    {
                        "key": (year, window),
                        "data": data,
                        "error": None,
                        "updated_at": time.time(),
                    }
                )
        except Exception as exc:
            with _season_warm_lock:
                _season_warm_cache["error"] = str(exc)
        finally:
            with _season_warm_lock:
                _season_warm_cache["in_progress"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _render_season_page(season_data=None, *, year=None, window=5, error=None, loading=False, status_code=200):
    """Render the season page with stable fallback data."""
    payload = season_data or empty_season_form()
    payload_meta = payload.get("meta", empty_season_form()["meta"])
    payload_summary = payload.get("summary", empty_season_form()["summary"])
    payload_meta["year"] = year if year is not None else payload_meta.get("year")
    payload_meta["window"] = window
    payload_meta["window_label"] = f"Last {window} rounds"
    return render_template(
        "season.html",
        season_data=payload,
        season_meta=payload_meta,
        season_summary=payload_summary,
        error=error,
        loading=loading,
    ), status_code


def _read_circuit_warm_cache():
    """Return a snapshot of the circuit warm cache."""
    with _circuit_warm_lock:
        return {
            "key": _circuit_warm_cache["key"],
            "data": _circuit_warm_cache["data"],
            "error": _circuit_warm_cache["error"],
            "in_progress": _circuit_warm_cache["in_progress"],
            "updated_at": _circuit_warm_cache["updated_at"],
        }


def _start_circuit_warmup(year, round_num):
    """Warm up circuit intelligence in a background thread."""
    with _circuit_warm_lock:
        requested_key = (year, round_num)
        if _circuit_warm_cache["in_progress"] and _circuit_warm_cache["key"] == requested_key:
            return
        _circuit_warm_cache["key"] = requested_key
        _circuit_warm_cache["data"] = None
        _circuit_warm_cache["error"] = None
        _circuit_warm_cache["in_progress"] = True

    def _worker():
        try:
            data = build_circuit_intelligence(year, round_num)
            with _circuit_warm_lock:
                _circuit_warm_cache.update(
                    {
                        "key": (year, round_num),
                        "data": data,
                        "error": None,
                        "updated_at": time.time(),
                    }
                )
        except Exception as exc:
            with _circuit_warm_lock:
                _circuit_warm_cache["error"] = str(exc)
        finally:
            with _circuit_warm_lock:
                _circuit_warm_cache["in_progress"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _render_circuit_page(circuit_data=None, *, year=None, round_num=None, error=None, loading=False, status_code=200):
    """Render the circuit page with stable fallback data."""
    payload = circuit_data or empty_circuit_intelligence()
    payload_meta = payload.get("meta", empty_circuit_intelligence()["meta"])
    payload_summary = payload.get("summary", empty_circuit_intelligence()["summary"])
    payload_meta["year"] = year if year is not None else payload_meta.get("year")
    payload_meta["round_number"] = round_num if round_num is not None else payload_meta.get("round_number")
    return render_template(
        "circuit.html",
        circuit_data=payload,
        circuit_meta=payload_meta,
        circuit_summary=payload_summary,
        error=error,
        loading=loading,
    ), status_code


def _read_driver_warm_cache():
    """Return a snapshot of the driver warm cache."""
    with _driver_warm_lock:
        return {
            "key": _driver_warm_cache["key"],
            "data": _driver_warm_cache["data"],
            "error": _driver_warm_cache["error"],
            "in_progress": _driver_warm_cache["in_progress"],
            "updated_at": _driver_warm_cache["updated_at"],
        }


def _start_driver_warmup(year, driver, window):
    """Warm up driver intelligence in a background thread."""
    with _driver_warm_lock:
        requested_key = (year, driver or "", window)
        if _driver_warm_cache["in_progress"] and _driver_warm_cache["key"] == requested_key:
            return
        _driver_warm_cache["key"] = requested_key
        _driver_warm_cache["data"] = None
        _driver_warm_cache["error"] = None
        _driver_warm_cache["in_progress"] = True

    def _worker():
        try:
            data = build_driver_intelligence(year, driver=driver, window=window)
            with _driver_warm_lock:
                _driver_warm_cache.update(
                    {
                        "key": (year, driver or "", window),
                        "data": data,
                        "error": None,
                        "updated_at": time.time(),
                    }
                )
        except Exception as exc:
            with _driver_warm_lock:
                _driver_warm_cache["error"] = str(exc)
        finally:
            with _driver_warm_lock:
                _driver_warm_cache["in_progress"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _render_driver_page(driver_data=None, *, year=None, driver=None, window=5, error=None, loading=False, status_code=200):
    """Render the driver page with stable fallback data."""
    payload = driver_data or empty_driver_intelligence()
    empty_payload = empty_driver_intelligence()
    payload_meta = payload.get("meta", empty_payload["meta"])
    payload_summary = payload.get("summary", empty_payload["summary"])
    payload_meta["year"] = year if year is not None else payload_meta.get("year")
    payload_meta["window"] = window
    payload_meta["window_label"] = f"Last {window} rounds"
    if driver:
        payload_meta["driver"] = driver
    return render_template(
        "driver.html",
        driver_data=payload,
        driver_meta=payload_meta,
        driver_summary=payload_summary,
        error=error,
        loading=loading,
    ), status_code


# ---------------------------------------------------------------------------
# Helper: run race analysis modules
# ---------------------------------------------------------------------------
def _run_race_analysis(laps, session=None, session_info=None, leaderboard=None):
    """Build the small set of analyses that remain useful after the finish."""
    try:
        alerts = detect_anomalies(laps)
        alert_summary = get_anomaly_summary(alerts)
    except Exception as exc:
        logger.warning("Anomaly detection failed: %s", exc)
        alerts, alert_summary = [], {}

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
        "race_summary": _build_post_race_summary(leaderboard),
        "race_projection_accuracy": race_projection_accuracy,
    }


def _build_post_race_summary(leaderboard):
    """Create headline facts from the official completed-race classification."""
    rows = leaderboard or []
    if not rows:
        return {
            "winner": "—",
            "podium": "—",
            "race_laps": 0,
            "fastest_lap_driver": "—",
            "fastest_lap_time": "—",
            "biggest_gainer": "—",
            "biggest_gain": 0,
            "retirements": 0,
        }

    fastest_candidates = [row for row in rows if pd.notna(row.get("best_lap"))]
    fastest = min(fastest_candidates, key=lambda row: row["best_lap"]) if fastest_candidates else None
    gainers = [row for row in rows if (row.get("positions_gained") or 0) > 0]
    biggest_gainer = max(gainers, key=lambda row: row["positions_gained"]) if gainers else None
    retirements = sum(
        1
        for row in rows
        if row.get("status") not in ("Finished", "Unknown")
        and not str(row.get("status", "")).startswith("+")
    )

    return {
        "winner": rows[0]["driver"],
        "podium": " · ".join(row["driver"] for row in rows[:3]),
        "race_laps": rows[0].get("total_laps", 0),
        "fastest_lap_driver": fastest["driver"] if fastest else "—",
        "fastest_lap_time": fastest["best_lap_display"] if fastest else "—",
        "biggest_gainer": biggest_gainer["driver"] if biggest_gainer else "—",
        "biggest_gain": biggest_gainer["positions_gained"] if biggest_gainer else 0,
        "retirements": retirements,
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


# ---------------------------------------------------------------------------
# Helper: empty defaults for all analysis types
# ---------------------------------------------------------------------------
def _empty_race():
    return {
        "alerts": [], "alert_summary": {},
        "race_summary": {},
        "race_projection_accuracy": empty_accuracy(),
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
        session_type — 'Race' or 'Sprint'

    If no parameters are given, auto-detects the latest completed Grand Prix.
    """
    # Render health checks use HEAD; respond fast to avoid expensive loads.
    if request.method == "HEAD":
        return ("", 200)
    # Only completed race sessions are exposed by the public dashboard.
    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)
    if year and round_num:
        session_type = "Sprint" if (session_type or "").lower() == "sprint" else "Race"

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
# ROUTE: JSON API
# ---------------------------------------------------------------------------
@app.route("/api/data")
def api_data():
    """
    Return the same completed-race review as structured JSON.
    """
    if request.method == "HEAD":
        return ("", 200)
    start_time = time.time()

    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)
    if year and round_num:
        session_type = "Sprint" if (session_type or "").lower() == "sprint" else "Race"

    try:
        data = get_dashboard_data(year, round_num, session_type)
    except Exception as exc:
        logger.exception("API request failed")
        return jsonify({"error": str(exc)}), 500

    if data["error"]:
        return jsonify({"error": data["error"]}), 500

    analysis = _run_race_analysis(
        data["laps"],
        session=data.get("session"),
        session_info=data.get("session_info"),
        leaderboard=data.get("leaderboard"),
    )

    elapsed = round(time.time() - start_time, 2)

    return jsonify(
        {
            "session_info": data["session_info"],
            "session_category": "race",
            "leaderboard": [
                {k: v for k, v in d.items() if k != "best_lap"}
                for d in data["leaderboard"]
            ],
            **{k: v for k, v in analysis.items()},
            "load_time": elapsed,
        }
    )


# ---------------------------------------------------------------------------
# ROUTE: Season Form Tracker
# ---------------------------------------------------------------------------
@app.route("/season")
def season_view():
    """Render season-level form and momentum analysis."""
    year = request.args.get("year", type=int)
    window = request.args.get("window", default=5, type=int)
    if request.method == "HEAD":
        return ("", 200)

    if year is None:
        try:
            latest = get_latest_session_info()
            year = latest.get("year")
        except Exception:
            year = None

    if year is None:
        year = datetime.datetime.now().year

    requested_key = (year, window)
    warm_state = _read_season_warm_cache()

    if warm_state["key"] == requested_key and warm_state["data"] is not None:
        return _render_season_page(warm_state["data"], year=year, window=window)

    if warm_state["key"] == requested_key and warm_state["error"]:
        return _render_season_page(year=year, window=window, error=warm_state["error"], status_code=500)

    if not warm_state["in_progress"] or warm_state["key"] != requested_key:
        _start_season_warmup(year, window)

    return _render_season_page(
        year=year,
        window=window,
        loading=True,
        error="Building season form data. This can take a little longer on Render while recent qualifying and race results are loaded.",
    )


# ---------------------------------------------------------------------------
# ROUTE: Circuit Intelligence
# ---------------------------------------------------------------------------
@app.route("/circuit")
def circuit_view():
    """Render a circuit intelligence page for a selected round."""
    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    if request.method == "HEAD":
        return ("", 200)

    if year is None or round_num is None:
        try:
            latest = get_latest_session_info()
            year = year or latest.get("year")
            round_num = round_num or latest.get("round_number")
        except Exception:
            year = year or datetime.datetime.now().year
            round_num = round_num or 1

    requested_key = (year, round_num)
    warm_state = _read_circuit_warm_cache()

    if warm_state["key"] == requested_key and warm_state["data"] is not None:
        return _render_circuit_page(warm_state["data"], year=year, round_num=round_num)

    if warm_state["key"] == requested_key and warm_state["error"]:
        return _render_circuit_page(year=year, round_num=round_num, error=warm_state["error"], status_code=500)

    if not warm_state["in_progress"] or warm_state["key"] != requested_key:
        _start_circuit_warmup(year, round_num)

    return _render_circuit_page(
        year=year,
        round_num=round_num,
        loading=True,
        error="Building circuit intelligence. This can take a little longer on Render while recent race and qualifying history is loaded.",
    )


# ---------------------------------------------------------------------------
# ROUTE: Driver Intelligence
# ---------------------------------------------------------------------------
@app.route("/driver")
def driver_view():
    """Render a driver-focused season page."""
    year = request.args.get("year", type=int)
    driver = request.args.get("driver", default=None, type=str)
    window = request.args.get("window", default=5, type=int)
    if request.method == "HEAD":
        return ("", 200)

    if year is None:
        try:
            latest = get_latest_session_info()
            year = latest.get("year")
        except Exception:
            year = None

    if year is None:
        year = datetime.datetime.now().year

    requested_key = (year, driver or "", window)
    warm_state = _read_driver_warm_cache()

    if warm_state["key"] == requested_key and warm_state["data"] is not None:
        loaded_driver = driver or warm_state["data"].get("meta", {}).get("driver")
        return _render_driver_page(warm_state["data"], year=year, driver=loaded_driver, window=window)

    if warm_state["key"] == requested_key and warm_state["error"]:
        return _render_driver_page(year=year, driver=driver, window=window, error=warm_state["error"], status_code=500)

    if not warm_state["in_progress"] or warm_state["key"] != requested_key:
        _start_driver_warmup(year, driver, window)

    return _render_driver_page(
        year=year,
        driver=driver,
        window=window,
        loading=True,
        error="Building driver intelligence. This can take a little longer on Render while recent qualifying and race results are loaded.",
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
