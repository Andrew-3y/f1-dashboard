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

import fastf1
import pandas as pd
import datetime
import os
import logging
from functools import lru_cache

# ---------------------------------------------------------------------------
# Logging — lets us see what's happening in Render's log viewer
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastF1 cache setup
# ---------------------------------------------------------------------------
# Render gives us an ephemeral filesystem.  We store the cache in /tmp
# so it survives across requests within the same dyno wake-up cycle.
CACHE_DIR = os.environ.get("FASTF1_CACHE", "/tmp/fastf1_cache")
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
# Detect the latest completed (or most recent) F1 session
# ---------------------------------------------------------------------------
def get_latest_session_info():
    """
    Walk through the current season's event schedule to find the most
    recent session that has already happened.

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

    HOW IT WORKS:
      1. Load the full event schedule for the current year.
      2. Iterate events in REVERSE order (newest first).
      3. For each event, check session dates (Race → Qualifying → …).
      4. Return the first session whose date is in the past.
      5. If nothing in the current year, fall back to the previous year's
         last race.
    """
    now = datetime.datetime.now()
    year = now.year

    for attempt_year in [year, year - 1]:
        try:
            schedule = fastf1.get_event_schedule(attempt_year, include_testing=False)
        except Exception as exc:
            logger.warning("Could not load %d schedule: %s", attempt_year, exc)
            continue

        # Session columns in priority order (most interesting first)
        session_priority = [
            ("Race", "Session5Date"),
            ("Sprint", "Session4Date"),
            ("Qualifying", "Session3Date"),
            ("Practice 3", "Session3Date"),
            ("Practice 2", "Session2Date"),
            ("Practice 1", "Session1Date"),
        ]

        # Walk events newest-first
        for _, event in schedule.iloc[::-1].iterrows():
            for session_type, date_col in session_priority:
                try:
                    session_date = pd.Timestamp(event[date_col])
                    if pd.isna(session_date):
                        continue
                    # Make comparison timezone-naive
                    if session_date.tz is not None:
                        session_date = session_date.tz_localize(None)
                    if session_date <= pd.Timestamp(now):
                        return {
                            "year": attempt_year,
                            "round_number": int(event["RoundNumber"]),
                            "event_name": event["EventName"],
                            "session_type": session_type.split()[0]
                            if session_type.startswith("Practice")
                            else session_type,
                        }
                except Exception:
                    continue

    raise RuntimeError("No completed F1 session found. It may be the off-season.")


# ---------------------------------------------------------------------------
# Load session lap data (with in-memory caching)
# ---------------------------------------------------------------------------
# We cache the last result in a module-level dict so that multiple
# requests within the same Render wake cycle don't re-download.
_session_cache = {}


def load_session(year, round_number, session_type):
    """
    Load a FastF1 session and return its lap data as a DataFrame.

    Parameters
    ----------
    year : int
    round_number : int
    session_type : str   ('Race', 'Qualifying', 'Sprint', etc.)

    Returns
    -------
    tuple (fastf1.core.Session, pandas.DataFrame)
        The session object and its laps DataFrame.

    Notes
    -----
    - First call downloads data (~10-30 s depending on session).
    - Subsequent calls with the same arguments return instantly from cache.
    """
    cache_key = (year, round_number, session_type)
    if cache_key in _session_cache:
        logger.info("Returning cached session for %s", cache_key)
        return _session_cache[cache_key]

    logger.info("Loading session: %d Round %d %s …", year, round_number, session_type)

    # Map friendly names to FastF1's expected identifiers
    session_map = {
        "Race": "R",
        "Qualifying": "Q",
        "Sprint": "S",
        "Practice": "FP1",
        "Practice 1": "FP1",
        "Practice 2": "FP2",
        "Practice 3": "FP3",
    }
    identifier = session_map.get(session_type, session_type)

    session = fastf1.get_session(year, round_number, identifier)
    session.load(
        telemetry=False,   # skip heavy telemetry to stay within memory
        weather=False,
        messages=False,
    )

    laps = session.laps
    _session_cache[cache_key] = (session, laps)
    return session, laps


# ---------------------------------------------------------------------------
# Build a leaderboard from the laps DataFrame
# ---------------------------------------------------------------------------
def build_leaderboard(laps, session_type="Race"):
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

    is_race = session_type.lower() in ("race", "sprint")

    if is_race:
        return _build_race_leaderboard(laps)
    else:
        return _build_quali_leaderboard(laps)


def _build_race_leaderboard(laps):
    """
    Race leaderboard: use the official Position from each driver's
    last recorded lap, and calculate gaps from cumulative race time.
    """
    # For each driver, grab their last lap entry
    last_laps_idx = laps.groupby("Driver")["LapNumber"].idxmax()
    last_laps = laps.loc[last_laps_idx].copy()

    # Drop drivers with no position data
    last_laps = last_laps.dropna(subset=["Position"])
    last_laps["Position"] = last_laps["Position"].astype(int)
    last_laps = last_laps.sort_values("Position").reset_index(drop=True)

    if last_laps.empty:
        return []

    # Get cumulative race time for each driver (the 'Time' column in FastF1
    # represents total elapsed time at the END of that lap)
    # Use it to calculate gaps to the leader
    leader_total_time = None
    if "Time" in last_laps.columns:
        leader_row = last_laps.iloc[0]
        if pd.notna(leader_row["Time"]):
            leader_total_time = leader_row["Time"].total_seconds()

    leaderboard = []
    for _, row in last_laps.iterrows():
        # Best lap for display (fastest individual lap this driver set)
        driver_laps = laps[laps["Driver"] == row["Driver"]]
        best_lap = driver_laps["LapTime"].min()

        # Gap calculation
        if leader_total_time and "Time" in row and pd.notna(row["Time"]):
            gap = row["Time"].total_seconds() - leader_total_time
        else:
            gap = 0 if row["Position"] == 1 else None

        leaderboard.append(
            {
                "position": int(row["Position"]),
                "driver": row["Driver"],
                "team": row["Team"] if pd.notna(row["Team"]) else "Unknown",
                "best_lap": best_lap,
                "best_lap_display": format_laptime(best_lap),
                "gap_seconds": round(gap, 3) if gap is not None else 0,
                "gap_display": format_gap(gap),
                "total_laps": int(row["LapNumber"]),
            }
        )

    return leaderboard


def _build_quali_leaderboard(laps):
    """
    Qualifying / Practice leaderboard: sort by fastest single lap time.
    """
    quicklaps = (
        laps.groupby("Driver")
        .agg(
            BestLap=("LapTime", "min"),
            Team=("Team", "first"),
            TotalLaps=("LapNumber", "max"),
        )
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


# ---------------------------------------------------------------------------
# Get full processed data bundle (used by app.py)
# ---------------------------------------------------------------------------
def get_dashboard_data(year=None, round_number=None, session_type=None):
    """
    High-level entry point: fetch, process, and return everything the
    dashboard needs.

    Parameters
    ----------
    year, round_number, session_type : optional
        If all three are provided, load that specific session.
        Otherwise auto-detect the latest session.

    Returns
    -------
    dict with keys:
        session_info  — metadata about the session
        leaderboard   — list of driver dicts (see build_leaderboard)
        laps          — raw laps DataFrame (used by anomaly & predictor)
        error         — None if everything is fine, else an error string
    """
    try:
        if year and round_number and session_type:
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

        leaderboard = build_leaderboard(laps, session_type=info["session_type"])

        return {
            "session_info": info,
            "leaderboard": leaderboard,
            "laps": laps,
            "error": None,
        }

    except Exception as exc:
        logger.exception("Failed to load dashboard data")
        return {
            "session_info": None,
            "leaderboard": [],
            "laps": pd.DataFrame(),
            "error": str(exc),
        }
