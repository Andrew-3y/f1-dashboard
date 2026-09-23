"""
app.py — Flask Application (Main Entry Point)
==============================================

This is the file that Render will run. It serves a Formula 1 dashboard
built from completed race data.

HOW IT WORKS (on-demand architecture):
  1. User opens the URL → Flask receives a GET request.
  2. Flask calls data_handler to fetch the latest completed Grand Prix.
  3. It builds the official classification and factual post-race summary.
  4. The dashboard data is injected into an HTML template and returned.
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
from data_handler import get_dashboard_data, get_latest_session_info, is_session_cached, normalize_session_type
from season_form import build_season_form, empty_season_form, get_cached_season_form
from circuit_intel import build_circuit_intelligence, empty_circuit_intelligence, get_cached_circuit_intelligence
from driver_intel import build_driver_intelligence, empty_driver_intelligence, get_cached_driver_intelligence

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


def _base_dashboard_context():
    """Return a stable context for all dashboard renders."""
    return {
        "error": None,
        "session_info": None,
        "session_category": "race",
        "validation": None,
        "leaderboard": [],
        "load_time": 0,
        "session_summary": {},
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
  <title>F1 Race Dashboard</title>
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
    <h1>F1 Race Dashboard</h1>
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
_latest_warmup_lock = threading.Lock()
_latest_warmup_in_progress = False

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
    """Warm up a requested completed session in a background thread."""
    with _warm_lock:
        requested_key = (year, round_num, session_type)
        # One warmup owns this shared cache at a time. Starting a second,
        # different warmup used to replace the first key mid-load and could
        # leave the dashboard retrying forever after a service restart.
        if _warm_cache["in_progress"]:
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
            logger.info("Dashboard data prepared for %s", (year, round_num, session_type))
            session_category = _session_category(data.get("session_info", {}).get("session_type"))
            if data.get("error"):
                analysis = _base_dashboard_context()
            elif session_category == "race":
                analysis = _run_race_analysis(
                    data.get("leaderboard"),
                    session=data.get("session"),
                    laps=data.get("laps"),
                )
            else:
                analysis = {"session_summary": _build_session_summary(data.get("leaderboard"))}
            logger.info("Dashboard analysis prepared for %s", (year, round_num, session_type))
            with _warm_lock:
                _warm_cache.update(
                    {
                        "key": (year, round_num, session_type),
                        "data": data,
                        "analysis": analysis,
                        "session_category": session_category,
                        "error": data.get("error"),
                        "updated_at": time.time(),
                    }
                )
            logger.info("Dashboard warmup ready for %s", (year, round_num, session_type))
        except Exception as exc:
            logger.exception("Dashboard warmup failed for %s", (year, round_num, session_type))
            with _warm_lock:
                _warm_cache["error"] = str(exc)
        finally:
            with _warm_lock:
                _warm_cache["in_progress"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _render_warmup(session_type=None):
    """Render the warmup state for a requested completed session."""
    return _render_dashboard(
        session_category=_session_category(session_type),
        error="WARMUP: Loading completed session data. This can take ~30s on a cold start. The page will refresh automatically.",
    )


def _start_latest_warmup():
    """Resolve and preload the latest completed race without an ambiguous cache key."""
    global _latest_warmup_in_progress
    with _latest_warmup_lock:
        if _latest_warmup_in_progress:
            return
        _latest_warmup_in_progress = True

    def _worker():
        global _latest_warmup_in_progress
        try:
            info = get_latest_session_info()
            if not info:
                raise RuntimeError("No completed Grand Prix is available to preload.")
            _start_warmup(info["year"], info["round_number"], info["session_type"])
        except Exception:
            logger.exception("Startup latest-session resolution failed")
        finally:
            with _latest_warmup_lock:
                _latest_warmup_in_progress = False

    threading.Thread(target=_worker, daemon=True).start()


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
    """Warm up circuit history in a background thread."""
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
    payload_meta["year"] = year if year is not None else payload_meta.get("year")
    payload_meta["round_number"] = round_num if round_num is not None else payload_meta.get("round_number")
    return render_template(
        "circuit.html",
        circuit_data=payload,
        circuit_meta=payload_meta,
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
# Helper: build the post-race summary
# ---------------------------------------------------------------------------
def _run_race_analysis(leaderboard=None, session=None, laps=None):
    """Build factual headline statistics from the final classification."""
    strategy_rows = _build_strategy_rows(leaderboard, laps=laps)
    return {
        "race_summary": _build_post_race_summary(leaderboard),
        "race_story": _build_race_story(leaderboard, session=session, laps=laps),
        "strategy_rows": strategy_rows,
        "race_progression": _build_race_progression(leaderboard, laps=laps),
        "close_finishes": _build_close_finishes(leaderboard),
        "teammate_battles": _build_teammate_battles(leaderboard),
    }


def _session_category(session_type):
    """Classify a supported session for the correct post-session presentation."""
    normalized = normalize_session_type(session_type)
    if normalized in {"Race", "Sprint"}:
        return "race"
    if normalized in {"Qualifying", "Sprint Qualifying"}:
        return "qualifying"
    return "practice"


def _build_session_summary(leaderboard):
    """Build factual summary cards for non-race completed sessions."""
    rows = leaderboard or []
    fastest = min(
        (row for row in rows if pd.notna(row.get("best_lap"))),
        key=lambda row: row["best_lap"],
        default=None,
    )
    return {
        "leader": rows[0].get("driver", "—") if rows else "—",
        "top_three": " · ".join(row.get("driver", "—") for row in rows[:3]) if rows else "—",
        "fastest_lap_driver": fastest.get("driver", "—") if fastest else "—",
        "fastest_lap_time": fastest.get("best_lap_display", "—") if fastest else "—",
        "timed_drivers": len(rows),
    }


def _is_retirement(status):
    """Return whether an official result status represents a race retirement."""
    normalized = str(status or "").strip().lower()
    if not normalized or normalized in {"finished", "lapped", "classified", "unknown"}:
        return False
    if normalized.startswith("+") or normalized in {"did not start", "did not qualify", "disqualified"}:
        return False

    retirement_markers = (
        "retired",
        "accident",
        "collision",
        "engine",
        "gearbox",
        "transmission",
        "clutch",
        "hydraulic",
        "electrical",
        "brake",
        "suspension",
        "mechanical",
        "damage",
        "overheating",
    )
    return any(marker in normalized for marker in retirement_markers)


def _build_race_story(leaderboard, session=None, laps=None):
    """Build a factual account of recorded race events from timing data."""
    rows = leaderboard or []
    story = {
        "lead_changes": None,
        "leaders": [],
        "safety_cars": None,
        "virtual_safety_cars": None,
        "retirements": [
            {"driver": row.get("driver", "-"), "status": row.get("status", "Retired")}
            for row in rows
            if _is_retirement(row.get("status"))
        ],
    }

    if laps is not None and not getattr(laps, "empty", True):
        if {"LapNumber", "Position", "Driver"}.issubset(laps.columns):
            leader_laps = laps[["LapNumber", "Position", "Driver"]].copy()
            leader_laps["Position"] = pd.to_numeric(leader_laps["Position"], errors="coerce")
            leader_laps = leader_laps[
                leader_laps["LapNumber"].notna()
                & leader_laps["Driver"].notna()
                & (leader_laps["Position"] == 1)
            ].sort_values("LapNumber")
            if not leader_laps.empty:
                lead_sequence = leader_laps.drop_duplicates("LapNumber", keep="last")["Driver"].tolist()
                leaders = []
                for driver in lead_sequence:
                    if not leaders or leaders[-1] != driver:
                        leaders.append(driver)
                story["leaders"] = leaders
                story["lead_changes"] = max(len(leaders) - 1, 0)

    track_status = getattr(session, "track_status", None) if session is not None else None
    if track_status is not None and not getattr(track_status, "empty", True):
        if "Message" in track_status.columns:
            messages = track_status["Message"].fillna("").astype(str).str.strip().str.upper()
            story["safety_cars"] = int((messages == "SCDEPLOYED").sum())
            story["virtual_safety_cars"] = int((messages == "VSCDEPLOYED").sum())
        elif "Status" in track_status.columns:
            status_codes = track_status["Status"].astype(str)
            story["safety_cars"] = int((status_codes == "4").sum())
            story["virtual_safety_cars"] = int((status_codes == "6").sum())

    return story


def _compound_display(compound):
    """Return a compact, readable tyre label from FastF1 compound data."""
    normalized = str(compound or "").strip().upper()
    labels = {
        "SOFT": ("S", "Soft"),
        "MEDIUM": ("M", "Medium"),
        "HARD": ("H", "Hard"),
        "INTERMEDIATE": ("I", "Intermediate"),
        "WET": ("W", "Wet"),
    }
    return labels.get(normalized, ("—", "Unknown"))


def _build_strategy_rows(leaderboard, laps=None):
    """Build each driver's recorded compound sequence from completed lap data.

    FastF1 can split a run into multiple raw stints during early-race
    interruptions even when the tyre compound has not changed. Merging those
    fragments keeps the post-race view factual and legible.
    """
    rows = leaderboard or []
    if laps is None or getattr(laps, "empty", True):
        return []
    required_columns = {"Driver", "Stint", "LapNumber", "Compound"}
    if not required_columns.issubset(laps.columns):
        return []

    strategy_laps = laps.dropna(subset=["Driver", "Stint", "LapNumber"]).copy()
    if "FastF1Generated" in strategy_laps.columns:
        generated_mask = strategy_laps["FastF1Generated"].eq(True)
        strategy_laps = strategy_laps[~generated_mask]

    stints_by_driver = {}
    for driver, driver_laps in strategy_laps.groupby("Driver", sort=False):
        stints = []
        for _, stint_laps in driver_laps.groupby("Stint", sort=True):
            lap_numbers = pd.to_numeric(stint_laps["LapNumber"], errors="coerce").dropna()
            if lap_numbers.empty:
                continue

            compounds = stint_laps["Compound"].dropna().astype(str)
            compound = next(
                (value for value in compounds if value.strip().upper() not in {"", "UNKNOWN", "NAN"}),
                "Unknown",
            )
            short_compound, compound_name = _compound_display(compound)
            first_lap = int(lap_numbers.min())
            last_lap = int(lap_numbers.max())
            stint_starts_on_new_compound = not stints or stints[-1]["compound_code"] != short_compound

            if stint_starts_on_new_compound:
                stints.append(
                    {
                        "compound_code": short_compound,
                        "compound": compound_name,
                        "lap_count": int(lap_numbers.nunique()),
                        "first_lap": first_lap,
                        "last_lap": last_lap,
                    }
                )
            else:
                previous_stint = stints[-1]
                previous_stint["lap_count"] += int(lap_numbers.nunique())
                previous_stint["last_lap"] = last_lap

        for stint in stints:
            first_lap = stint["first_lap"]
            last_lap = stint["last_lap"]
            stint["lap_range"] = f"L{first_lap}–{last_lap}" if first_lap != last_lap else f"L{first_lap}"
            stint.pop("first_lap")
            stint.pop("last_lap")
        stints_by_driver[str(driver)] = stints

    strategy_rows = []
    for result in rows:
        driver = result.get("driver", "-")
        stints = stints_by_driver.get(driver, [])
        strategy_rows.append(
            {
                "position": result.get("position", "—"),
                "driver": driver,
                "team": result.get("team", "Unknown"),
                "status": result.get("status", "Unknown"),
                "stops": max(len(stints) - 1, 0) if stints else None,
                "stints": stints,
            }
        )
    return strategy_rows


def _build_race_progression(leaderboard, laps=None):
    """Return lap-by-lap classified positions for the selected completed race."""
    rows = leaderboard or []
    empty_progression = {"total_laps": 0, "max_position": max(len(rows), 1), "drivers": []}
    if laps is None or getattr(laps, "empty", True):
        return empty_progression
    required_columns = {"Driver", "LapNumber", "Position"}
    if not required_columns.issubset(laps.columns):
        return empty_progression

    position_laps = laps.dropna(subset=["Driver", "LapNumber", "Position"]).copy()
    if "FastF1Generated" in position_laps.columns:
        generated_mask = position_laps["FastF1Generated"].eq(True)
        position_laps = position_laps[~generated_mask]
    position_laps["LapNumber"] = pd.to_numeric(position_laps["LapNumber"], errors="coerce")
    position_laps["Position"] = pd.to_numeric(position_laps["Position"], errors="coerce")
    position_laps = position_laps.dropna(subset=["LapNumber", "Position"])
    if position_laps.empty:
        return empty_progression

    total_laps = int(position_laps["LapNumber"].max())
    max_position = max(len(rows), int(position_laps["Position"].max()))
    by_driver = {}
    for driver, driver_laps in position_laps.groupby("Driver", sort=False):
        latest_per_lap = (
            driver_laps.sort_values("LapNumber")
            .drop_duplicates("LapNumber", keep="last")
        )
        points = [
            {"lap": int(row.LapNumber), "position": int(row.Position)}
            for row in latest_per_lap.itertuples()
            if 1 <= int(row.Position) <= max_position
        ]
        if points:
            by_driver[str(driver)] = points

    drivers = []
    for result in rows:
        driver = result.get("driver", "-")
        points = by_driver.get(driver, [])
        if not points:
            continue
        drivers.append(
            {
                "driver": driver,
                "team": result.get("team", "Unknown"),
                "grid_position": result.get("grid_position"),
                "final_position": result.get("position"),
                "points": points,
            }
        )

    return {"total_laps": total_laps, "max_position": max_position, "drivers": drivers}


def _build_close_finishes(leaderboard, limit=3):
    """Return the closest adjacent classified finishers by final time gap."""
    rows = leaderboard or []
    close_finishes = []
    for ahead, behind in zip(rows, rows[1:]):
        ahead_gap = ahead.get("gap_seconds")
        behind_gap = behind.get("gap_seconds")
        if pd.isna(ahead_gap) or pd.isna(behind_gap):
            continue

        margin = float(behind_gap) - float(ahead_gap)
        if margin < 0:
            continue
        close_finishes.append(
            {
                "ahead_driver": ahead.get("driver", "—"),
                "ahead_position": ahead.get("position", "—"),
                "behind_driver": behind.get("driver", "—"),
                "behind_position": behind.get("position", "—"),
                "margin_seconds": round(margin, 3),
                "margin_display": f"{margin:.3f}s",
            }
        )

    return sorted(close_finishes, key=lambda item: item["margin_seconds"])[:limit]


def _build_teammate_battles(leaderboard):
    """Pair each team's two classified drivers for the selected race only."""
    teams = {}
    for row in leaderboard or []:
        team = row.get("team")
        driver = row.get("driver")
        position = row.get("position")
        if not team or not driver or position is None:
            continue
        teams.setdefault(str(team), []).append(row)

    battles = []
    for team, drivers in teams.items():
        if len(drivers) != 2:
            continue
        first, second = sorted(drivers, key=lambda row: row["position"])
        battles.append(
            {
                "team": team,
                "first": first,
                "second": second,
                "winner": first.get("driver", "—"),
            }
        )

    return sorted(battles, key=lambda battle: battle["team"])


def _build_post_race_summary(leaderboard):
    """Create headline facts from the official completed-race classification."""
    rows = leaderboard or []
    if not rows:
        return {
            "winner": "—",
            "podium": "—",
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
    retirements = sum(1 for row in rows if _is_retirement(row.get("status")))

    return {
        "winner": rows[0]["driver"],
        "podium": " · ".join(row["driver"] for row in rows[:3]),
        "fastest_lap_driver": fastest["driver"] if fastest else "—",
        "fastest_lap_time": fastest["best_lap_display"] if fastest else "—",
        "biggest_gainer": biggest_gainer["driver"] if biggest_gainer else "—",
        "biggest_gain": biggest_gainer["positions_gained"] if biggest_gainer else 0,
        "retirements": retirements,
    }


# ---------------------------------------------------------------------------
# Helper: empty defaults for all analysis types
# ---------------------------------------------------------------------------
def _empty_race():
    return {
        "race_summary": {},
        "race_story": {},
        "strategy_rows": [],
        "race_progression": {"total_laps": 0, "max_position": 1, "drivers": []},
        "close_finishes": [],
        "teammate_battles": [],
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
        session_type — a supported completed session type

    If no parameters are given, auto-detects the latest completed Grand Prix.
    """
    # Render health checks use HEAD; respond fast to avoid expensive loads.
    if request.method == "HEAD":
        return ("", 200)
    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)
    if year and round_num:
        session_type = normalize_session_type(session_type) or "Race"

    requested_key = (year, round_num, session_type)
    warm_state = _read_warm_cache()

    auto_request = not (year and round_num and session_type)
    logger.info(
        "Dashboard request cache state: auto=%s key=%s cached_key=%s has_data=%s has_analysis=%s in_progress=%s",
        auto_request,
        requested_key,
        warm_state["key"],
        bool(warm_state["data"]),
        bool(warm_state["analysis"]),
        warm_state["in_progress"],
    )
    if (auto_request or warm_state["key"] == requested_key) and warm_state["data"] and warm_state["analysis"]:
        if warm_state["error"]:
            return _render_dashboard(
                session_category=warm_state["session_category"],
                error=warm_state["error"],
            )
        return _render_dashboard(
            error=None,
            session_info=warm_state["data"]["session_info"],
            session_category=warm_state["session_category"],
            validation=warm_state["data"].get("validation"),
            leaderboard=warm_state["data"]["leaderboard"],
            load_time=0,
            **warm_state["analysis"],
        )

    # A session already in FastF1's in-memory cache can be rendered directly.
    # Previously it still went through the background warmup screen and its
    # fixed retry delay even though no network fetch was needed.
    if year and round_num and session_type and is_session_cached(year, round_num, session_type):
        data = get_dashboard_data(year, round_num, session_type)
        if data.get("error"):
            return _render_dashboard(session_category=_session_category(session_type), error=data["error"])
        session_category = _session_category(data["session_info"].get("session_type"))
        analysis = (
            _run_race_analysis(data.get("leaderboard"), session=data.get("session"), laps=data.get("laps"))
            if session_category == "race"
            else {"session_summary": _build_session_summary(data.get("leaderboard"))}
        )
        return _render_dashboard(
            session_info=data["session_info"],
            session_category=session_category,
            validation=data.get("validation"),
            leaderboard=data["leaderboard"],
            load_time=0,
            **analysis,
        )

    # Cold-start warmup path for both auto-detected and explicit requests.
    if warm_state["in_progress"] and warm_state["key"] == requested_key:
        return _render_warmup(session_type)

    if auto_request:
        _start_latest_warmup()
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
    Return the same completed-race dashboard data as structured JSON.
    """
    if request.method == "HEAD":
        return ("", 200)
    start_time = time.time()

    year = request.args.get("year", type=int)
    round_num = request.args.get("round", type=int)
    session_type = request.args.get("session_type", default=None, type=str)
    if year and round_num:
        session_type = normalize_session_type(session_type) or "Race"

    try:
        data = get_dashboard_data(year, round_num, session_type)
    except Exception as exc:
        logger.exception("API request failed")
        return jsonify({"error": str(exc)}), 500

    if data["error"]:
        # The request itself succeeded, but the selected session is not safe
        # to publish (for example, incomplete timing data).  Do not report
        # this as an application failure to API clients or uptime monitors.
        return jsonify({"error": data["error"]}), 422

    session_category = _session_category(data["session_info"].get("session_type"))
    analysis = (
        _run_race_analysis(data.get("leaderboard"), session=data.get("session"), laps=data.get("laps"))
        if session_category == "race"
        else {"session_summary": _build_session_summary(data.get("leaderboard"))}
    )

    elapsed = round(time.time() - start_time, 2)

    return jsonify(
        {
            "session_info": data["session_info"],
            "session_category": session_category,
            "validation": data.get("validation"),
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
    """Render season-level completed-results analysis."""
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

    cached_season = get_cached_season_form(year, window)
    if cached_season is not None:
        return _render_season_page(cached_season, year=year, window=window)

    if not warm_state["in_progress"] or warm_state["key"] != requested_key:
        _start_season_warmup(year, window)

    return _render_season_page(
        year=year,
        window=window,
        loading=True,
        error="Building season form data. This can take a little longer on Render while recent qualifying and race results are loaded.",
    )


# ---------------------------------------------------------------------------
# ROUTE: Circuit History
# ---------------------------------------------------------------------------
@app.route("/circuit")
def circuit_view():
    """Render factual race and qualifying history for a selected round."""
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

    cached_circuit = get_cached_circuit_intelligence(year, round_num)
    if cached_circuit is not None:
        return _render_circuit_page(cached_circuit, year=year, round_num=round_num)

    if not warm_state["in_progress"] or warm_state["key"] != requested_key:
        _start_circuit_warmup(year, round_num)

    return _render_circuit_page(
        year=year,
        round_num=round_num,
        loading=True,
        error="Loading recent race and qualifying history. This can take a little longer on Render on the first request.",
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

    cached_driver = get_cached_driver_intelligence(year, driver, window)
    if cached_driver is not None:
        loaded_driver = driver or cached_driver.get("meta", {}).get("driver")
        return _render_driver_page(cached_driver, year=year, driver=loaded_driver, window=window)

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


def _prewarm_latest_completed_race():
    """Start the latest-race fetch during service startup when enabled.

    This is deliberately opt-in so local development and the test suite do
    not make network calls merely by importing the Flask application.
    """
    _start_latest_warmup()


if os.environ.get("PREWARM_LATEST_SESSION", "").strip().lower() == "true":
    _prewarm_latest_completed_race()


# ---------------------------------------------------------------------------
# Run the app
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # When running locally: python app.py
    # Render will use gunicorn instead (see render.yaml)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
