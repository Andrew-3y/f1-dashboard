"""
anomaly.py — Lap-Time Anomaly Detection System
===============================================

This module scans every driver's lap times and flags "anomalies":
moments where a driver's pace suddenly drops compared to their own
rolling average.

WHY THIS MATTERS:
  In a real race, a sudden lap-time increase can signal:
    - tyre degradation (tyres wearing out)
    - a mistake or off-track moment
    - traffic (getting stuck behind a slower car)
    - car damage
    - a pit stop (which we filter out)

HOW THE ALGORITHM WORKS:
  1. For each driver, compute a rolling average of their last N laps.
  2. Compare each lap time to that rolling average.
  3. If a lap is more than THRESHOLD seconds slower, flag it.
  4. Filter out pit-in / pit-out laps (those are expected to be slow).
  5. Rank alerts by severity (biggest pace loss first).

This is deliberately kept simple — no ML models, no external deps —
so it runs fast on Render's limited CPU.
"""

import pandas as pd
import logging
from collections import Counter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
# How many previous laps to average over.  5 is a good balance between
# responsiveness and noise filtering.
ROLLING_WINDOW = 5

# Minimum pace loss (in seconds) to trigger an alert.
# 1.0 s is significant in F1 — roughly the difference between a clean
# lap and a lap with a noticeable issue.
PACE_LOSS_THRESHOLD = 1.0

# Maximum number of alerts to return (keep the dashboard readable).
MAX_ALERTS = 20

# If too many drivers spike on the same lap, treat it as a session-wide
# slowdown (SC/VSC/red flag/weather shift) instead of a personal anomaly.
GLOBAL_EVENT_DRIVER_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------
def detect_anomalies(laps, window=ROLLING_WINDOW, threshold=PACE_LOSS_THRESHOLD):
    """
    Scan lap data and return a list of anomaly alerts.

    Parameters
    ----------
    laps : pandas.DataFrame
        The laps DataFrame from a loaded FastF1 session.
        Expected columns: Driver, LapNumber, LapTime, PitInTime, PitOutTime, Team
    window : int
        Number of laps for the rolling average (default 5).
    threshold : float
        Minimum seconds slower than the rolling average to flag (default 1.0).

    Returns
    -------
    list[dict]
        Each dict contains:
            driver       — three-letter abbreviation (e.g. 'VER')
            team         — team name
            lap_number   — which lap the anomaly occurred on
            lap_time_s   — actual lap time in seconds
            rolling_avg  — rolling average in seconds
            delta        — how much slower than the rolling average
            severity     — 'HIGH' (>2s), 'MEDIUM' (>1.5s), or 'LOW'
            message      — human-readable alert string

    Algorithm Detail
    ----------------
    For each driver:
      1. Sort their laps by LapNumber.
      2. Convert LapTime to seconds.
      3. Remove pit-in and pit-out laps (they're expected outliers).
      4. Compute a rolling mean over the last `window` laps.
      5. Calculate delta = lap_time - rolling_mean.
      6. If delta > threshold → create an alert.
    """
    if laps.empty:
        return []

    alerts = []

    # Group by driver so we can analyze each driver independently
    for driver, driver_laps in laps.groupby("Driver"):
        # Sort chronologically and keep only laps with valid times
        dl = (
            driver_laps.sort_values("LapNumber")
            .copy()
        )

        # Convert LapTime (Timedelta) to float seconds for math
        dl["LapTimeSec"] = dl["LapTime"].dt.total_seconds()

        # Drop rows where we don't have a valid lap time
        dl = dl.dropna(subset=["LapTimeSec"])

        if len(dl) < window + 1:
            # Not enough laps to compute a meaningful rolling average
            continue

        # Filter out pit-in and pit-out laps
        # PitInTime is NaT when the driver did NOT pit on that lap
        is_pit_lap = dl["PitInTime"].notna() | dl["PitOutTime"].notna()
        clean_laps = dl[~is_pit_lap].copy()

        if len(clean_laps) < window + 1:
            continue

        # Compute rolling average (shift by 1 so the current lap isn't included)
        clean_laps["RollingAvg"] = (
            clean_laps["LapTimeSec"]
            .rolling(window=window, min_periods=window)
            .mean()
            .shift(1)  # compare against PREVIOUS laps, not including current
        )

        # Calculate delta: how much slower is this lap vs. the rolling avg?
        clean_laps["Delta"] = clean_laps["LapTimeSec"] - clean_laps["RollingAvg"]

        # Flag anomalies
        anomalous = clean_laps[clean_laps["Delta"] > threshold]

        team = dl["Team"].iloc[0] if "Team" in dl.columns else "Unknown"

        for _, row in anomalous.iterrows():
            delta = round(row["Delta"], 3)
            severity = _classify_severity(delta)

            alerts.append(
                {
                    "driver": driver,
                    "team": team,
                    "lap_number": int(row["LapNumber"]),
                    "lap_time_s": round(row["LapTimeSec"], 3),
                    "rolling_avg": round(row["RollingAvg"], 3),
                    "delta": delta,
                    "severity": severity,
                    "message": (
                        f"ALERT: {driver} lost {delta:.1f}s on lap "
                        f"{int(row['LapNumber'])} "
                        f"(lap: {row['LapTimeSec']:.3f}s vs "
                        f"avg: {row['RollingAvg']:.3f}s) — {severity}"
                    ),
                }
            )

    if alerts:
        lap_counts = Counter(alert["lap_number"] for alert in alerts)
        global_event_laps = {
            lap_number
            for lap_number, count in lap_counts.items()
            if count >= GLOBAL_EVENT_DRIVER_THRESHOLD
        }
        if global_event_laps:
            alerts = [
                alert for alert in alerts
                if alert["lap_number"] not in global_event_laps
            ]

    # Sort by delta descending (most severe first), then limit
    alerts.sort(key=lambda a: a["delta"], reverse=True)
    return alerts[:MAX_ALERTS]


# ---------------------------------------------------------------------------
# Severity classification helper
# ---------------------------------------------------------------------------
def _classify_severity(delta_seconds):
    """
    Classify an anomaly's severity based on how many seconds were lost.

    Parameters
    ----------
    delta_seconds : float

    Returns
    -------
    str
        'CRITICAL' if > 3s, 'HIGH' if > 2s, 'MEDIUM' if > 1.5s, else 'LOW'.

    Why these thresholds?
      - > 3s:  Almost certainly a major incident (spin, damage, off-track)
      - > 2s:  Very significant; likely tyre issue or traffic
      - > 1.5s: Notable; could be a mistake in one corner
      - > 1s:  Minor but worth noting
    """
    if delta_seconds > 3.0:
        return "CRITICAL"
    elif delta_seconds > 2.0:
        return "HIGH"
    elif delta_seconds > 1.5:
        return "MEDIUM"
    else:
        return "LOW"


# ---------------------------------------------------------------------------
# Summary statistics (shown at the top of the Alerts panel)
# ---------------------------------------------------------------------------
def get_anomaly_summary(alerts):
    """
    Produce a quick summary of detected anomalies.

    Parameters
    ----------
    alerts : list[dict]
        Output from detect_anomalies().

    Returns
    -------
    dict
        total_alerts, critical, high, medium, low counts,
        most_affected_driver, max_delta
    """
    if not alerts:
        return {
            "total_alerts": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "most_affected_driver": None,
            "max_delta": 0,
        }

    from collections import Counter

    severity_counts = Counter(a["severity"] for a in alerts)
    driver_counts = Counter(a["driver"] for a in alerts)
    most_affected = driver_counts.most_common(1)[0][0]

    return {
        "total_alerts": len(alerts),
        "critical": severity_counts.get("CRITICAL", 0),
        "high": severity_counts.get("HIGH", 0),
        "medium": severity_counts.get("MEDIUM", 0),
        "low": severity_counts.get("LOW", 0),
        "most_affected_driver": most_affected,
        "max_delta": round(max(a["delta"] for a in alerts), 3),
    }
