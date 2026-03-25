"""
practice.py — Practice Session Analysis Module
================================================

Everything you need to understand a practice session even if you missed
it, so you can go into qualifying and the race with full context:

  1.  Long run pace — sustained stint analysis (race simulation)
  2.  Short run pace — single-lap / qualifying simulation
  3.  Compound comparison — pace delta between tire types
  4.  Team pace ranking — where each team stands
  5.  Consistency analysis — who's nailing their setup
  6.  Driver programme summary — laps, compounds, run types per driver
  7.  Theoretical best lap — best S1 + S2 + S3 combined
  8.  Sector analysis — best sectors per driver
  9.  Track evolution — how the track got faster over the session
  10. Tyre deg curves — how each compound degrades over a stint
  11. Race pace prediction — estimated race pace ranking from long runs
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LONG_RUN_MIN_LAPS = 5          # Minimum laps to count as a long run
OUTLIER_THRESHOLD_S = 2.5      # Laps this much slower than median are outliers
FUEL_EFFECT_S_PER_LAP = 0.06   # Typical fuel burn-off pace improvement


# ---------------------------------------------------------------------------
# Helper: identify stints
# ---------------------------------------------------------------------------
def _identify_stints(driver_laps):
    """Split a driver's laps into stints based on pit stops."""
    stints = []
    current_stint = []

    for _, lap in driver_laps.sort_values("LapNumber").iterrows():
        is_pit_out = pd.notna(lap.get("PitOutTime"))
        is_pit_in = pd.notna(lap.get("PitInTime"))

        if is_pit_out and current_stint:
            stints.append(current_stint)
            current_stint = []

        # Exclude pit-in and pit-out laps so long-run pace isn't polluted by
        # in-lap / out-lap outliers.
        if pd.notna(lap.get("LapTime")) and not is_pit_out and not is_pit_in:
            current_stint.append(lap)

        if is_pit_in and current_stint:
            stints.append(current_stint)
            current_stint = []

    if current_stint:
        stints.append(current_stint)

    return [s for s in stints if len(s) > 0]


def _clean_lap_times(lap_times_s):
    """Remove outlier lap times and return cleaned list."""
    if len(lap_times_s) < 2:
        return lap_times_s
    median = np.median(lap_times_s)
    return [t for t in lap_times_s if abs(t - median) <= OUTLIER_THRESHOLD_S]


# ---------------------------------------------------------------------------
# Long Run Analysis
# ---------------------------------------------------------------------------
def analyze_long_runs(laps):
    """
    Find sustained stints (5+ laps) to estimate race pace.
    Includes fuel-corrected pace and degradation rate.
    """
    if laps.empty:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        team = dlaps["Team"].iloc[0] if "Team" in dlaps.columns and pd.notna(dlaps["Team"].iloc[0]) else "Unknown"
        stints = _identify_stints(dlaps)

        for stint in stints:
            if len(stint) < LONG_RUN_MIN_LAPS:
                continue

            lap_times = [lap["LapTime"].total_seconds() for lap in stint if pd.notna(lap["LapTime"])]
            if len(lap_times) < LONG_RUN_MIN_LAPS:
                continue

            cleaned = _clean_lap_times(lap_times)
            if len(cleaned) < LONG_RUN_MIN_LAPS:
                continue

            compound = "UNKNOWN"
            for lap in stint:
                c = lap.get("Compound")
                if pd.notna(c):
                    compound = str(c).upper()
                    break

            avg_pace = np.mean(cleaned)
            median_pace = np.median(cleaned)

            # Degradation slope
            deg_per_lap = 0.0
            if len(cleaned) >= 3:
                x = np.arange(len(cleaned))
                coeffs = np.polyfit(x, cleaned, 1)
                deg_per_lap = round(coeffs[0], 3)

            # Fuel correction: assume mid-stint fuel load
            fuel_correction = len(cleaned) * (FUEL_EFFECT_S_PER_LAP / 2)
            fuel_corrected = avg_pace - fuel_correction

            # First/last lap for trend
            first_lap = cleaned[0]
            last_lap = cleaned[-1]

            results.append({
                "driver": driver,
                "team": team,
                "compound": compound,
                "stint_laps": len(cleaned),
                "avg_pace_s": round(avg_pace, 3),
                "median_pace_s": round(median_pace, 3),
                "degradation_per_lap": deg_per_lap,
                "fuel_corrected_pace": round(fuel_corrected, 3),
                "first_lap_s": round(first_lap, 3),
                "last_lap_s": round(last_lap, 3),
                "lap_times": [round(t, 3) for t in cleaned],
            })

    results.sort(key=lambda x: x["fuel_corrected_pace"])
    return results


# ---------------------------------------------------------------------------
# Short Run / Single-Lap Pace
# ---------------------------------------------------------------------------
def analyze_short_runs(laps):
    """Analyze qualifying-simulation laps (push laps)."""
    if laps.empty:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"

        lap_times = [lt.total_seconds() for lt in valid["LapTime"]]
        cleaned = _clean_lap_times(lap_times)
        if not cleaned:
            continue

        best = min(cleaned)
        best_idx = valid["LapTime"].idxmin()
        compound = "UNKNOWN"
        if "Compound" in valid.columns:
            c = valid.loc[best_idx, "Compound"]
            if pd.notna(c):
                compound = str(c).upper()

        consistency = round(np.std(cleaned), 3) if len(cleaned) > 1 else 0.0

        # Top 3 laps
        top_laps = sorted(cleaned)[:3]

        results.append({
            "driver": driver,
            "team": team,
            "best_lap_s": round(best, 3),
            "compound": compound,
            "num_attempts": len(cleaned),
            "consistency": consistency,
            "top_3_avg": round(np.mean(top_laps), 3),
        })

    results.sort(key=lambda x: x["best_lap_s"])

    if results:
        best_time = results[0]["best_lap_s"]
        for r in results:
            r["gap_to_best"] = round(r["best_lap_s"] - best_time, 3)

    for i, r in enumerate(results, 1):
        r["position"] = i

    return results


# ---------------------------------------------------------------------------
# Compound Comparison
# ---------------------------------------------------------------------------
def analyze_compounds(laps):
    """Compare pace across different tire compounds."""
    if laps.empty or "Compound" not in laps.columns:
        return []

    valid = laps.dropna(subset=["LapTime", "Compound"])
    if valid.empty:
        return []

    results = []
    for compound, claps in valid.groupby("Compound"):
        lap_times = [lt.total_seconds() for lt in claps["LapTime"]]
        cleaned = _clean_lap_times(lap_times)
        if not cleaned:
            continue

        num_drivers = claps["Driver"].nunique()
        drivers = sorted(claps["Driver"].unique())

        results.append({
            "compound": str(compound).upper(),
            "avg_pace_s": round(np.mean(cleaned), 3),
            "best_pace_s": round(min(cleaned), 3),
            "median_pace_s": round(np.median(cleaned), 3),
            "num_drivers": num_drivers,
            "num_laps": len(cleaned),
            "drivers": drivers,
        })

    results.sort(key=lambda x: x["best_pace_s"])

    if results:
        fastest = results[0]["best_pace_s"]
        for r in results:
            r["delta_to_fastest"] = round(r["best_pace_s"] - fastest, 3)

    return results


# ---------------------------------------------------------------------------
# Team Pace Ranking
# ---------------------------------------------------------------------------
def analyze_team_ranking(laps):
    """Rank teams by their best representative pace."""
    if laps.empty:
        return []

    driver_data = {}
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue
        best = valid["LapTime"].min().total_seconds()
        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        driver_data[driver] = {"time": best, "team": team}

    teams = {}
    for driver, info in driver_data.items():
        team = info["team"]
        if team not in teams:
            teams[team] = []
        teams[team].append({"driver": driver, "time": info["time"]})

    results = []
    for team, drivers in teams.items():
        drivers.sort(key=lambda x: x["time"])
        best_driver = drivers[0]
        avg_time = np.mean([d["time"] for d in drivers])

        results.append({
            "team": team,
            "best_driver": best_driver["driver"],
            "best_time_s": round(best_driver["time"], 3),
            "avg_time_s": round(avg_time, 3),
            "both_drivers": [{"driver": d["driver"], "time": round(d["time"], 3)} for d in drivers],
        })

    results.sort(key=lambda x: x["best_time_s"])

    if results:
        best = results[0]["best_time_s"]
        for r in results:
            r["gap_to_best_team"] = round(r["best_time_s"] - best, 3)

    for i, r in enumerate(results, 1):
        r["position"] = i

    return results


# ---------------------------------------------------------------------------
# Consistency Analysis
# ---------------------------------------------------------------------------
def analyze_consistency(laps):
    """Rank drivers by lap time consistency."""
    if laps.empty:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if len(valid) < 3:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"

        lap_times = [lt.total_seconds() for lt in valid["LapTime"]]
        cleaned = _clean_lap_times(lap_times)
        if len(cleaned) < 3:
            continue

        std_dev = np.std(cleaned)
        avg_pace = np.mean(cleaned)

        if std_dev < 0.3:
            rating = "EXCELLENT"
        elif std_dev < 0.5:
            rating = "GOOD"
        elif std_dev < 0.8:
            rating = "AVERAGE"
        else:
            rating = "INCONSISTENT"

        results.append({
            "driver": driver,
            "team": team,
            "num_laps": len(cleaned),
            "std_dev": round(std_dev, 3),
            "avg_pace_s": round(avg_pace, 3),
            "consistency_rating": rating,
        })

    results.sort(key=lambda x: x["std_dev"])
    return results


# ---------------------------------------------------------------------------
# NEW: Driver Programme Summary
# ---------------------------------------------------------------------------
def analyze_programmes(laps):
    """
    Summary of each driver's session: total laps, compounds used,
    long runs vs short runs, time on track.
    """
    if laps.empty:
        return []

    results = []
    for driver, dlaps in laps.groupby("Driver"):
        valid = dlaps.dropna(subset=["LapTime"])
        if valid.empty:
            continue

        team = valid["Team"].iloc[0] if "Team" in valid.columns and pd.notna(valid["Team"].iloc[0]) else "Unknown"
        total_laps = len(valid)

        # Compounds used
        compounds_used = []
        if "Compound" in valid.columns:
            compounds_used = sorted(set(str(c).upper() for c in valid["Compound"].dropna().unique()))

        # Stints
        stints = _identify_stints(dlaps)
        num_stints = len(stints)
        long_run_laps = 0
        short_run_laps = 0
        for stint in stints:
            stint_len = sum(1 for lap in stint if pd.notna(lap.get("LapTime")))
            if stint_len >= LONG_RUN_MIN_LAPS:
                long_run_laps += stint_len
            else:
                short_run_laps += stint_len

        # Total time on track
        total_time = sum(lt.total_seconds() for lt in valid["LapTime"])

        # Best and average pace
        best_time = valid["LapTime"].min().total_seconds()
        avg_time = np.mean([lt.total_seconds() for lt in valid["LapTime"]])

        results.append({
            "driver": driver,
            "team": team,
            "total_laps": total_laps,
            "num_stints": num_stints,
            "long_run_laps": long_run_laps,
            "short_run_laps": short_run_laps,
            "compounds_used": compounds_used,
            "total_time_s": round(total_time, 1),
            "total_time_display": f"{int(total_time // 60)}m {int(total_time % 60)}s",
            "best_time_s": round(best_time, 3),
            "avg_time_s": round(avg_time, 3),
        })

    results.sort(key=lambda x: x["total_laps"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# NEW: Theoretical Best Lap (best S1 + S2 + S3)
# ---------------------------------------------------------------------------
def analyze_theoretical_best(laps):
    """
    Calculate each driver's theoretical best by combining personal best
    S1, S2, S3 from any lap. Shows potential vs actual best.
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
        for i, col in enumerate(sector_cols, 1):
            sector_valid = valid.dropna(subset=[col])
            if sector_valid.empty:
                best_sectors[f"s{i}"] = None
                continue
            best_sectors[f"s{i}"] = round(sector_valid[col].min().total_seconds(), 3)

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
            "time_left_on_table": delta,
            **best_sectors,
        })

    results = [r for r in results if r["theoretical_s"] is not None]
    results.sort(key=lambda x: x["theoretical_s"])

    if results:
        best = results[0]["theoretical_s"]
        for i, r in enumerate(results, 1):
            r["position"] = i
            r["gap_to_best"] = round(r["theoretical_s"] - best, 3)

    return results


# ---------------------------------------------------------------------------
# NEW: Sector Analysis
# ---------------------------------------------------------------------------
def analyze_sectors(laps):
    """
    Best sector times per driver across the whole session.
    Shows where each driver is strong/weak.
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

        sectors = {}
        for i, col in enumerate(sector_cols, 1):
            sv = valid.dropna(subset=[col])
            if not sv.empty:
                sectors[f"s{i}"] = round(sv[col].min().total_seconds(), 3)
            else:
                sectors[f"s{i}"] = None

        results.append({"driver": driver, "team": team, **sectors})

    if not results:
        return []

    # Deltas + classification
    for si in ["s1", "s2", "s3"]:
        valid_vals = [r[si] for r in results if r[si] is not None]
        if valid_vals:
            best_val = min(valid_vals)
            for r in results:
                if r[si] is not None:
                    delta = round(r[si] - best_val, 3)
                    r[f"{si}_delta"] = delta
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

    # Sort by sum of best sectors
    for r in results:
        total = sum(r[f"s{i}"] for i in range(1, 4) if r[f"s{i}"] is not None)
        r["total_sectors_s"] = round(total, 3) if total else 999
    results.sort(key=lambda x: x["total_sectors_s"])

    for i, r in enumerate(results, 1):
        r["position"] = i

    return results


# ---------------------------------------------------------------------------
# NEW: Track Evolution
# ---------------------------------------------------------------------------
def analyze_track_evolution(laps):
    """
    Track how the circuit got faster through the practice session
    as rubber was laid down and track temperature changed.
    """
    if laps.empty:
        return []

    valid = laps.dropna(subset=["LapTime", "LapNumber"]).copy()
    if valid.empty or len(valid) < 5:
        return []

    valid["LapTimeS"] = valid["LapTime"].apply(lambda x: x.total_seconds())

    max_lap = valid["LapNumber"].max()
    min_lap = valid["LapNumber"].min()
    if max_lap == min_lap:
        return []

    # Split into 5 phases for practice (longer sessions)
    num_phases = 5
    range_size = (max_lap - min_lap) / num_phases
    phase_names = ["Opening", "Early", "Middle", "Late", "Final"]

    phases = []
    for i in range(num_phases):
        start = min_lap + i * range_size
        end = min_lap + (i + 1) * range_size
        phase_laps = valid[(valid["LapNumber"] >= start) & (valid["LapNumber"] < end + (1 if i == num_phases - 1 else 0))]
        if phase_laps.empty:
            continue

        fastest_idx = phase_laps["LapTimeS"].idxmin()
        fastest = phase_laps.loc[fastest_idx]

        # Remove outliers for average
        times = phase_laps["LapTimeS"].tolist()
        cleaned = _clean_lap_times(times)
        avg = np.mean(cleaned) if cleaned else np.mean(times)

        phases.append({
            "phase": phase_names[i] if i < len(phase_names) else f"Phase {i+1}",
            "fastest_time_s": round(fastest["LapTimeS"], 3),
            "fastest_driver": fastest["Driver"],
            "avg_time_s": round(avg, 3),
            "num_laps": len(phase_laps),
            "num_drivers": phase_laps["Driver"].nunique(),
            "lap_range": f"{int(start)}-{int(end)}",
        })

    if len(phases) >= 2:
        first_fastest = phases[0]["fastest_time_s"]
        for p in phases:
            p["evolution_delta"] = round(p["fastest_time_s"] - first_fastest, 3)

    return phases


# ---------------------------------------------------------------------------
# NEW: Tyre Degradation Curves per Compound
# ---------------------------------------------------------------------------
def analyze_tyre_deg_curves(laps):
    """
    How each tyre compound degrades over a stint.
    Aggregates long run data per compound to show expected deg.
    """
    if laps.empty or "Compound" not in laps.columns:
        return []

    compound_stints = {}

    for driver, dlaps in laps.groupby("Driver"):
        stints = _identify_stints(dlaps)
        for stint in stints:
            if len(stint) < LONG_RUN_MIN_LAPS:
                continue

            compound = "UNKNOWN"
            for lap in stint:
                c = lap.get("Compound")
                if pd.notna(c):
                    compound = str(c).upper()
                    break

            if compound == "UNKNOWN":
                continue

            lap_times = [lap["LapTime"].total_seconds() for lap in stint if pd.notna(lap["LapTime"])]
            cleaned = _clean_lap_times(lap_times)
            if len(cleaned) < LONG_RUN_MIN_LAPS:
                continue

            if compound not in compound_stints:
                compound_stints[compound] = []
            compound_stints[compound].append({
                "driver": driver,
                "times": cleaned,
                "deg": 0.0,
            })

    results = []
    for compound, stints in compound_stints.items():
        all_degs = []
        all_avg_paces = []
        total_stint_laps = 0

        for stint in stints:
            times = stint["times"]
            total_stint_laps += len(times)
            all_avg_paces.append(np.mean(times))
            if len(times) >= 3:
                coeffs = np.polyfit(np.arange(len(times)), times, 1)
                all_degs.append(coeffs[0])

        avg_deg = round(np.mean(all_degs), 3) if all_degs else 0.0
        avg_pace = round(np.mean(all_avg_paces), 3) if all_avg_paces else 0.0

        if avg_deg < 0.01:
            trend = "STABLE"
        elif avg_deg < 0.05:
            trend = "LOW"
        elif avg_deg < 0.10:
            trend = "MODERATE"
        else:
            trend = "HIGH"

        results.append({
            "compound": compound,
            "avg_deg_per_lap": avg_deg,
            "avg_pace_s": avg_pace,
            "num_stints": len(stints),
            "total_laps": total_stint_laps,
            "drivers": sorted(set(s["driver"] for s in stints)),
            "trend": trend,
        })

    # Sort: SOFT first, MEDIUM, HARD
    compound_order = {"SOFT": 0, "MEDIUM": 1, "HARD": 2, "INTERMEDIATE": 3, "WET": 4}
    results.sort(key=lambda x: compound_order.get(x["compound"], 5))

    return results


# ---------------------------------------------------------------------------
# NEW: Race Pace Prediction
# ---------------------------------------------------------------------------
def analyze_race_pace_prediction(laps):
    """
    Estimate race pace ranking from long run data.
    Fuel-corrects and aggregates to show expected race-day order.
    """
    if laps.empty:
        return []

    driver_long_runs = {}
    for driver, dlaps in laps.groupby("Driver"):
        team = dlaps["Team"].iloc[0] if "Team" in dlaps.columns and pd.notna(dlaps["Team"].iloc[0]) else "Unknown"
        stints = _identify_stints(dlaps)

        long_run_paces = []
        for stint in stints:
            if len(stint) < LONG_RUN_MIN_LAPS:
                continue
            lap_times = [lap["LapTime"].total_seconds() for lap in stint if pd.notna(lap["LapTime"])]
            cleaned = _clean_lap_times(lap_times)
            if len(cleaned) < LONG_RUN_MIN_LAPS:
                continue

            # Fuel-correct: assume mid-stint fuel
            fuel_correction = len(cleaned) * (FUEL_EFFECT_S_PER_LAP / 2)
            corrected = np.mean(cleaned) - fuel_correction
            long_run_paces.append(corrected)

        if long_run_paces:
            driver_long_runs[driver] = {
                "team": team,
                "avg_race_pace": np.mean(long_run_paces),
                "best_race_pace": min(long_run_paces),
                "num_runs": len(long_run_paces),
            }

    if not driver_long_runs:
        return []

    results = []
    for driver, data in driver_long_runs.items():
        results.append({
            "driver": driver,
            "team": data["team"],
            "predicted_race_pace_s": round(data["avg_race_pace"], 3),
            "best_run_pace_s": round(data["best_race_pace"], 3),
            "num_long_runs": data["num_runs"],
        })

    results.sort(key=lambda x: x["predicted_race_pace_s"])

    if results:
        best = results[0]["predicted_race_pace_s"]
        for i, r in enumerate(results, 1):
            r["position"] = i
            r["gap_to_fastest"] = round(r["predicted_race_pace_s"] - best, 3)

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze_practice(laps):
    """Run all practice analysis modules."""
    modules = {
        "long_runs": (analyze_long_runs, []),
        "short_runs": (analyze_short_runs, []),
        "compounds": (analyze_compounds, []),
        "team_ranking": (analyze_team_ranking, []),
        "consistency": (analyze_consistency, []),
        "programmes": (analyze_programmes, []),
        "theoretical_best": (analyze_theoretical_best, []),
        "sectors": (analyze_sectors, []),
        "track_evolution": (analyze_track_evolution, []),
        "tyre_deg_curves": (analyze_tyre_deg_curves, []),
        "race_pace_prediction": (analyze_race_pace_prediction, []),
    }

    results = {}
    for key, (func, default) in modules.items():
        try:
            results[key] = func(laps)
        except Exception as exc:
            logger.warning("%s analysis failed: %s", key, exc)
            results[key] = default

    return results


def get_practice_summary(analysis):
    """Build summary stats for the practice header cards."""
    long_runs = analysis.get("long_runs", [])
    short_runs = analysis.get("short_runs", [])
    compounds = analysis.get("compounds", [])
    team_ranking = analysis.get("team_ranking", [])
    consistency = analysis.get("consistency", [])
    programmes = analysis.get("programmes", [])
    race_pred = analysis.get("race_pace_prediction", [])

    fastest_driver = short_runs[0]["driver"] if short_runs else "—"
    fastest_time = short_runs[0]["best_lap_s"] if short_runs else 0

    best_long_run = long_runs[0]["driver"] if long_runs else "—"
    best_long_run_pace = long_runs[0]["fuel_corrected_pace"] if long_runs else 0

    compounds_tested = len(compounds)
    most_consistent = consistency[0]["driver"] if consistency else "—"
    top_team = team_ranking[0]["team"] if team_ranking else "—"

    total_laps = sum(p["total_laps"] for p in programmes) if programmes else 0
    most_laps_driver = programmes[0]["driver"] if programmes else "—"
    most_laps = programmes[0]["total_laps"] if programmes else 0

    race_pace_leader = race_pred[0]["driver"] if race_pred else "—"

    return {
        "fastest_driver": fastest_driver,
        "fastest_time": fastest_time,
        "best_long_run": best_long_run,
        "best_long_run_pace": best_long_run_pace,
        "compounds_tested": compounds_tested,
        "most_consistent": most_consistent,
        "top_team": top_team,
        "total_laps": total_laps,
        "most_laps_driver": most_laps_driver,
        "most_laps": most_laps,
        "race_pace_leader": race_pace_leader,
    }
