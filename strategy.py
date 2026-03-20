"""
strategy.py — Pit Strategy Simulator
=====================================

WHY THIS MATTERS IN F1:
  The difference between winning and P5 can come down to pit stop
  timing.  Teams constantly ask:
    "If we pit NOW, where do we come out?"
    "Can we undercut the car ahead?"
    "Will we emerge in traffic?"

  This module answers those questions using simple math — no heavy
  simulation engine, just the same core logic real strategists use
  (gap analysis + estimated pit loss).

WHAT THIS MODULE DOES:
  For each driver, simulates "What if they pit RIGHT NOW?":
    1. Calculates their current race position and gaps to other drivers
    2. Subtracts the estimated pit stop time loss
    3. Determines where they'd rejoin (estimated position)
    4. Checks if that puts them in traffic
    5. Evaluates undercut/overcut potential vs. the car ahead

KEY CONCEPTS:
  - Pit loss: The time a driver loses by going through the pit lane
    instead of continuing on track.  Typically 20-25 seconds in F1.
  - Undercut: Pitting BEFORE a rival.  On fresh tires you set faster
    laps, and when they pit later you're ahead.  Works best when
    tire degradation is high.
  - Overcut: Staying out LONGER than a rival.  Works when the track
    is clear and you can set fast laps in free air while they're in
    traffic on fresh tires.
  - Traffic: Emerging from the pits behind a slower car (backmarker
    or someone on older tires) can cost several seconds.

DATA REQUIRED:
  - Driver, LapNumber, LapTime, Position, Time (cumulative)
  - PitInTime, PitOutTime
  - Gaps between consecutive drivers (calculated from Time column)
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PIT_LOSS_S = 23.0    # seconds lost during a pit stop
UNDERCUT_ADVANTAGE_S = 1.5   # typical fresh-tyre pace gain per lap
UNDERCUT_WINDOW_LAPS = 3     # how many laps the undercut advantage lasts
TRAFFIC_THRESHOLD_S = 2.0    # if within this gap, driver is "in traffic"
MAX_STRATEGIES = 20          # cap output for readability


# ---------------------------------------------------------------------------
# Core: simulate pit strategies for all drivers
# ---------------------------------------------------------------------------
def simulate_strategies(laps, pit_loss=DEFAULT_PIT_LOSS_S, degradation_data=None):
    """
    For every driver currently racing, simulate what happens if they
    pit on the next lap.

    Parameters
    ----------
    laps : pandas.DataFrame
        The full laps DataFrame from FastF1.
    pit_loss : float
        Estimated time lost during a pit stop (seconds).
    degradation_data : list[dict] or None
        Output from degradation.analyze_degradation().
        If provided, uses degradation rates to improve undercut analysis.

    Returns
    -------
    list[dict]
        One entry per driver with pit strategy analysis.
    """
    if laps.empty:
        return []

    # Build a snapshot of the current race state
    race_state = _build_race_state(laps)
    if race_state.empty or len(race_state) < 2:
        return []

    # Build degradation lookup (driver → deg_per_lap)
    deg_lookup = {}
    if degradation_data:
        for d in degradation_data:
            deg_lookup[d["driver"]] = d.get("deg_per_lap", 0)

    strategies = []

    for _, driver_row in race_state.iterrows():
        try:
            result = _simulate_pit_for_driver(
                driver_row, race_state, pit_loss, deg_lookup
            )
            if result is not None:
                strategies.append(result)
        except Exception as exc:
            logger.warning(
                "Strategy sim failed for %s: %s", driver_row["Driver"], exc
            )

    # Sort by position
    strategies.sort(key=lambda s: s["current_position"])
    return strategies[:MAX_STRATEGIES]


# ---------------------------------------------------------------------------
# Build a snapshot of current race positions and gaps
# ---------------------------------------------------------------------------
def _build_race_state(laps):
    """
    Create a DataFrame with one row per driver showing their current
    position, cumulative time, recent pace, and gap to the car ahead.

    Columns: Driver, Team, Position, CumulativeTime, RecentPace,
             GapAhead, DriverAhead
    """
    # Get each driver's last lap
    valid = laps.dropna(subset=["Position"]).copy()
    if valid.empty:
        return pd.DataFrame()

    last_idx = valid.groupby("Driver")["LapNumber"].idxmax()
    state = valid.loc[last_idx, ["Driver", "Team", "Position"]].copy()

    # Cumulative race time
    if "Time" in valid.columns:
        time_idx = valid.dropna(subset=["Time"]).groupby("Driver")["LapNumber"].idxmax()
        times = valid.loc[time_idx, ["Driver", "Time"]].copy()
        times["CumulativeTime"] = times["Time"].dt.total_seconds()
        state = state.merge(times[["Driver", "CumulativeTime"]], on="Driver", how="left")
    else:
        state["CumulativeTime"] = np.nan

    # Recent pace (average of last 5 laps)
    pace_data = []
    for driver in state["Driver"]:
        dl = valid[valid["Driver"] == driver].sort_values("LapNumber").tail(5)
        avg_pace = dl["LapTime"].dt.total_seconds().mean()
        pace_data.append({"Driver": driver, "RecentPace": avg_pace})
    pace_df = pd.DataFrame(pace_data)
    state = state.merge(pace_df, on="Driver", how="left")

    # Sort by position
    state["Position"] = state["Position"].astype(int)
    state = state.sort_values("Position").reset_index(drop=True)

    # Calculate gap to the car ahead
    state["GapAhead"] = np.nan
    state["DriverAhead"] = None
    for i in range(1, len(state)):
        if pd.notna(state.iloc[i]["CumulativeTime"]) and pd.notna(state.iloc[i - 1]["CumulativeTime"]):
            state.loc[state.index[i], "GapAhead"] = (
                state.iloc[i]["CumulativeTime"] - state.iloc[i - 1]["CumulativeTime"]
            )
        state.loc[state.index[i], "DriverAhead"] = state.iloc[i - 1]["Driver"]

    return state


# ---------------------------------------------------------------------------
# Simulate pitting for one driver
# ---------------------------------------------------------------------------
def _simulate_pit_for_driver(driver_row, race_state, pit_loss, deg_lookup):
    """
    Simulate: "What if this driver pits on the next lap?"

    Steps:
      1. Add pit_loss to their cumulative time → new_time.
      2. Compare new_time against all other drivers' cumulative times.
      3. Determine new (estimated) position after pit.
      4. Check if they'd emerge in traffic.
      5. Check undercut/overcut potential vs. the car ahead.

    Returns
    -------
    dict with strategy analysis, or None if data is insufficient.
    """
    driver = driver_row["Driver"]
    team = driver_row["Team"] if pd.notna(driver_row["Team"]) else "Unknown"
    current_pos = int(driver_row["Position"])
    cumulative_time = driver_row["CumulativeTime"]

    if pd.isna(cumulative_time):
        return None

    # Step 1: Projected time after pit stop
    projected_time = cumulative_time + pit_loss

    # Step 2: Where would they rejoin?
    # Compare projected_time against everyone else's current cumulative time.
    rejoin_pos = 1
    in_traffic = False
    closest_gap_after = None
    driver_behind_after = None

    for _, other in race_state.iterrows():
        if other["Driver"] == driver:
            continue
        if pd.isna(other["CumulativeTime"]):
            continue

        if projected_time > other["CumulativeTime"]:
            rejoin_pos += 1
            # Check if we'd be right behind this car (traffic)
            gap_behind = projected_time - other["CumulativeTime"]
            if gap_behind < TRAFFIC_THRESHOLD_S:
                in_traffic = True
                driver_behind_after = other["Driver"]
                closest_gap_after = round(gap_behind, 1)

    positions_lost = rejoin_pos - current_pos

    # Step 3: Undercut analysis
    undercut = _analyze_undercut(
        driver_row, race_state, pit_loss, deg_lookup
    )

    # Step 4: Build recommendation
    recommendation = _build_recommendation(
        positions_lost, in_traffic, undercut, driver_row
    )

    # Step 5: Build message
    message = _build_strategy_message(
        driver, current_pos, rejoin_pos, positions_lost,
        in_traffic, driver_behind_after, closest_gap_after,
        undercut, recommendation
    )

    return {
        "driver": driver,
        "team": team,
        "current_position": current_pos,
        "rejoin_position": rejoin_pos,
        "positions_lost": positions_lost,
        "in_traffic": in_traffic,
        "closest_after_pit": driver_behind_after,
        "gap_after_pit": closest_gap_after,
        "undercut_possible": undercut["possible"],
        "undercut_target": undercut["target"],
        "recommendation": recommendation,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Undercut analysis
# ---------------------------------------------------------------------------
def _analyze_undercut(driver_row, race_state, pit_loss, deg_lookup):
    """
    Determine if an undercut on the car ahead is feasible.

    An undercut works when:
      1. The gap to the car ahead is less than the pit loss.
      2. The driver's fresh-tyre pace advantage (typically ~1.5s/lap)
         over 2-3 laps can close the remaining gap.
      3. The car ahead has meaningful tire degradation.

    Returns
    -------
    dict with keys: possible, target, laps_needed, advantage_s
    """
    gap_ahead = driver_row.get("GapAhead")
    driver_ahead = driver_row.get("DriverAhead")

    result = {
        "possible": False,
        "target": driver_ahead,
        "laps_needed": None,
        "advantage_s": 0,
    }

    if pd.isna(gap_ahead) or driver_ahead is None:
        return result

    # The undercut advantage per lap depends on:
    #   1. Fresh tyre pace delta (~1.5s/lap vs. worn tyres)
    #   2. The rival's current degradation rate
    target_deg = deg_lookup.get(driver_ahead, 0.05)  # default 0.05s/lap
    pace_advantage = UNDERCUT_ADVANTAGE_S + target_deg

    # Gap that needs to be overcome:
    # After pitting, you lose `pit_loss` seconds but gain `pace_advantage`
    # per lap for `UNDERCUT_WINDOW_LAPS` laps.
    total_undercut_gain = pace_advantage * UNDERCUT_WINDOW_LAPS
    net_gap = gap_ahead  # gap to car ahead before pitting

    # Undercut is possible if the fresh-tyre advantage over N laps
    # exceeds the current gap
    if total_undercut_gain > net_gap and net_gap < pit_loss:
        result["possible"] = True
        result["laps_needed"] = max(1, int(np.ceil(net_gap / pace_advantage)))
        result["advantage_s"] = round(total_undercut_gain - net_gap, 1)

    return result


# ---------------------------------------------------------------------------
# Build recommendation
# ---------------------------------------------------------------------------
def _build_recommendation(positions_lost, in_traffic, undercut, driver_row):
    """
    Generate a simple PIT / STAY / CONSIDER recommendation.

    Logic:
      - PIT NOW:     Undercut is possible and no traffic risk
      - CONSIDER PIT: Undercut possible but traffic risk, or marginal
      - STAY OUT:    Would lose positions and no undercut opportunity
      - HOLD:        No clear advantage either way
    """
    if undercut["possible"] and not in_traffic and positions_lost <= 1:
        return "PIT NOW"
    elif undercut["possible"] and (in_traffic or positions_lost <= 2):
        return "CONSIDER PIT"
    elif positions_lost >= 3 and not undercut["possible"]:
        return "STAY OUT"
    elif positions_lost == 0:
        return "PIT NOW"
    else:
        return "HOLD"


# ---------------------------------------------------------------------------
# Build strategy message
# ---------------------------------------------------------------------------
def _build_strategy_message(driver, current_pos, rejoin_pos, positions_lost,
                            in_traffic, traffic_driver, traffic_gap,
                            undercut, recommendation):
    """
    Build a readable strategy summary.

    Examples:
      "VER: Pit now → rejoin P1 (no positions lost). Recommendation: PIT NOW"
      "NOR: Pit now → rejoin P5 (-2 pos). Undercut on LEC possible in ~2 laps. CONSIDER PIT"
      "HAM: Pit now → rejoin P8 (-3 pos, in traffic behind SAI). STAY OUT"
    """
    pos_change = f"P{rejoin_pos}"
    if positions_lost > 0:
        pos_change += f" (-{positions_lost} pos)"
    elif positions_lost == 0:
        pos_change += " (no loss)"
    else:
        pos_change += f" (+{abs(positions_lost)} pos)"

    parts = [f"{driver}: Pit now → rejoin {pos_change}"]

    if in_traffic and traffic_driver:
        parts.append(f"in traffic behind {traffic_driver} ({traffic_gap}s)")

    if undercut["possible"] and undercut["target"]:
        parts.append(
            f"Undercut on {undercut['target']} possible in ~{undercut['laps_needed']} laps"
        )

    parts.append(f"[{recommendation}]")

    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Summary for dashboard
# ---------------------------------------------------------------------------
def get_strategy_summary(strategies):
    """
    Produce summary stats for the dashboard header.

    Returns
    -------
    dict
        total, pit_now_count, undercuts_available, best_opportunity
    """
    if not strategies:
        return {
            "total": 0,
            "pit_now_count": 0,
            "undercuts_available": 0,
            "best_opportunity": None,
        }

    pit_now = [s for s in strategies if s["recommendation"] == "PIT NOW"]
    undercuts = [s for s in strategies if s["undercut_possible"]]

    best = None
    if undercuts:
        best = f"{undercuts[0]['driver']} on {undercuts[0]['undercut_target']}"

    return {
        "total": len(strategies),
        "pit_now_count": len(pit_now),
        "undercuts_available": len(undercuts),
        "best_opportunity": best,
    }
