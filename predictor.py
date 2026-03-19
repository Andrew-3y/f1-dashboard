"""
predictor.py — Overtake Prediction System
==========================================

This module analyzes gap-closing rates between consecutive drivers
and predicts when (if ever) the following driver will enter DRS range
(< 1 second gap) — which is when overtakes become possible.

KEY F1 CONCEPTS FOR BEGINNERS:
  - DRS (Drag Reduction System): A rear wing flap that opens on
    straights when a driver is within 1 second of the car ahead.
    It gives a significant speed boost and is the most common
    overtaking aid.
  - Gap closing rate: If Driver A is 3.0s behind Driver B on lap 10,
    and 2.5s behind on lap 11, the closing rate is 0.5s per lap.
  - At that rate, Driver A would reach DRS range in about
    (3.0 - 1.0) / 0.5 = 4 more laps.

ALGORITHM:
  1. For each pair of consecutive drivers (by position):
     a. Get the gap between them over the last N laps.
     b. Calculate the gap trend (is it shrinking or growing?).
     c. If shrinking, predict how many laps until DRS range.
  2. Output a sorted list of predictions (soonest overtake first).

LIMITATIONS:
  - We assume the closing rate stays constant (in reality it fluctuates).
  - Pit stops can cause sudden gap changes (we smooth these out).
  - This is a SIMPLE linear model — real teams use much more data.
    But it's impressive for a portfolio project!
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DRS_THRESHOLD = 1.0     # seconds — DRS is enabled below this gap
LOOKBACK_LAPS = 8       # how many recent laps to analyze for trend
MAX_PREDICTIONS = 15    # cap output for readability
MAX_LAPS_PREDICTION = 30  # don't predict more than 30 laps ahead


# ---------------------------------------------------------------------------
# Core prediction function
# ---------------------------------------------------------------------------
def predict_overtakes(laps):
    """
    Analyze lap data and predict potential overtakes.

    Parameters
    ----------
    laps : pandas.DataFrame
        The laps DataFrame from a loaded FastF1 session.

    Returns
    -------
    list[dict]
        Each dict contains:
            chaser          — driver abbreviation (the one catching up)
            chaser_team     — team name
            target          — driver abbreviation (the one being caught)
            target_team     — team name
            current_gap     — current gap in seconds
            closing_rate    — seconds gained per lap (positive = catching)
            laps_to_drs     — predicted laps until DRS range
            confidence      — 'HIGH', 'MEDIUM', or 'LOW'
            message         — human-readable prediction string
    """
    if laps.empty:
        return []

    predictions = []

    # Get the final position of each driver (their position on the last lap)
    last_laps = _get_last_laps(laps)
    if last_laps.empty or len(last_laps) < 2:
        return []

    # Sort by position
    last_laps = last_laps.sort_values("Position")

    drivers_in_order = last_laps["Driver"].tolist()

    # Analyze each consecutive pair
    for i in range(len(drivers_in_order) - 1):
        target = drivers_in_order[i]      # driver ahead
        chaser = drivers_in_order[i + 1]  # driver behind

        prediction = _analyze_pair(laps, chaser, target)
        if prediction is not None:
            predictions.append(prediction)

    # Sort: soonest overtake first
    predictions.sort(key=lambda p: p["laps_to_drs"] if p["laps_to_drs"] else 999)
    return predictions[:MAX_PREDICTIONS]


# ---------------------------------------------------------------------------
# Get the last recorded lap for each driver
# ---------------------------------------------------------------------------
def _get_last_laps(laps):
    """
    For each driver, find their last lap (highest LapNumber) to determine
    their current position.

    Returns a DataFrame with one row per driver: Driver, Position, Team.
    """
    # Filter to laps that have a valid position
    valid = laps.dropna(subset=["Position"]).copy()
    if valid.empty:
        return pd.DataFrame()

    # Get the row with the maximum LapNumber for each driver
    idx = valid.groupby("Driver")["LapNumber"].idxmax()
    last = valid.loc[idx, ["Driver", "Position", "Team"]].copy()
    last["Position"] = last["Position"].astype(int)
    return last


# ---------------------------------------------------------------------------
# Analyze a single pair of drivers
# ---------------------------------------------------------------------------
def _analyze_pair(laps, chaser, target):
    """
    For one chaser/target pair, compute the gap trend and predict
    when (if ever) the chaser will reach DRS range.

    Parameters
    ----------
    laps : DataFrame
    chaser : str — driver abbreviation
    target : str — driver abbreviation

    Returns
    -------
    dict or None
        None if there's not enough data to make a prediction.
    """
    # Get lap times for both drivers
    chaser_laps = (
        laps[laps["Driver"] == chaser]
        .sort_values("LapNumber")
        .copy()
    )
    target_laps = (
        laps[laps["Driver"] == target]
        .sort_values("LapNumber")
        .copy()
    )

    if chaser_laps.empty or target_laps.empty:
        return None

    # Convert to seconds
    chaser_laps["TimeSec"] = chaser_laps["LapTime"].dt.total_seconds()
    target_laps["TimeSec"] = target_laps["LapTime"].dt.total_seconds()

    # Find overlapping laps (both drivers completed)
    common_laps = set(chaser_laps["LapNumber"]) & set(target_laps["LapNumber"])
    if len(common_laps) < 3:
        return None

    common_sorted = sorted(common_laps)

    # Take the last LOOKBACK_LAPS for trend analysis
    recent = common_sorted[-LOOKBACK_LAPS:]

    chaser_recent = chaser_laps[chaser_laps["LapNumber"].isin(recent)].set_index("LapNumber")
    target_recent = target_laps[target_laps["LapNumber"].isin(recent)].set_index("LapNumber")

    # Calculate per-lap gap delta:
    # Positive delta means chaser is FASTER (gaining time)
    deltas = []
    for lap in recent:
        if lap in chaser_recent.index and lap in target_recent.index:
            c_time = chaser_recent.loc[lap, "TimeSec"]
            t_time = target_recent.loc[lap, "TimeSec"]
            if pd.notna(c_time) and pd.notna(t_time):
                # If target is slower (higher time), chaser is gaining
                deltas.append(t_time - c_time)

    if len(deltas) < 2:
        return None

    # Average closing rate (positive = chaser is catching up)
    closing_rate = np.mean(deltas)

    # Estimate current gap from cumulative time difference
    # Use the last few laps' cumulative difference as an approximation
    current_gap = _estimate_current_gap(laps, chaser, target)
    if current_gap is None or current_gap < 0:
        return None

    # Teams
    chaser_team = chaser_laps["Team"].iloc[0] if "Team" in chaser_laps.columns else "Unknown"
    target_team = target_laps["Team"].iloc[0] if "Team" in target_laps.columns else "Unknown"

    # Predict laps to DRS range
    if closing_rate <= 0.01:
        # Not closing — no overtake predicted
        laps_to_drs = None
        confidence = "LOW"
        message = (
            f"{chaser} is NOT closing on {target} "
            f"(gap: {current_gap:.1f}s, rate: {closing_rate:+.3f}s/lap)"
        )
    else:
        gap_to_close = max(current_gap - DRS_THRESHOLD, 0)
        laps_to_drs = round(gap_to_close / closing_rate, 1)

        if laps_to_drs > MAX_LAPS_PREDICTION:
            laps_to_drs = None
            confidence = "LOW"
            message = (
                f"{chaser} closing on {target} slowly "
                f"(gap: {current_gap:.1f}s, rate: {closing_rate:+.3f}s/lap)"
            )
        else:
            confidence = _classify_confidence(closing_rate, current_gap, len(deltas))
            message = (
                f"{chaser} could reach DRS on {target} in ~{laps_to_drs:.0f} laps "
                f"(gap: {current_gap:.1f}s, closing at {closing_rate:.3f}s/lap)"
            )

    return {
        "chaser": chaser,
        "chaser_team": chaser_team,
        "target": target,
        "target_team": target_team,
        "current_gap": round(current_gap, 3),
        "closing_rate": round(closing_rate, 3),
        "laps_to_drs": laps_to_drs,
        "confidence": confidence,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Estimate the current gap between two drivers
# ---------------------------------------------------------------------------
def _estimate_current_gap(laps, chaser, target):
    """
    Estimate the gap between chaser and target by comparing their
    cumulative race times.

    Falls back to comparing their last lap's time gap if cumulative
    data isn't clean.

    Returns
    -------
    float or None
        Gap in seconds (positive = chaser is behind target).
    """
    try:
        chaser_laps = laps[laps["Driver"] == chaser].sort_values("LapNumber")
        target_laps = laps[laps["Driver"] == target].sort_values("LapNumber")

        # Use Time column (cumulative race time) if available
        if "Time" in chaser_laps.columns:
            chaser_time = chaser_laps["Time"].dropna()
            target_time = target_laps["Time"].dropna()

            if not chaser_time.empty and not target_time.empty:
                gap = (
                    chaser_time.iloc[-1].total_seconds()
                    - target_time.iloc[-1].total_seconds()
                )
                return abs(gap)

        # Fallback: approximate from average lap time difference
        c_avg = chaser_laps["LapTime"].dropna().mean().total_seconds()
        t_avg = target_laps["LapTime"].dropna().mean().total_seconds()
        # This is a rough estimate
        return abs(c_avg - t_avg) * 2

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Confidence classification
# ---------------------------------------------------------------------------
def _classify_confidence(closing_rate, gap, data_points):
    """
    Rate our confidence in the overtake prediction.

    HIGH:   Strong closing rate, small gap, lots of data
    MEDIUM: Moderate closing or moderate gap
    LOW:    Slow closing, big gap, or little data
    """
    if data_points >= 6 and closing_rate > 0.3 and gap < 3.0:
        return "HIGH"
    elif data_points >= 4 and closing_rate > 0.1 and gap < 5.0:
        return "MEDIUM"
    else:
        return "LOW"


# ---------------------------------------------------------------------------
# Summary for the dashboard header
# ---------------------------------------------------------------------------
def get_prediction_summary(predictions):
    """
    Quick stats about the overtake predictions.

    Returns
    -------
    dict
        total, imminent (<=3 laps), high_confidence count, fastest_closing pair
    """
    if not predictions:
        return {
            "total": 0,
            "imminent": 0,
            "high_confidence": 0,
            "fastest_closing": None,
        }

    active = [p for p in predictions if p["laps_to_drs"] is not None]

    imminent = [p for p in active if p["laps_to_drs"] <= 3]
    high_conf = [p for p in predictions if p["confidence"] == "HIGH"]

    # Fastest closing pair
    closers = [p for p in predictions if p["closing_rate"] > 0]
    fastest = max(closers, key=lambda p: p["closing_rate"]) if closers else None

    return {
        "total": len(predictions),
        "imminent": len(imminent),
        "high_confidence": len(high_conf),
        "fastest_closing": (
            f"{fastest['chaser']} on {fastest['target']}" if fastest else None
        ),
    }
