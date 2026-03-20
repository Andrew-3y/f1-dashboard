"""
degradation.py — Tire Degradation Modeling + Pit Window Prediction
==================================================================

WHY THIS MATTERS IN F1:
  Tires lose grip over time.  As rubber wears, lap times get slower.
  A driver on old tires might lose 0.05-0.15s per lap — and suddenly
  find themselves vulnerable to attack from drivers on fresher rubber.
  Knowing the "degradation rate" tells a team:
    - How quickly they're losing time
    - When it's optimal to pit (the "pit window")
    - Whether to overcut or undercut a rival

WHAT THIS MODULE DOES:
  1. Identifies each driver's current stint (laps since last pit stop)
  2. Filters out "dirty" laps (pit-in, pit-out, safety car, outliers)
  3. Fits a simple linear trend to clean stint laps
  4. Expresses degradation as seconds-per-lap (e.g. "+0.08s/lap")
  5. Estimates when tyre performance crosses a "pit window" threshold

ALGORITHM (simple linear regression — no ML libraries needed):
  For each stint of clean laps:
    slope = (sum of (x_i - x_mean)(y_i - y_mean)) /
            (sum of (x_i - x_mean)^2)
  where x = lap number within stint, y = lap time in seconds.
  A positive slope means the driver is getting SLOWER each lap.

  The "pit window" opens when the cumulative time lost from
  degradation exceeds the cost of a pit stop (typically ~22 seconds).

DATA REQUIRED FROM FASTF1:
  - Driver, LapNumber, LapTime, Stint, Compound
  - PitInTime, PitOutTime (to identify pit laps)
  - IsPersonalBest (optional — helps filter anomalies)

HANDLES MISSING DATA:
  - If 'Stint' column is missing → falls back to counting pit stops
  - If 'Compound' column is missing → labels it "UNKNOWN"
  - If too few clean laps → returns "Insufficient data"
  - If stint has < 4 clean laps → skips degradation calculation
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Minimum number of clean laps in a stint to calculate degradation.
# Below this, the linear fit would be unreliable.
MIN_CLEAN_LAPS = 4

# A lap is considered an "outlier" if it deviates more than this many
# seconds from the stint median.  This filters safety cars, traffic,
# mistakes, and other one-off anomalies.
OUTLIER_THRESHOLD_S = 3.0

# Typical pit stop time loss (time standing still + pit lane delta).
# In 2024-2025 F1, this is roughly 20-25 seconds at most circuits.
DEFAULT_PIT_LOSS_S = 23.0

# Degradation threshold: if cumulative time loss on the current stint
# is approaching the pit stop cost, the "pit window" is open.
# We trigger the window when projected loss exceeds this fraction of pit cost.
PIT_WINDOW_FRACTION = 0.7

# Maximum number of drivers to report on (keep dashboard readable)
MAX_REPORTS = 20


# ---------------------------------------------------------------------------
# Core: analyze all drivers
# ---------------------------------------------------------------------------
def analyze_degradation(laps, pit_loss=DEFAULT_PIT_LOSS_S):
    """
    Analyze tire degradation for every driver in the session.

    Parameters
    ----------
    laps : pandas.DataFrame
        The full laps DataFrame from FastF1.
    pit_loss : float
        Estimated time lost during a pit stop (seconds).

    Returns
    -------
    list[dict]
        One entry per driver, each containing:
            driver          — abbreviation (e.g. 'VER')
            team            — team name
            compound        — current tyre compound (SOFT/MEDIUM/HARD/UNKNOWN)
            stint_number    — which stint the driver is on (1 = first stint)
            stint_laps      — how many laps into the current stint
            deg_per_lap     — degradation rate (seconds lost per lap)
            deg_trend       — 'STABLE' / 'MODERATE' / 'HIGH' / 'CRITICAL'
            pit_window      — 'OPEN' / 'APPROACHING' / 'CLOSED' / 'N/A'
            laps_to_window  — estimated laps until pit window opens (None if open/N/A)
            message         — human-readable summary string
    """
    if laps.empty:
        return []

    reports = []

    for driver, driver_laps in laps.groupby("Driver"):
        try:
            report = _analyze_driver(driver_laps, pit_loss)
            if report is not None:
                reports.append(report)
        except Exception as exc:
            logger.warning("Degradation analysis failed for %s: %s", driver, exc)
            continue

    # Sort by degradation rate descending (worst degradation first)
    reports.sort(key=lambda r: r["deg_per_lap"], reverse=True)
    return reports[:MAX_REPORTS]


# ---------------------------------------------------------------------------
# Analyze a single driver
# ---------------------------------------------------------------------------
def _analyze_driver(driver_laps, pit_loss):
    """
    Analyze one driver's current stint for tire degradation.

    Steps:
      1. Sort laps chronologically.
      2. Identify the current (most recent) stint.
      3. Filter out pit-in, pit-out, and outlier laps.
      4. Fit a linear trend to the clean laps.
      5. Calculate degradation rate and pit window status.

    Returns
    -------
    dict or None
        None if there isn't enough data.
    """
    dl = driver_laps.sort_values("LapNumber").copy()
    driver = dl["Driver"].iloc[0]
    team = dl["Team"].iloc[0] if "Team" in dl.columns else "Unknown"

    # Convert LapTime to seconds
    dl["LapTimeSec"] = dl["LapTime"].dt.total_seconds()
    dl = dl.dropna(subset=["LapTimeSec"])

    if len(dl) < MIN_CLEAN_LAPS:
        return None

    # ----- Identify stints -----
    # FastF1 provides a 'Stint' column.  If missing, derive from pit stops.
    if "Stint" in dl.columns and dl["Stint"].notna().any():
        current_stint_num = int(dl["Stint"].iloc[-1])
        stint_laps = dl[dl["Stint"] == current_stint_num].copy()
    else:
        # Fallback: a new stint starts after every pit-out lap
        stint_laps = _get_current_stint_fallback(dl)
        current_stint_num = 1  # approximate

    # ----- Get compound -----
    compound = "UNKNOWN"
    if "Compound" in stint_laps.columns:
        compounds = stint_laps["Compound"].dropna()
        if not compounds.empty:
            compound = str(compounds.iloc[-1]).upper()

    # ----- Filter pit and outlier laps -----
    clean = _filter_clean_laps(stint_laps)

    if len(clean) < MIN_CLEAN_LAPS:
        # Not enough clean laps — report basic info but no trend
        return {
            "driver": driver,
            "team": team,
            "compound": compound,
            "stint_number": current_stint_num,
            "stint_laps": len(stint_laps),
            "deg_per_lap": 0.0,
            "deg_trend": "N/A",
            "pit_window": "N/A",
            "laps_to_window": None,
            "message": f"{driver}: stint {current_stint_num} on {compound} — insufficient data for trend",
        }

    # ----- Calculate degradation via linear regression -----
    deg_per_lap = _calculate_degradation(clean)

    # ----- Classify the trend -----
    deg_trend = _classify_degradation(deg_per_lap)

    # ----- Estimate pit window -----
    pit_window, laps_to_window = _estimate_pit_window(
        deg_per_lap, len(stint_laps), pit_loss
    )

    # ----- Build message -----
    message = _build_message(
        driver, compound, current_stint_num, len(stint_laps),
        deg_per_lap, deg_trend, pit_window, laps_to_window
    )

    return {
        "driver": driver,
        "team": team,
        "compound": compound,
        "stint_number": current_stint_num,
        "stint_laps": len(stint_laps),
        "deg_per_lap": round(deg_per_lap, 3),
        "deg_trend": deg_trend,
        "pit_window": pit_window,
        "laps_to_window": laps_to_window,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Fallback stint detection (when 'Stint' column is missing)
# ---------------------------------------------------------------------------
def _get_current_stint_fallback(dl):
    """
    If FastF1 didn't provide a Stint column, infer the current stint
    by finding the last pit-out lap and taking everything after it.
    """
    pit_out_laps = dl[dl["PitOutTime"].notna()]

    if pit_out_laps.empty:
        # No pit stops — the whole session is one stint
        return dl.copy()

    last_pit_out_lap = pit_out_laps["LapNumber"].max()
    return dl[dl["LapNumber"] >= last_pit_out_lap].copy()


# ---------------------------------------------------------------------------
# Filter clean laps (remove pits + outliers)
# ---------------------------------------------------------------------------
def _filter_clean_laps(stint_laps):
    """
    Remove pit-in laps, pit-out laps, and statistical outliers from
    a stint to get only "representative" flying laps.

    Steps:
      1. Remove laps where PitInTime or PitOutTime is set.
      2. Remove the FIRST lap of the stint (out-lap — always slow).
      3. Remove laps that deviate > OUTLIER_THRESHOLD from the median.
         This catches safety car laps, traffic, and mistakes.
    """
    clean = stint_laps.copy()

    # Remove pit-in and pit-out laps
    if "PitInTime" in clean.columns:
        clean = clean[clean["PitInTime"].isna()]
    if "PitOutTime" in clean.columns:
        clean = clean[clean["PitOutTime"].isna()]

    # Remove the first lap of the stint (out-lap)
    if len(clean) > 1:
        clean = clean.iloc[1:]

    # Must have LapTimeSec
    clean = clean.dropna(subset=["LapTimeSec"])

    if len(clean) < 2:
        return clean

    # Remove outliers (safety car, traffic, etc.)
    median_time = clean["LapTimeSec"].median()
    clean = clean[
        (clean["LapTimeSec"] - median_time).abs() <= OUTLIER_THRESHOLD_S
    ]

    return clean


# ---------------------------------------------------------------------------
# Linear regression for degradation (no scipy needed)
# ---------------------------------------------------------------------------
def _calculate_degradation(clean_laps):
    """
    Fit a simple linear regression:  lap_time = slope * lap_in_stint + intercept

    We only need the slope, which represents seconds gained (positive)
    or lost (negative) per additional lap on the tyres.

    A positive slope = driver is getting SLOWER = tire degradation.

    Uses the manual formula (no numpy.polyfit needed, though we could):
      slope = sum((x - x_mean) * (y - y_mean)) / sum((x - x_mean)^2)

    Parameters
    ----------
    clean_laps : DataFrame
        Filtered laps with LapTimeSec column.

    Returns
    -------
    float
        Degradation rate in seconds per lap.
        Positive means tires are degrading (driver is getting slower).
    """
    # x = position in stint (0, 1, 2, ...), y = lap time in seconds
    x = np.arange(len(clean_laps), dtype=float)
    y = clean_laps["LapTimeSec"].values.astype(float)

    x_mean = x.mean()
    y_mean = y.mean()

    numerator = np.sum((x - x_mean) * (y - y_mean))
    denominator = np.sum((x - x_mean) ** 2)

    if denominator == 0:
        return 0.0

    slope = numerator / denominator
    return slope


# ---------------------------------------------------------------------------
# Classify the degradation severity
# ---------------------------------------------------------------------------
def _classify_degradation(deg_per_lap):
    """
    Classify how severe the tire degradation is.

    Thresholds based on real F1 ranges:
      - < 0.03 s/lap:  Tires are holding up well → STABLE
      - 0.03 - 0.08:   Normal degradation → MODERATE
      - 0.08 - 0.15:   Significant drop-off → HIGH
      - > 0.15:        Tires are falling apart → CRITICAL

    A negative value means the driver is actually getting FASTER
    (fuel burn-off outweighs tyre wear) → IMPROVING.
    """
    if deg_per_lap < 0.0:
        return "IMPROVING"
    elif deg_per_lap < 0.03:
        return "STABLE"
    elif deg_per_lap < 0.08:
        return "MODERATE"
    elif deg_per_lap < 0.15:
        return "HIGH"
    else:
        return "CRITICAL"


# ---------------------------------------------------------------------------
# Estimate pit window
# ---------------------------------------------------------------------------
def _estimate_pit_window(deg_per_lap, stint_length, pit_loss):
    """
    Determine whether the "pit window" is open.

    Logic:
      The pit window opens when the cumulative time lost from tire
      degradation approaches the time cost of a pit stop.

      cumulative_loss = deg_per_lap * stint_length * (stint_length + 1) / 2
      (This is the sum of an arithmetic series: 0 + deg + 2*deg + ... + n*deg)

      But a simpler approximation:
        total_time_lost ≈ deg_per_lap * stint_length^2 / 2

      If total_time_lost > pit_loss * PIT_WINDOW_FRACTION → window is OPEN
      If within 5 laps of opening → APPROACHING
      Otherwise → CLOSED

    Returns
    -------
    (str, int or None)
        (window_status, laps_until_window_opens)
    """
    if deg_per_lap <= 0.01:
        return "CLOSED", None

    # Cumulative time lost so far (approximation)
    time_lost_so_far = deg_per_lap * stint_length * (stint_length + 1) / 2
    target = pit_loss * PIT_WINDOW_FRACTION

    if time_lost_so_far >= target:
        return "OPEN", 0

    # Estimate laps until window opens
    # We need to find N where deg_per_lap * N * (N+1) / 2 >= target
    # Simplify: N^2 * deg_per_lap / 2 ≈ target → N ≈ sqrt(2 * target / deg)
    laps_total_needed = np.sqrt(2 * target / deg_per_lap)
    laps_remaining = max(0, int(np.ceil(laps_total_needed - stint_length)))

    if laps_remaining <= 5:
        return "APPROACHING", laps_remaining
    else:
        return "CLOSED", laps_remaining


# ---------------------------------------------------------------------------
# Build human-readable message
# ---------------------------------------------------------------------------
def _build_message(driver, compound, stint_num, stint_laps,
                   deg_per_lap, deg_trend, pit_window, laps_to_window):
    """
    Build a concise, readable summary for the dashboard.

    Examples:
      "VER: +0.08s/lap on MEDIUM (stint 2, lap 15) — pit window OPEN"
      "NOR: Pace STABLE on HARD (stint 1, lap 8) — pit window CLOSED"
      "LEC: +0.12s/lap on SOFT (stint 3, lap 22) — pit window APPROACHING (~3 laps)"
    """
    if deg_trend == "N/A":
        return f"{driver}: stint {stint_num} on {compound} — insufficient data"

    if deg_trend == "IMPROVING":
        pace_str = "pace IMPROVING (fuel effect)"
    elif deg_trend == "STABLE":
        pace_str = "pace STABLE"
    else:
        pace_str = f"+{deg_per_lap:.3f}s/lap ({deg_trend})"

    window_str = f"pit window {pit_window}"
    if laps_to_window and laps_to_window > 0:
        window_str += f" (~{laps_to_window} laps)"

    return (
        f"{driver}: {pace_str} on {compound} "
        f"(stint {stint_num}, lap {stint_laps}) — {window_str}"
    )


# ---------------------------------------------------------------------------
# Summary for the dashboard stats row
# ---------------------------------------------------------------------------
def get_degradation_summary(reports):
    """
    Produce high-level stats for the stats-row cards.

    Returns
    -------
    dict with keys:
        total_drivers    — how many drivers were analyzed
        windows_open     — how many have their pit window OPEN
        worst_driver     — driver with highest degradation
        worst_deg        — their degradation rate
        avg_deg          — average degradation across all drivers
    """
    if not reports:
        return {
            "total_drivers": 0,
            "windows_open": 0,
            "worst_driver": None,
            "worst_deg": 0,
            "avg_deg": 0,
        }

    valid = [r for r in reports if r["deg_per_lap"] > 0]
    windows_open = sum(1 for r in reports if r["pit_window"] == "OPEN")

    worst = max(reports, key=lambda r: r["deg_per_lap"])
    avg_deg = round(np.mean([r["deg_per_lap"] for r in valid]), 3) if valid else 0

    return {
        "total_drivers": len(reports),
        "windows_open": windows_open,
        "worst_driver": worst["driver"],
        "worst_deg": worst["deg_per_lap"],
        "avg_deg": avg_deg,
    }
