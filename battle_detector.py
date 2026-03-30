"""
battle_detector.py — On-Track Battle Detection System
=====================================================

WHY THIS MATTERS:
  F1 broadcasting sometimes misses close battles happening further
  down the field.  This module scans the entire grid and identifies
  driver pairs who are:
    - Within a close gap (< 2 seconds)
    - Getting closer lap by lap (gap is shrinking)
    - Likely in a real on-track fight

  This makes the dashboard feel "alive" — like a race control screen
  showing where the action is happening RIGHT NOW.

ALGORITHM:
  1. For each pair of consecutive drivers (by position):
     a. Calculate the current gap.
     b. Check if gap < BATTLE_GAP_THRESHOLD.
     c. Calculate whether the gap is shrinking (closing rate).
     d. Classify the battle intensity: INTENSE / CLOSE / WATCHING.
  2. Sort by intensity and gap (closest battles first).

DIFFERENCE FROM predictor.py:
  - predictor.py asks "WHEN will a driver catch another?" (future)
  - battle_detector.py asks "WHO is fighting RIGHT NOW?" (present)
  - predictor.py focuses on DRS range prediction.
  - battle_detector.py focuses on on-track proximity and drama.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Maximum gap (seconds) to consider two drivers "in a battle"
BATTLE_GAP_THRESHOLD = 2.0

# If within DRS range (< 1.0s), that's a very intense battle
DRS_RANGE = 1.0

# How many recent laps to check for gap trend
LOOKBACK_LAPS = 5

# Maximum number of battles to report
MAX_BATTLES = 10


# ---------------------------------------------------------------------------
# Core: detect all active battles
# ---------------------------------------------------------------------------
def detect_battles(laps):
    """
    Scan the grid and identify active on-track battles.

    Parameters
    ----------
    laps : pandas.DataFrame
        Full laps DataFrame from FastF1.

    Returns
    -------
    list[dict]
        Each dict contains:
            driver_ahead   — abbreviation
            driver_behind  — abbreviation
            team_ahead     — team name
            team_behind    — team name
            gap            — current gap in seconds
            closing_rate   — how fast the gap is shrinking (s/lap)
            intensity      — 'INTENSE' / 'CLOSE' / 'WATCHING'
            drs_active     — whether the gap is within DRS range
            message        — human-readable description
    """
    if laps.empty:
        return []

    # Build current standings with cumulative times
    standings = _get_current_standings(laps)
    if len(standings) < 2:
        return []

    battles = []

    # Check each consecutive pair
    for i in range(len(standings) - 1):
        ahead = standings.iloc[i]
        behind = standings.iloc[i + 1]

        gap = _calculate_gap(ahead, behind)
        if gap is None or gap > BATTLE_GAP_THRESHOLD:
            continue

        # Calculate closing rate from recent laps
        closing_rate = _calculate_closing_rate(
            laps, behind["Driver"], ahead["Driver"]
        )

        # Classify battle intensity
        intensity = _classify_intensity(gap, closing_rate)
        drs_active = gap < DRS_RANGE

        message = _build_battle_message(
            ahead["Driver"], behind["Driver"],
            gap, closing_rate, intensity, drs_active
        )

        battles.append({
            "driver_ahead": ahead["Driver"],
            "driver_behind": behind["Driver"],
            "team_ahead": ahead["Team"] if pd.notna(ahead["Team"]) else "Unknown",
            "team_behind": behind["Team"] if pd.notna(behind["Team"]) else "Unknown",
            "gap": round(gap, 3),
            "closing_rate": round(closing_rate, 3) if closing_rate else 0,
            "intensity": intensity,
            "drs_active": drs_active,
            "message": message,
        })

    # Sort: most intense first, then smallest gap
    intensity_order = {"INTENSE": 0, "CLOSE": 1, "WATCHING": 2}
    battles.sort(key=lambda b: (intensity_order.get(b["intensity"], 3), b["gap"]))

    return battles[:MAX_BATTLES]


# ---------------------------------------------------------------------------
# Get current standings with cumulative times
# ---------------------------------------------------------------------------
def _get_current_standings(laps):
    """
    Build a DataFrame of current driver positions with cumulative race time.
    Sorted by position.
    """
    valid = laps.dropna(subset=["Position"]).copy()
    if valid.empty:
        return pd.DataFrame()

    last_idx = valid.groupby("Driver")["LapNumber"].idxmax()
    state = valid.loc[last_idx, ["Driver", "Team", "Position", "LapNumber"]].copy()

    if "Time" in valid.columns:
        time_valid = valid.dropna(subset=["Time"])
        if not time_valid.empty:
            time_idx = time_valid.groupby("Driver")["LapNumber"].idxmax()
            times = time_valid.loc[time_idx, ["Driver", "LapNumber", "Time"]].copy()
            times["CumTime"] = times["Time"].dt.total_seconds()
            state = state.merge(
                times[["Driver", "LapNumber", "CumTime"]],
                on=["Driver", "LapNumber"],
                how="left",
            )

    if "CumTime" not in state.columns:
        state["CumTime"] = np.nan

    state["Position"] = state["Position"].astype(int)
    return state.sort_values("Position").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Calculate gap between two drivers
# ---------------------------------------------------------------------------
def _calculate_gap(ahead_row, behind_row):
    """
    Calculate the gap between two drivers using cumulative race time.

    Returns
    -------
    float or None
        Gap in seconds (positive = behind is behind ahead).
    """
    if pd.isna(ahead_row["CumTime"]) or pd.isna(behind_row["CumTime"]):
        return None

    ahead_lap = ahead_row.get("LapNumber")
    behind_lap = behind_row.get("LapNumber")
    if pd.isna(ahead_lap) or pd.isna(behind_lap) or int(ahead_lap) != int(behind_lap):
        return None

    gap = behind_row["CumTime"] - ahead_row["CumTime"]
    if gap < 0:
        return None

    return gap


# ---------------------------------------------------------------------------
# Calculate closing rate between two drivers
# ---------------------------------------------------------------------------
def _calculate_closing_rate(laps, chaser, target):
    """
    Calculate how quickly the chaser is closing on the target
    by comparing their lap times over the last few laps.

    Positive = chaser is catching (target's laps are slower).
    Negative = chaser is falling behind.

    Returns
    -------
    float or None
    """
    chaser_laps = (
        laps[laps["Driver"] == chaser]
        .sort_values("LapNumber")
        .tail(LOOKBACK_LAPS)
        .copy()
    )
    target_laps = (
        laps[laps["Driver"] == target]
        .sort_values("LapNumber")
        .tail(LOOKBACK_LAPS)
        .copy()
    )

    # Convert to seconds
    chaser_laps["Sec"] = chaser_laps["LapTime"].dt.total_seconds()
    target_laps["Sec"] = target_laps["LapTime"].dt.total_seconds()

    # Find common laps
    common = set(chaser_laps["LapNumber"]) & set(target_laps["LapNumber"])
    if len(common) < 2:
        return None

    deltas = []
    for lap in sorted(common):
        c = chaser_laps.loc[chaser_laps["LapNumber"] == lap, "Sec"]
        t = target_laps.loc[target_laps["LapNumber"] == lap, "Sec"]
        if not c.empty and not t.empty:
            c_val = c.iloc[0]
            t_val = t.iloc[0]
            if pd.notna(c_val) and pd.notna(t_val):
                deltas.append(t_val - c_val)

    if not deltas:
        return None

    return np.mean(deltas)


# ---------------------------------------------------------------------------
# Classify battle intensity
# ---------------------------------------------------------------------------
def _classify_intensity(gap, closing_rate):
    """
    Classify how intense the battle is.

    INTENSE: Within DRS range OR gap < 1s and closing
    CLOSE:   Gap < 1.5s or actively closing quickly
    WATCHING: Within 2s but not converging rapidly
    """
    if gap < DRS_RANGE:
        return "INTENSE"
    elif gap < 1.5 and closing_rate and closing_rate > 0:
        return "INTENSE"
    elif gap < 1.5 or (closing_rate and closing_rate > 0.2):
        return "CLOSE"
    else:
        return "WATCHING"


# ---------------------------------------------------------------------------
# Build battle message
# ---------------------------------------------------------------------------
def _build_battle_message(ahead, behind, gap, closing_rate, intensity, drs):
    """
    Build a readable battle description.

    Examples:
      "INTENSE: RUS vs LEC — 0.7s gap, DRS active, closing at 0.15s/lap"
      "CLOSE: ALO vs SAI — 1.3s gap, closing at 0.08s/lap"
      "WATCHING: NOR vs PIA — 1.9s gap, stable"
    """
    parts = [f"{intensity}: {behind} vs {ahead} — {gap:.1f}s gap"]

    if drs:
        parts.append("DRS active")

    if closing_rate and closing_rate > 0.02:
        parts.append(f"closing at {closing_rate:.2f}s/lap")
    elif closing_rate and closing_rate < -0.02:
        parts.append(f"gap growing at {abs(closing_rate):.2f}s/lap")
    else:
        parts.append("gap stable")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Summary for dashboard
# ---------------------------------------------------------------------------
def get_battle_summary(battles):
    """
    Produce summary stats for the dashboard stats row.

    Returns
    -------
    dict
        total_battles, intense_count, drs_battles, hottest_battle
    """
    if not battles:
        return {
            "total_battles": 0,
            "intense_count": 0,
            "drs_battles": 0,
            "hottest_battle": None,
        }

    intense = [b for b in battles if b["intensity"] == "INTENSE"]
    drs = [b for b in battles if b["drs_active"]]
    hottest = battles[0]  # already sorted by intensity

    return {
        "total_battles": len(battles),
        "intense_count": len(intense),
        "drs_battles": len(drs),
        "hottest_battle": (
            f"{hottest['driver_behind']} vs {hottest['driver_ahead']}"
        ),
    }
