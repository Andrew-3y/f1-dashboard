"""
data_handler.py — FastF1 Data Fetching & Processing Module
==========================================================

This module is responsible for ALL data operations:
  1. Figuring out which F1 session is the "latest available"
  2. Loading that session's lap data via FastF1
  3. Building a clean leaderboard (positions, lap times, gaps)
  4. Caching results so repeated page loads are fast

KEY CONCEPTS FOR BEGINNERS:
  - FastF1 is a free Python library that pulls telemetry & timing
    data from the official F1 API.  It stores heavy files in a local
    cache directory so the second fetch is almost instant.
  - A "session" is one on-track activity: Practice 1/2/3, Qualifying,
    Sprint, or Race.
  - Lap times come back as pandas Timedelta objects; we convert them
    to human-readable strings like "1:23.456".
"""

import datetime
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import fastf1
import pandas as pd

# ---------------------------------------------------------------------------
# Logging — lets us see what's happening in Render's log viewer
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastF1 cache setup
# ---------------------------------------------------------------------------
# Render gives us an ephemeral filesystem. Use the OS temp directory by
# default so this also works on Windows, where "/tmp" is not writable.
DEFAULT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "fastf1_cache")
CACHE_DIR = os.environ.get("FASTF1_CACHE", DEFAULT_CACHE_DIR)


def _get_session_schedule(year):
    """Return sprint-aware schedule metadata, or ``None`` when unavailable.

    Ergast is useful as a calendar fallback but does not describe historic
    sprint sessions. It must therefore never be used to reject a requested
    session as unscheduled.
    """
    for backend in ("fastf1", "f1timing"):
        try:
            schedule = fastf1.get_event_schedule(
                year,
                include_testing=False,
                backend=backend,
            )
            if schedule is not None and not schedule.empty:
                return schedule
        except Exception as exc:
            logger.warning("%s schedule unavailable for %s: %s", backend, year, exc)
    return None
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)


# ---------------------------------------------------------------------------
# Helper: convert a pandas Timedelta to a readable lap-time string
# ---------------------------------------------------------------------------
def format_laptime(td):
    """
    Convert a pandas Timedelta (or NaT) into 'M:SS.mmm'.

    Examples
    --------
    >>> import pandas as pd
    >>> format_laptime(pd.Timedelta(minutes=1, seconds=23, milliseconds=456))
    '1:23.456'
    >>> format_laptime(pd.NaT)
    'N/A'
    """
    if pd.isna(td):
        return "N/A"
    total_seconds = td.total_seconds()
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:06.3f}"


# ---------------------------------------------------------------------------
# Helper: convert a gap (float seconds) to a display string
# ---------------------------------------------------------------------------
def format_gap(seconds):
    """
    Format a gap in seconds for the leaderboard.

    Parameters
    ----------
    seconds : float or None
        Gap to the leader, in seconds.

    Returns
    -------
    str
        'LEADER' for 0 / None, otherwise '+X.XXXs'.
    """
    if seconds is None or seconds == 0:
        return "LEADER"
    return f"+{seconds:.3f}s"


# ---------------------------------------------------------------------------
# Detect the latest completed Grand Prix
# ---------------------------------------------------------------------------
def get_latest_session_info():
    """
    Return the most recent Grand Prix that is safely past its finish window.

    The dashboard is intentionally post-race only. A four-hour buffer after
    the scheduled race start avoids exposing an active race or a result that
    is still being finalized by the upstream data providers.

    Returns
    -------
    dict
        Keys: year, round_number, event_name, session_type
        Example: {'year': 2025, 'round_number': 3,
                  'event_name': 'Australian Grand Prix',
                  'session_type': 'Race'}

    Raises
    ------
    RuntimeError
        If no completed session can be found (e.g. off-season).

    If no completed Grand Prix exists in the current season, the previous
    season is checked as a fallback.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    year = now.year

    for attempt_year in [year, year - 1]:
        try:
            schedule = _get_session_schedule(attempt_year)
            if schedule is None:
                continue
        except Exception as exc:
            logger.warning("Could not load %d schedule: %s", attempt_year, exc)
            continue

        # Walk events newest-first
        for _, event in schedule.iloc[::-1].iterrows():
            race_start = None
            for idx in range(1, 6):
                session_name = event.get(f"Session{idx}")
                session_date = event.get(f"Session{idx}DateUtc")

                if (
                    pd.isna(session_name)
                    or pd.isna(session_date)
                    or str(session_name).lower() != "race"
                ):
                    continue

                session_ts = pd.Timestamp(session_date)
                if session_ts.tzinfo is None:
                    session_ts = session_ts.tz_localize("UTC")
                else:
                    session_ts = session_ts.tz_convert("UTC")

                race_start = session_ts
                break

            if race_start is not None and race_start + pd.Timedelta(hours=4) <= pd.Timestamp(now):
                return {
                    "year": attempt_year,
                    "round_number": int(event["RoundNumber"]),
                    "event_name": event["EventName"],
                    "session_type": "Race",
                }

    raise RuntimeError("No completed Grand Prix is available yet.")


# ---------------------------------------------------------------------------
# Load session lap data (with in-memory caching)
# ---------------------------------------------------------------------------
# We cache the last result in a module-level dict so that multiple
# requests within the same Render wake cycle don't re-download.
_session_cache = {}
_session_cache_lock = threading.RLock()
_session_load_locks = {}

SUPPORTED_SESSION_TYPES = (
    "Practice 1",
    "Practice 2",
    "Practice 3",
    "Qualifying",
    "Sprint Qualifying",
    "Sprint",
    "Race",
)


def normalize_session_type(session_type):
    """Return a supported public session name, or ``None`` for an invalid one."""
    normalized = str(session_type or "").strip().lower()
    aliases = {
        "practice": "Practice 1",
        "practice 1": "Practice 1",
        "fp1": "Practice 1",
        "practice 2": "Practice 2",
        "fp2": "Practice 2",
        "practice 3": "Practice 3",
        "fp3": "Practice 3",
        "qualifying": "Qualifying",
        "q": "Qualifying",
        "sprint qualifying": "Sprint Qualifying",
        "sprint shootout": "Sprint Qualifying",
        "sq": "Sprint Qualifying",
        "sprint": "Sprint",
        "s": "Sprint",
        "race": "Race",
        "r": "Race",
    }
    return aliases.get(normalized)


def is_session_cached(year, round_number, session_type, include_laps=True):
    """Return whether a completed session is already resident in memory."""
    normalized = normalize_session_type(session_type) or session_type
    with _session_cache_lock:
        return (year, round_number, normalized, bool(include_laps)) in _session_cache


def load_session(year, round_number, session_type, include_laps=True):
    """
    Load a FastF1 session and return its lap data as a DataFrame.

    Parameters
    ----------
    year : int
    round_number : int
    session_type : str   ('Race', 'Qualifying', 'Sprint', etc.)
    include_laps : bool
        Load lap-by-lap timing only when the caller needs it. Result-only
        pages use the official classification without downloading this much
        larger dataset.

    Returns
    -------
    tuple (fastf1.core.Session, pandas.DataFrame)
        The session object and its laps DataFrame.

    Notes
    -----
    - First call downloads data (~10-30 s depending on session).
    - Subsequent calls with the same arguments return instantly from cache.
    """
    session_type = normalize_session_type(session_type) or session_type
    cache_key = (year, round_number, session_type, bool(include_laps))
    with _session_cache_lock:
        cached_session = _session_cache.get(cache_key)
    if cached_session is not None:
        logger.info("Returning cached session for %s", cache_key)
        return cached_session

    with _session_cache_lock:
        session_lock = _session_load_locks.setdefault(cache_key, threading.Lock())

    # Several pages can ask for the same completed session while warming. A
    # per-session lock means one FastF1 request is shared instead of repeated.
    with session_lock:
        with _session_cache_lock:
            cached_session = _session_cache.get(cache_key)
        if cached_session is not None:
            logger.info("Returning cached session for %s", cache_key)
            return cached_session

        logger.info("Loading session: %d Round %d %s …", year, round_number, session_type)

        # Map friendly names to FastF1's expected identifiers
        session_map = {
            "Race": "R",
            "Qualifying": "Q",
            "Sprint Qualifying": "SQ",
            "Sprint": "S",
            "Practice": "FP1",
            "Practice 1": "FP1",
            "Practice 2": "FP2",
            "Practice 3": "FP3",
        }
        identifier = session_map.get(session_type, session_type)

        session = fastf1.get_session(year, round_number, identifier)
        session.load(
            laps=include_laps,
            telemetry=False,   # skip heavy telemetry to stay within memory
            weather=False,
            messages=False,
        )

        laps = session.laps if include_laps else pd.DataFrame()
        with _session_cache_lock:
            _session_cache[cache_key] = (session, laps)
        return session, laps


def load_sessions_concurrently(session_requests, max_workers=3):
    """Load independent completed sessions concurrently without losing errors.

    ``session_requests`` contains ``(year, round, session_type, include_laps)``
    tuples. The returned mapping keeps each request's result or exception so
    callers can safely withhold only the unavailable session.
    """
    requests = list(dict.fromkeys(session_requests))
    if not requests:
        return {}

    workers = min(max(int(max_workers), 1), len(requests))
    loaded = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fastf1-result") as executor:
        futures = {
            executor.submit(load_session, year, round_number, session_type, include_laps): request_key
            for request_key in requests
            for year, round_number, session_type, include_laps in [request_key]
        }
        for future in as_completed(futures):
            request_key = futures[future]
            try:
                loaded[request_key] = future.result()
            except Exception as exc:
                loaded[request_key] = exc
    return loaded


# ---------------------------------------------------------------------------
# Build a leaderboard from the laps DataFrame
# ---------------------------------------------------------------------------
def _valid_laps(df):
    """Return laps that are safe to use for timing/classification display."""
    valid = df.dropna(subset=["LapTime"]).copy()
    if "Deleted" in valid.columns:
        deleted_mask = valid["Deleted"].eq(True)
        valid = valid[~deleted_mask]
    return valid


def _official_best_td(result_row):
    """Return the driver's official best qualifying lap from session results."""
    for col in ("Q3", "Q2", "Q1"):
        if col in result_row and pd.notna(result_row[col]):
            return result_row[col]
    return pd.NaT


def _qualifying_classification_time(result_row):
    """Return the knockout segment and official time used for classification."""
    for segment in ("Q3", "Q2", "Q1"):
        if segment in result_row and pd.notna(result_row[segment]):
            return segment, result_row[segment]
    return None, pd.NaT


def _session_results_rows(session):
    """Return official session result rows sorted by official position."""
    if session is None:
        return pd.DataFrame()

    try:
        results = session.results
    except Exception:
        return pd.DataFrame()

    if results is None or results.empty:
        return pd.DataFrame()

    rows = results.reset_index()
    if "DriverNumber" not in rows.columns:
        rows = rows.rename(columns={rows.columns[0]: "DriverNumber"})

    if "Position" not in rows.columns:
        return pd.DataFrame()

    rows = rows.dropna(subset=["Position"]).copy()
    if rows.empty:
        return pd.DataFrame()

    rows["Position"] = rows["Position"].astype(int)
    return rows.sort_values("Position").reset_index(drop=True)


def _final_elapsed_time(driver_laps):
    """Return a driver's final classified elapsed race time from lap data."""
    if driver_laps is None or driver_laps.empty or "Time" not in driver_laps.columns:
        return pd.NaT

    timed = driver_laps.dropna(subset=["Time"]).sort_values("LapNumber")
    if timed.empty:
        return pd.NaT
    return timed.iloc[-1]["Time"]


def _official_race_gap(row, leader_time, leader_laps, driver_laps_count, final_elapsed_time, leader_elapsed_time):
    """Return a reliable race gap from FastF1 session results.

    FastF1 race result rows can expose `Time` in two different ways:
      - winner: total elapsed race time
      - finishers behind: often already the gap to the winner

    If a non-winning driver's `Time` is smaller than the winner's total race
    time, treat it as a direct gap. Otherwise, treat it as an elapsed finish
    time and subtract the leader's elapsed time.
    """
    if int(row["Position"]) == 1:
        return 0.0, "LEADER"

    if (
        pd.notna(leader_laps)
        and pd.notna(driver_laps_count)
        and driver_laps_count < leader_laps
    ):
        lap_deficit = int(leader_laps - driver_laps_count)
        if lap_deficit > 0:
            suffix = "Lap" if lap_deficit == 1 else "Laps"
            return None, f"+{lap_deficit} {suffix}"

    if pd.notna(final_elapsed_time) and pd.notna(leader_elapsed_time):
        elapsed_gap = final_elapsed_time.total_seconds() - leader_elapsed_time.total_seconds()
        if elapsed_gap >= 0:
            elapsed_gap = round(elapsed_gap, 3)
            return elapsed_gap, format_gap(elapsed_gap)

    result_time = row.get("Time")
    if pd.isna(result_time):
        status = row.get("Status")
        classified = row.get("ClassifiedPosition")
        display = status if pd.notna(status) and status else (classified if pd.notna(classified) else "N/A")
        return None, display

    result_seconds = result_time.total_seconds()
    if pd.isna(leader_time):
        return round(result_seconds, 3), format_gap(result_seconds)

    leader_seconds = leader_time.total_seconds()
    if 0 <= result_seconds < leader_seconds:
        gap_seconds = result_seconds
    else:
        gap_seconds = result_seconds - leader_seconds

    if gap_seconds < 0:
        gap_seconds = abs(gap_seconds)

    gap_seconds = round(gap_seconds, 3)
    return gap_seconds, format_gap(gap_seconds)


def build_leaderboard(laps, session_type="Race", session=None):
    """
    Build a sorted leaderboard.

    For RACE sessions:
        - Sorted by actual finishing position (from the Position column
          on each driver's final lap).
        - Gap shown as cumulative race-time difference to the leader
          (using the 'Time' column = total elapsed race time per lap).

    For QUALIFYING / PRACTICE sessions:
        - Sorted by best single lap time (fastest lap = P1).
        - Gap shown as delta to the fastest lap.

    Parameters
    ----------
    laps : pandas.DataFrame
        The laps DataFrame from a loaded FastF1 session.
    session_type : str
        'Race', 'Qualifying', 'Sprint', 'Practice', etc.

    Returns
    -------
    list[dict]
        Each dict has keys:
            position, driver, team, best_lap, best_lap_display,
            gap_seconds, gap_display, total_laps
    """
    if laps.empty:
        return []

    normalized_type = normalize_session_type(session_type)

    if normalized_type in ("Race", "Sprint"):
        return _build_race_leaderboard(session, laps)
    elif normalized_type in ("Qualifying", "Sprint Qualifying"):
        return _build_quali_leaderboard(session, laps)
    elif normalized_type and normalized_type.startswith("Practice"):
        return _build_practice_leaderboard(laps)

    return []


def _build_race_leaderboard(session, laps):
    """
    Race leaderboard: prefer FastF1's official session results.
    """
    result_rows = _session_results_rows(session)
    if result_rows.empty:
        return []

    leader_time = result_rows.iloc[0]["Time"] if "Time" in result_rows.columns else pd.NaT
    valid_laps = _valid_laps(laps)
    classified_laps = laps.dropna(subset=["LapNumber"]).copy() if not laps.empty else pd.DataFrame()
    leader_row = result_rows.iloc[0]
    leader_driver = leader_row.get("Abbreviation") or leader_row.get("BroadcastName") or leader_row.get("DriverNumber")
    leader_driver_laps = classified_laps[classified_laps["Driver"] == leader_driver]
    leader_elapsed_time = _final_elapsed_time(leader_driver_laps)
    leader_laps = int(leader_row["Laps"]) if pd.notna(leader_row.get("Laps")) else int(leader_driver_laps["LapNumber"].max()) if not leader_driver_laps.empty else None

    leaderboard = []
    for _, row in result_rows.iterrows():
        driver = row.get("Abbreviation") or row.get("BroadcastName") or row.get("DriverNumber")
        driver_laps = valid_laps[valid_laps["Driver"] == driver]
        classified_driver_laps = classified_laps[classified_laps["Driver"] == driver]
        best_lap = driver_laps["LapTime"].min()
        driver_laps_count = int(row["Laps"]) if pd.notna(row.get("Laps")) else int(classified_driver_laps["LapNumber"].max()) if not classified_driver_laps.empty else None
        final_elapsed_time = _final_elapsed_time(classified_driver_laps)
        gap_seconds, gap_display = _official_race_gap(
            row,
            leader_time,
            leader_laps,
            driver_laps_count,
            final_elapsed_time,
            leader_elapsed_time,
        )

        grid_position = row.get("GridPosition")
        grid_position = int(grid_position) if pd.notna(grid_position) else None
        positions_gained = (
            grid_position - int(row["Position"])
            if grid_position is not None and grid_position > 0
            else None
        )
        status = row.get("Status")
        status = str(status) if pd.notna(status) and status else "Unknown"
        points = row.get("Points")
        points = float(points) if pd.notna(points) else 0.0

        leaderboard.append(
            {
                "position": int(row["Position"]),
                "driver": driver,
                "team": row["TeamName"] if pd.notna(row.get("TeamName")) else "Unknown",
                "best_lap": best_lap,
                "best_lap_display": format_laptime(best_lap),
                "gap_seconds": gap_seconds,
                "gap_display": gap_display,
                "total_laps": int(row["Laps"]) if pd.notna(row.get("Laps")) else int(driver_laps["LapNumber"].max()) if not driver_laps.empty else 0,
                "grid_position": grid_position,
                "positions_gained": positions_gained,
                "status": status,
                "points": points,
            }
        )

    return leaderboard


def _build_quali_leaderboard(session, laps):
    """
    Qualifying leaderboard: prefer FastF1's official session results.
    """
    result_rows = _session_results_rows(session)
    if result_rows.empty:
        return _build_practice_leaderboard(laps)

    leaderboard = []
    pole_segment, pole_time = _qualifying_classification_time(result_rows.iloc[0])
    valid_laps = _valid_laps(laps)

    for _, row in result_rows.iterrows():
        classification_segment, best_lap = _qualifying_classification_time(row)
        driver = row.get("Abbreviation") or row.get("BroadcastName") or row.get("DriverNumber")
        driver_laps = valid_laps[valid_laps["Driver"] == driver]
        gap = (
            best_lap.total_seconds() - pole_time.total_seconds()
            if (
                classification_segment == pole_segment == "Q3"
                and pd.notna(best_lap)
                and pd.notna(pole_time)
            )
            else None
        )
        status = row.get("Status")
        no_time_label = str(status) if pd.notna(status) and str(status).strip() else "NO TIME"
        segment_display = classification_segment or no_time_label

        leaderboard.append(
            {
                "position": int(row["Position"]),
                "driver": driver,
                "team": row["TeamName"] if pd.notna(row.get("TeamName")) else "Unknown",
                "best_lap": best_lap,
                "best_lap_display": format_laptime(best_lap),
                "gap_seconds": round(gap, 3) if gap is not None else None,
                "gap_display": "LEADER" if gap == 0 else (format_gap(gap) if gap is not None else segment_display),
                "classification_segment": classification_segment,
                "total_laps": (
                    int(row["Laps"])
                    if pd.notna(row.get("Laps"))
                    else int(driver_laps["LapNumber"].max()) if not driver_laps.empty else 0
                ),
            }
        )

    return leaderboard


def _build_practice_leaderboard(laps):
    """
    Practice leaderboard: sort by fastest single lap time.
    """
    valid_laps = _valid_laps(laps)
    total_laps = (
        laps.dropna(subset=["LapNumber"])
        .groupby("Driver")
        .agg(TotalLaps=("LapNumber", "nunique"))
    )
    quicklaps = (
        valid_laps.groupby("Driver")
        .agg(
            BestLap=("LapTime", "min"),
            Team=("Team", "first"),
        )
        .join(total_laps, how="left")
        .dropna(subset=["BestLap"])
        .sort_values("BestLap")
        .reset_index()
    )

    if quicklaps.empty:
        return []

    leader_time = quicklaps.iloc[0]["BestLap"].total_seconds()

    leaderboard = []
    for pos, (_, row) in enumerate(quicklaps.iterrows(), start=1):
        gap = row["BestLap"].total_seconds() - leader_time
        leaderboard.append(
            {
                "position": pos,
                "driver": row["Driver"],
                "team": row["Team"],
                "best_lap": row["BestLap"],
                "best_lap_display": format_laptime(row["BestLap"]),
                "gap_seconds": round(gap, 3),
                "gap_display": format_gap(gap),
                "total_laps": int(row["TotalLaps"]),
            }
        )

    return leaderboard


def validate_session_data(session, laps, leaderboard, session_type):
    """Validate source-derived session data before it is shown to a user.

    This checks structural integrity, not the sporting record itself: FastF1
    remains the source of truth for the raw timing and classification data.
    """
    normalized_type = normalize_session_type(session_type)
    rows = leaderboard or []
    errors = []

    if normalized_type is None:
        errors.append("unsupported session type")
    if session is None:
        errors.append("session was not loaded")
    if laps is None or getattr(laps, "empty", True):
        errors.append("no session laps were loaded")
    if not rows:
        errors.append("no leaderboard rows were produced")

    positions = [row.get("position") for row in rows]
    expected_positions = list(range(1, len(rows) + 1))
    if positions and positions != expected_positions:
        errors.append("leaderboard positions are not a contiguous official order")

    for row in rows:
        if not row.get("driver"):
            errors.append("leaderboard contains a row without a driver")
            break
        gap = row.get("gap_seconds")
        if gap is not None and (pd.isna(gap) or float(gap) < 0):
            errors.append("leaderboard contains an invalid timing gap")
            break

    official_result_sessions = {"Race", "Sprint", "Qualifying", "Sprint Qualifying"}
    if normalized_type in official_result_sessions and session is not None:
        result_rows = _session_results_rows(session)
        official_positions = result_rows["Position"].astype(int).tolist() if not result_rows.empty else []
        if not official_positions:
            errors.append("official session classification is unavailable")
        elif positions != official_positions:
            errors.append("displayed positions do not match the official session classification")

    if normalized_type and normalized_type.startswith("Practice"):
        result_participants = set()
        if session is not None:
            try:
                source_results = session.results
                if source_results is not None and "Abbreviation" in source_results.columns:
                    result_participants = set(source_results["Abbreviation"].dropna().astype(str))
            except Exception:
                result_participants = set()
        displayed_participants = {str(row.get("driver")) for row in rows if row.get("driver")}
        if result_participants and displayed_participants != result_participants:
            errors.append("practice timing does not include every listed session participant")

        lap_times = [row.get("best_lap") for row in rows]
        if any(pd.isna(lap_time) for lap_time in lap_times):
            errors.append("practice leaderboard contains a driver without a timed lap")
        elif lap_times != sorted(lap_times):
            errors.append("practice leaderboard is not sorted by fastest lap")

    if normalized_type in {"Qualifying", "Sprint Qualifying"}:
        for row in rows:
            segment = row.get("classification_segment")
            if row.get("gap_seconds") is not None and segment != "Q3":
                errors.append("qualifying gap compares times from different knockout segments")
                break

    return {
        "passed": not errors,
        "checks": [
            "session loaded",
            "leaderboard order",
            "non-negative timing gaps",
            "official classification match" if normalized_type in official_result_sessions else "fastest-lap order",
        ],
        "errors": errors,
        "source": "FastF1",
    }


# ---------------------------------------------------------------------------
# Get full processed data bundle (used by app.py)
# ---------------------------------------------------------------------------
def _session_is_safely_complete(schedule, round_number, session_type):
    """Return completion state; ``None`` means that session is not scheduled."""
    if schedule is None or schedule.empty:
        return True

    event_rows = schedule[schedule["RoundNumber"].astype(int) == int(round_number)]
    if event_rows.empty:
        return True

    event = event_rows.iloc[0]
    target = normalize_session_type(session_type)
    if target is None:
        return False
    schedule_names = {
        "Practice 1": {"practice 1"},
        "Practice 2": {"practice 2"},
        "Practice 3": {"practice 3"},
        "Qualifying": {"qualifying"},
        "Sprint Qualifying": {"sprint qualifying", "sprint shootout"},
        "Sprint": {"sprint"},
        "Race": {"race"},
    }[target]
    for idx in range(1, 6):
        name = event.get(f"Session{idx}")
        start = event.get(f"Session{idx}DateUtc")
        if pd.isna(name) or pd.isna(start) or str(name).strip().lower() not in schedule_names:
            continue

        start = pd.Timestamp(start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        buffer_hours = 4 if target == "Race" else 2
        now = pd.Timestamp(datetime.datetime.now(datetime.timezone.utc))
        return start + pd.Timedelta(hours=buffer_hours) <= now

    return None


def get_dashboard_data(year=None, round_number=None, session_type=None):
    """
    High-level entry point: fetch, process, and return everything the
    dashboard needs.

    Parameters
    ----------
    year, round_number, session_type : optional
        If all three are provided, load that completed race or sprint.
        Otherwise auto-detect the latest completed Grand Prix.

    Returns
    -------
    dict with keys:
        session_info  — metadata about the session
        leaderboard   — list of driver dicts (see build_leaderboard)
        laps          — raw laps DataFrame used by post-race analysis
        error         — None if everything is fine, else an error string
    """
    try:
        if year and round_number and session_type:
            session_type = normalize_session_type(session_type)
            if session_type is None:
                return {
                    "session_info": None,
                    "leaderboard": [],
                    "session": None,
                    "laps": pd.DataFrame(),
                    "error": "Choose a supported completed session.",
                }
            # Validate the requested round only if the schedule is available.
            # If the schedule request fails, continue and let FastF1 try.
            try:
                schedule = _get_session_schedule(year)
            except Exception as exc:
                logger.warning("Schedule lookup failed for %s: %s", year, exc)
                schedule = None

            if schedule is not None and not schedule.empty:
                try:
                    rounds = schedule["RoundNumber"].astype(int).tolist()
                except Exception:
                    rounds = []
                if rounds and int(round_number) not in rounds:
                    return {
                        "session_info": None,
                        "leaderboard": [],
                        "session": None,
                        "laps": pd.DataFrame(),
                        "error": f"Round {round_number} does not exist in the {year} schedule.",
                    }

                session_complete = _session_is_safely_complete(schedule, round_number, session_type)
                if session_complete is None:
                    # Historic FastF1 schedules can omit sprint labels even
                    # when the underlying session data is available. Let the
                    # source loader decide for these session types instead of
                    # rejecting a real sprint weekend from stale metadata.
                    if session_type not in {"Sprint", "Sprint Qualifying"}:
                        return {
                            "session_info": None,
                            "leaderboard": [],
                            "session": None,
                            "laps": pd.DataFrame(),
                            "error": f"{session_type} is not scheduled for round {round_number} in {year}.",
                        }
                if session_complete is False:
                    return {
                        "session_info": None,
                        "leaderboard": [],
                        "session": None,
                        "laps": pd.DataFrame(),
                        "error": "This dashboard only publishes a session after it has finished.",
                    }

            info = {
                "year": year,
                "round_number": round_number,
                "event_name": f"Round {round_number}",
                "session_type": session_type,
            }
        else:
            info = get_latest_session_info()

        session, laps = load_session(
            info["year"], info["round_number"], info["session_type"]
        )

        # Update event name from loaded session (more accurate)
        info["event_name"] = session.event["EventName"]

        leaderboard = build_leaderboard(laps, session_type=info["session_type"], session=session)
        validation = validate_session_data(session, laps, leaderboard, info["session_type"])
        if not validation["passed"]:
            return {
                "session_info": info,
                "leaderboard": [],
                "session": session,
                "laps": laps,
                "validation": validation,
                "error": "Session data failed integrity checks: " + "; ".join(validation["errors"]),
            }

        return {
            "session_info": info,
            "leaderboard": leaderboard,
            "session": session,
            "laps": laps,
            "validation": validation,
            "error": None,
        }

    except Exception as exc:
        logger.exception("Failed to load dashboard data")
        return {
            "session_info": None,
            "leaderboard": [],
            "session": None,
            "laps": pd.DataFrame(),
            "validation": None,
            "error": str(exc),
        }
