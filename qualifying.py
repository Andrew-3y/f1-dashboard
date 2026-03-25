"""
qualifying.py — Qualifying Session Analysis Module
====================================================

Everything you need to understand what happened in qualifying even if
you couldn't watch it live:

  1. Sector time breakdown (S1, S2, S3 for best laps)
  2. Q1/Q2/Q3 elimination tracking
  3. Lap improvement progression through the session
  4. Team qualifying pace comparison
  5. Theoretical best lap (best S1 + best S2 + best S3 per driver)
  6. Teammate head-to-head qualifying battle
  7. Track evolution (how the track got faster through the session)
  8. Tyre strategy (which compound each driver used per qualifying phase)
  9. Close calls — drivers who nearly got eliminated
  10. Position movement from Q1 through Q3
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def _split_quali_sessions(laps):
    """Return Q1/Q2/Q3 lap sets when session status data is available."""
    try:
        q1, q2, q3 = laps.split_qualifying_sessions()
        return {
            "Q1": q1 if q1 is not None else pd.DataFrame(),
            "Q2": q2 if q2 is not None else pd.DataFrame(),
            "Q3": q3 if q3 is not None else pd.DataFrame(),
        }
    except Exception as exc:
        logger.warning("Could not split qualifying sessions: %s", exc)
        return {"Q1": pd.DataFrame(), "Q2": pd.DataFrame(), "Q3": pd.DataFrame()}


def _driver_best_times(session_laps):
    """Return best lap info for each driver within a qualifying phase."""
    if session_laps is None or session_laps.empty:
        return {}

    bests = {}
    for driver, dlaps in session_laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        bests[driver] = {
            "driver": driver,
            "team": valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown",
            "best_lap_s": round(valid["LapTime"].min().total_seconds(), 3),
            "total_laps": len(valid),
        }

    return bests


# ---------------------------------------------------------------------------
# Sector Time Breakdown
# ---------------------------------------------------------------------------
def analyze_sectors(laps):
    """
    Extract best sector times for each driver's fastest lap.
    Color-codes each sector delta: purple (best), green (<0.1), yellow (<0.3), orange (rest).
    """
    if laps.empty:
        return []

    sector_cols = ["Sector1Time", "Sector2Time", "Sector3Time"]
    if "LapTime" not in laps.columns:
        return []

    has_sectors = all(c in laps.columns for c in sector_cols)

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        best_idx = valid["LapTime"].idxmin()
        best_row = valid.loc[best_idx]

        entry = {
            "driver": driver,
            "team": best_row.get("Team", "Unknown") if pd.notna(best_row.get("Team")) else "Unknown",
            "best_lap_s": round(best_row["LapTime"].total_seconds(), 3),
        }

        if has_sectors:
            for i, col in enumerate(sector_cols, 1):
                val = best_row.get(col)
                entry[f"s{i}"] = round(val.total_seconds(), 3) if pd.notna(val) else None
        else:
            entry["s1"] = entry["s2"] = entry["s3"] = None

        # Compound used for best lap
        entry["compound"] = "UNKNOWN"
        if "Compound" in laps.columns and pd.notna(best_row.get("Compound")):
            entry["compound"] = str(best_row["Compound"]).upper()

        results.append(entry)

    if not results:
        return []

    results.sort(key=lambda x: x["best_lap_s"])

    # Calculate deltas to best sector times + classify
    if has_sectors:
        for si in ["s1", "s2", "s3"]:
            valid_sectors = [r[si] for r in results if r[si] is not None]
            if valid_sectors:
                best_sector = min(valid_sectors)
                for r in results:
                    if r[si] is not None:
                        delta = round(r[si] - best_sector, 3)
                        r[f"{si}_delta"] = delta
                        # Classify: best, good (<0.1), ok (<0.3), slow
                        if delta == 0:
                            r[f"{si}_class"] = "best"
                        elif delta < 0.1:
                            r[f"{si}_class"] = "good"
                        elif delta < 0.3:
                            r[f"{si}_class"] = "ok"
                        else:
                            r[f"{si}_class"] = "slow"
                    else:
                        r[f"{si}_delta"] = None
                        r[f"{si}_class"] = None
            else:
                for r in results:
                    r[f"{si}_delta"] = None
                    r[f"{si}_class"] = None

    for i, r in enumerate(results, 1):
        r["position"] = i

    return results


# ---------------------------------------------------------------------------
# Q1 / Q2 / Q3 Elimination Tracking
# ---------------------------------------------------------------------------
def analyze_elimination(laps):
    """
    Determine which drivers were eliminated in Q1, Q2, Q3.
    """
    if laps.empty:
        return {"q1_eliminated": [], "q2_eliminated": [], "q3_drivers": []}

    split = _split_quali_sessions(laps)
    q1_bests = sorted(_driver_best_times(split["Q1"]).values(), key=lambda x: x["best_lap_s"])
    q2_bests = sorted(_driver_best_times(split["Q2"]).values(), key=lambda x: x["best_lap_s"])
    q3_bests = sorted(_driver_best_times(split["Q3"]).values(), key=lambda x: x["best_lap_s"])

    if not q1_bests:
        return {"q1_eliminated": [], "q2_eliminated": [], "q3_drivers": []}

    q1_cut = min(15, len(q1_bests))
    q2_cut = min(10, len(q2_bests))

    q1_eliminated = q1_bests[q1_cut:]
    q2_eliminated = q2_bests[q2_cut:]
    q3_drivers = q3_bests if q3_bests else q2_bests[:q2_cut]

    if q1_eliminated and q1_cut > 0:
        q1_cutoff_time = q1_bests[q1_cut - 1]["best_lap_s"]
        for d in q1_eliminated:
            d["gap_to_cutoff"] = round(d["best_lap_s"] - q1_cutoff_time, 3)
            d["eliminated_in"] = "Q1"

    if q2_eliminated and q2_cut > 0:
        q2_cutoff_time = q2_bests[q2_cut - 1]["best_lap_s"]
        for d in q2_eliminated:
            d["gap_to_cutoff"] = round(d["best_lap_s"] - q2_cutoff_time, 3)
            d["eliminated_in"] = "Q2"

    for d in q3_drivers:
        d["gap_to_cutoff"] = 0.0
        d["eliminated_in"] = "Q3"

    return {
        "q1_eliminated": q1_eliminated,
        "q2_eliminated": q2_eliminated,
        "q3_drivers": q3_drivers,
    }


# ---------------------------------------------------------------------------
# Lap Improvement Progression
# ---------------------------------------------------------------------------
def analyze_improvement(laps):
    """Track how each driver improved their lap time through the session."""
    if laps.empty:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"]).sort_values("LapNumber")
        if len(valid) < 2:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        lap_times = [round(lt.total_seconds(), 3) for lt in valid["LapTime"]]

        first_time = lap_times[0]
        best_time = min(lap_times)
        improvement = round(first_time - best_time, 3)

        results.append({
            "driver": driver,
            "team": team,
            "first_lap_s": first_time,
            "best_lap_s": best_time,
            "improvement_s": improvement,
            "improvement_pct": round((improvement / first_time) * 100, 2) if first_time > 0 else 0,
            "num_attempts": len(lap_times),
            "lap_times": lap_times,
        })

    results.sort(key=lambda x: x["improvement_s"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Team Qualifying Pace Comparison
# ---------------------------------------------------------------------------
def analyze_team_pace(laps):
    """Compare qualifying pace between teams using each team's best driver."""
    if laps.empty:
        return []

    driver_bests = {}
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue
        best = valid["LapTime"].min().total_seconds()
        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        driver_bests[driver] = {"time": best, "team": team}

    teams = {}
    for driver, info in driver_bests.items():
        team = info["team"]
        if team not in teams:
            teams[team] = []
        teams[team].append({"driver": driver, "time": info["time"]})

    results = []
    for team, drivers in teams.items():
        drivers.sort(key=lambda x: x["time"])
        d1 = drivers[0]
        d2 = drivers[1] if len(drivers) > 1 else None

        results.append({
            "team": team,
            "driver_1": d1["driver"],
            "driver_1_time": round(d1["time"], 3),
            "driver_2": d2["driver"] if d2 else "—",
            "driver_2_time": round(d2["time"], 3) if d2 else None,
            "intra_team_gap": round(d2["time"] - d1["time"], 3) if d2 else None,
            "best_time": d1["time"],
        })

    results.sort(key=lambda x: x["best_time"])

    if results:
        best_team_time = results[0]["best_time"]
        for r in results:
            r["gap_to_best_team"] = round(r["best_time"] - best_team_time, 3)

    return results


# ---------------------------------------------------------------------------
# NEW: Theoretical Best Lap (best S1 + best S2 + best S3)
# ---------------------------------------------------------------------------
def analyze_theoretical_best(laps):
    """
    Calculate each driver's theoretical best lap by combining their
    personal best S1, S2, S3 times from ANY lap in the session.
    Shows how much time was left on the table.
    """
    if laps.empty:
        return []

    sector_cols = ["Sector1Time", "Sector2Time", "Sector3Time"]
    if not all(c in laps.columns for c in sector_cols):
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        actual_best = valid["LapTime"].min().total_seconds()

        best_sectors = {}
        best_sector_laps = {}
        for i, col in enumerate(sector_cols, 1):
            sector_valid = valid.dropna(subset=[col])
            if sector_valid.empty:
                best_sectors[f"s{i}"] = None
                best_sector_laps[f"s{i}_lap"] = None
                continue
            best_idx = sector_valid[col].idxmin()
            best_sectors[f"s{i}"] = round(sector_valid.loc[best_idx, col].total_seconds(), 3)
            best_sector_laps[f"s{i}_lap"] = int(sector_valid.loc[best_idx, "LapNumber"])

        s_vals = [best_sectors[f"s{i}"] for i in range(1, 4)]
        if all(v is not None for v in s_vals):
            theoretical = round(sum(s_vals), 3)
            delta = round(actual_best - theoretical, 3)
        else:
            theoretical = None
            delta = None

        results.append({
            "driver": driver,
            "team": team,
            "actual_best_s": round(actual_best, 3),
            "theoretical_s": theoretical,
            "time_lost_s": delta,
            **best_sectors,
            **best_sector_laps,
        })

    results = [r for r in results if r["theoretical_s"] is not None]
    results.sort(key=lambda x: x["theoretical_s"])

    # Add theoretical position and gap
    if results:
        best_theoretical = results[0]["theoretical_s"]
        for i, r in enumerate(results, 1):
            r["theoretical_position"] = i
            r["gap_to_theoretical_pole"] = round(r["theoretical_s"] - best_theoretical, 3)

    return results


# ---------------------------------------------------------------------------
# NEW: Teammate Head-to-Head
# ---------------------------------------------------------------------------
def analyze_teammate_battles(laps):
    """
    Direct teammate qualifying comparison — who beat who and by how much.
    """
    if laps.empty:
        return []

    driver_bests = {}
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue
        best = valid["LapTime"].min().total_seconds()
        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        total_laps = len(valid)
        driver_bests[driver] = {"time": best, "team": team, "laps": total_laps}

    # All drivers sorted by time for position lookup
    all_sorted = sorted(driver_bests.items(), key=lambda x: x[1]["time"])
    position_map = {d: i + 1 for i, (d, _) in enumerate(all_sorted)}

    teams = {}
    for driver, info in driver_bests.items():
        team = info["team"]
        if team not in teams:
            teams[team] = []
        teams[team].append({"driver": driver, **info})

    results = []
    for team, drivers in teams.items():
        if len(drivers) < 2:
            continue
        drivers.sort(key=lambda x: x["time"])
        d1, d2 = drivers[0], drivers[1]
        gap = round(d2["time"] - d1["time"], 3)

        results.append({
            "team": team,
            "winner": d1["driver"],
            "winner_time": round(d1["time"], 3),
            "winner_position": position_map.get(d1["driver"], 0),
            "winner_laps": d1["laps"],
            "loser": d2["driver"],
            "loser_time": round(d2["time"], 3),
            "loser_position": position_map.get(d2["driver"], 0),
            "loser_laps": d2["laps"],
            "gap": gap,
            "gap_pct": round((gap / d1["time"]) * 100, 3) if d1["time"] > 0 else 0,
        })

    results.sort(key=lambda x: x["gap"])
    return results


# ---------------------------------------------------------------------------
# NEW: Track Evolution
# ---------------------------------------------------------------------------
def analyze_track_evolution(laps):
    """
    Show how the track got faster through the session as rubber was laid down.
    Splits the session into time windows and shows the fastest lap in each.
    """
    if laps.empty:
        return []

    valid = laps.dropna(subset=["LapTime", "LapNumber"]).copy()
    if valid.empty or len(valid) < 5:
        return []

    valid["LapTimeS"] = valid["LapTime"].apply(lambda x: x.total_seconds())

    # Use LapNumber as a proxy for session progression
    max_lap = valid["LapNumber"].max()
    min_lap = valid["LapNumber"].min()
    if max_lap == min_lap:
        return []

    # Split into roughly 4 phases
    range_size = (max_lap - min_lap) / 4
    phases = []
    phase_names = ["Early", "Mid-Early", "Mid-Late", "Late"]

    for i in range(4):
        start = min_lap + i * range_size
        end = min_lap + (i + 1) * range_size
        phase_laps = valid[(valid["LapNumber"] >= start) & (valid["LapNumber"] < end + (1 if i == 3 else 0))]

        if phase_laps.empty:
            continue

        fastest_idx = phase_laps["LapTimeS"].idxmin()
        fastest = phase_laps.loc[fastest_idx]
        avg_time = phase_laps["LapTimeS"].mean()
        num_laps = len(phase_laps)

        phases.append({
            "phase": phase_names[i],
            "fastest_time_s": round(fastest["LapTimeS"], 3),
            "fastest_driver": fastest["Driver"],
            "avg_time_s": round(avg_time, 3),
            "num_laps": num_laps,
            "lap_range": f"{int(start)}-{int(end)}",
        })

    # Calculate evolution delta
    if len(phases) >= 2:
        first_fastest = phases[0]["fastest_time_s"]
        for p in phases:
            p["evolution_delta"] = round(p["fastest_time_s"] - first_fastest, 3)
    else:
        for p in phases:
            p["evolution_delta"] = 0.0

    return phases


# ---------------------------------------------------------------------------
# NEW: Close Calls (drivers who nearly got eliminated)
# ---------------------------------------------------------------------------
def analyze_close_calls(laps):
    """
    Identify drivers who narrowly avoided elimination.
    Shows the margin between the last safe driver and the first eliminated.
    """
    if laps.empty:
        return []

    split = _split_quali_sessions(laps)
    q1_bests = sorted(_driver_best_times(split["Q1"]).values(), key=lambda x: x["best_lap_s"])
    q2_bests = sorted(_driver_best_times(split["Q2"]).values(), key=lambda x: x["best_lap_s"])

    if len(q1_bests) < 11:
        return []

    close_calls = []

    if len(q2_bests) >= 11:
        safe = q2_bests[9]
        eliminated = q2_bests[10]
        close_calls.append({
            "cutoff": "Q2 -> Q3",
            "last_safe": safe["driver"],
            "last_safe_team": safe["team"],
            "last_safe_time": safe["best_lap_s"],
            "first_out": eliminated["driver"],
            "first_out_team": eliminated["team"],
            "first_out_time": eliminated["best_lap_s"],
            "margin": round(eliminated["best_lap_s"] - safe["best_lap_s"], 3),
        })

    if len(q1_bests) >= 16:
        safe = q1_bests[14]
        eliminated = q1_bests[15]
        close_calls.append({
            "cutoff": "Q1 -> Q2",
            "last_safe": safe["driver"],
            "last_safe_team": safe["team"],
            "last_safe_time": safe["best_lap_s"],
            "first_out": eliminated["driver"],
            "first_out_team": eliminated["team"],
            "first_out_time": eliminated["best_lap_s"],
            "margin": round(eliminated["best_lap_s"] - safe["best_lap_s"], 3),
        })

    return close_calls


# ---------------------------------------------------------------------------
# NEW: Tyre Strategy per Phase
# ---------------------------------------------------------------------------
def analyze_tyre_usage(laps):
    """
    What compound each driver used for their fastest lap.
    Useful to understand strategic choices (who gambled on softs, etc).
    """
    if laps.empty or "Compound" not in laps.columns:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        best_idx = valid["LapTime"].idxmin()
        best_compound = "UNKNOWN"
        if pd.notna(valid.loc[best_idx].get("Compound")):
            best_compound = str(valid.loc[best_idx]["Compound"]).upper()

        # All compounds used in the session
        compounds_used = []
        if "Compound" in valid.columns:
            compounds_used = sorted(set(
                str(c).upper() for c in valid["Compound"].dropna().unique()
            ))

        # Laps per compound
        compound_laps = {}
        for c in compounds_used:
            c_laps = valid[valid["Compound"].apply(lambda x: str(x).upper() if pd.notna(x) else "") == c]
            if not c_laps.empty:
                compound_laps[c] = {
                    "count": len(c_laps),
                    "best": round(c_laps["LapTime"].min().total_seconds(), 3),
                }

        results.append({
            "driver": driver,
            "team": team,
            "best_compound": best_compound,
            "compounds_used": compounds_used,
            "compound_laps": compound_laps,
            "total_laps": len(valid),
        })

    results.sort(key=lambda x: min(
        (cl["best"] for cl in x["compound_laps"].values()), default=999
    ))

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze_qualifying(laps):
    """Run all qualifying analysis modules."""
    modules = {
        "sectors": (analyze_sectors, []),
        "elimination": (analyze_elimination, {"q1_eliminated": [], "q2_eliminated": [], "q3_drivers": []}),
        "improvement": (analyze_improvement, []),
        "team_pace": (analyze_team_pace, []),
        "theoretical_best": (analyze_theoretical_best, []),
        "teammate_battles": (analyze_teammate_battles, []),
        "track_evolution": (analyze_track_evolution, []),
        "close_calls": (analyze_close_calls, []),
        "tyre_usage": (analyze_tyre_usage, []),
    }

    results = {}
    for key, (func, default) in modules.items():
        try:
            results[key] = func(laps)
        except Exception as exc:
            logger.warning("%s analysis failed: %s", key, exc)
            results[key] = default

    return results


def get_qualifying_summary(analysis):
    """Build summary stats for the qualifying header cards."""
    sectors = analysis.get("sectors", [])
    improvement = analysis.get("improvement", [])
    team_pace = analysis.get("team_pace", [])
    theoretical = analysis.get("theoretical_best", [])
    teammate = analysis.get("teammate_battles", [])

    pole_driver = sectors[0]["driver"] if sectors else "—"
    pole_time = sectors[0]["best_lap_s"] if sectors else 0

    closest_margin = None
    if len(sectors) >= 2:
        closest_margin = round(sectors[1]["best_lap_s"] - sectors[0]["best_lap_s"], 3)

    biggest_improver = improvement[0]["driver"] if improvement else "—"
    biggest_improvement = improvement[0]["improvement_s"] if improvement else 0

    tightest_team = None
    if team_pace:
        teams_with_gap = [t for t in team_pace if t["intra_team_gap"] is not None]
        if teams_with_gap:
            tightest = min(teams_with_gap, key=lambda x: x["intra_team_gap"])
            tightest_team = {"team": tightest["team"], "gap": tightest["intra_team_gap"]}

    # Theoretical pole vs actual pole
    theoretical_pole_delta = None
    if theoretical and sectors:
        theoretical_pole_delta = round(sectors[0]["best_lap_s"] - theoretical[0]["theoretical_s"], 3)

    # Closest teammate battle
    closest_teammates = None
    if teammate:
        closest_teammates = {"team": teammate[0]["team"], "gap": teammate[0]["gap"]}

    return {
        "pole_driver": pole_driver,
        "pole_time": pole_time,
        "closest_margin": closest_margin,
        "biggest_improver": biggest_improver,
        "biggest_improvement": biggest_improvement,
        "tightest_team": tightest_team,
        "total_drivers": len(sectors),
        "theoretical_pole_delta": theoretical_pole_delta,
        "closest_teammates": closest_teammates,
    }
