"""
race_projection.py - Pre-race finish projection
==============================================

Builds a projected end-of-race leaderboard for the qualifying page using
the full pre-race weekend context:
  - qualifying classification / grid position
  - qualifying form (theoretical pace and lap progression)
  - practice long-run race pace when available

The output is intentionally framed as a projection, not a certainty.
"""

import logging

from practice import analyze_race_pace_prediction

logger = logging.getLogger(__name__)


def _aggregate_practice_race_pace(practice_sessions):
    """Combine practice race-pace rankings across available sessions."""
    aggregated = {}

    for practice in practice_sessions or []:
        laps = practice.get("laps")
        if laps is None or getattr(laps, "empty", True):
            continue

        try:
            session_results = analyze_race_pace_prediction(laps)
        except Exception as exc:
            logger.warning("Race pace projection failed for %s: %s", practice.get("session_type", "practice"), exc)
            continue

        for row in session_results:
            driver = row["driver"]
            entry = aggregated.setdefault(
                driver,
                {
                    "team": row["team"],
                    "pace_values": [],
                    "best_gaps": [],
                    "sessions": [],
                },
            )
            entry["pace_values"].append(row["predicted_race_pace_s"])
            entry["best_gaps"].append(row.get("gap_to_fastest", 0.0))
            entry["sessions"].append(practice.get("session_type", "Practice"))

    if not aggregated:
        return {}

    ranked = []
    for driver, data in aggregated.items():
        avg_pace = sum(data["pace_values"]) / len(data["pace_values"])
        avg_gap = sum(data["best_gaps"]) / len(data["best_gaps"]) if data["best_gaps"] else 0.0
        ranked.append(
            {
                "driver": driver,
                "team": data["team"],
                "avg_pace": round(avg_pace, 3),
                "avg_gap": round(avg_gap, 3),
                "sessions_used": sorted(set(data["sessions"])),
            }
        )

    ranked.sort(key=lambda x: x["avg_pace"])
    best = ranked[0]["avg_pace"]
    for pos, row in enumerate(ranked, start=1):
        row["position"] = pos
        row["gap_to_best"] = round(row["avg_pace"] - best, 3)

    return {row["driver"]: row for row in ranked}


def _build_maps(quali_analysis):
    """Index qualifying analysis tables by driver for quick lookups."""
    theoretical = {row["driver"]: row for row in quali_analysis.get("theoretical_best", [])}
    improvement = {row["driver"]: row for row in quali_analysis.get("improvement", [])}
    tyre_usage = {row["driver"]: row for row in quali_analysis.get("tyre_usage", [])}
    return theoretical, improvement, tyre_usage


def project_race_finish(quali_analysis, practice_sessions=None):
    """
    Build an ordered pre-race finish projection.

    Returns a dict with:
      projected_finish: ordered list of projected results
      summary: header-level stats about the projection
    """
    sectors = quali_analysis.get("sectors", [])
    if not sectors:
        return {
            "projected_finish": [],
            "summary": {
                "predicted_winner": "-",
                "biggest_riser": "-",
                "confidence": "LOW",
                "practice_sessions_used": [],
                "has_practice_pace": False,
            },
        }

    practice_pace = _aggregate_practice_race_pace(practice_sessions or [])
    theoretical_map, improvement_map, tyre_usage_map = _build_maps(quali_analysis)

    projected = []
    for grid_row in sectors:
        driver = grid_row["driver"]
        score = grid_row["position"] * 0.55
        reasons = [f"starts P{grid_row['position']}"]

        pace_row = practice_pace.get(driver)
        if pace_row:
            score += pace_row["position"] * 0.35
            reasons.append(f"practice race pace P{pace_row['position']}")
            if pace_row["gap_to_best"] <= 0.15:
                score -= 0.2
            elif pace_row["gap_to_best"] >= 0.6:
                score += 0.35
        else:
            reasons.append("no long-run pace signal")
            score += grid_row["position"] * 0.10

        theory_row = theoretical_map.get(driver)
        if theory_row:
            score += theory_row["theoretical_position"] * 0.10
            if theory_row["time_lost_s"] >= 0.2:
                score -= 0.15
                reasons.append(f"{theory_row['time_lost_s']}s left in hand")
            else:
                reasons.append(f"theoretical P{theory_row['theoretical_position']}")

        improvement_row = improvement_map.get(driver)
        if improvement_row:
            if improvement_row["improvement_s"] >= 0.45:
                score -= 0.1
            reasons.append(f"improved {improvement_row['improvement_s']}s through session")

        tyre_row = tyre_usage_map.get(driver)
        if tyre_row and tyre_row.get("best_compound") == "MEDIUM":
            reasons.append("showed speed on medium tyre")

        projected.append(
            {
                "driver": driver,
                "team": grid_row["team"],
                "starting_position": grid_row["position"],
                "qualifying_lap_s": grid_row["best_lap_s"],
                "projected_score": round(score, 3),
                "practice_race_pace_position": pace_row["position"] if pace_row else None,
                "practice_sessions_used": pace_row["sessions_used"] if pace_row else [],
                "reasons": reasons[:3],
            }
        )

    projected.sort(key=lambda x: x["projected_score"])
    for pos, row in enumerate(projected, start=1):
        row["projected_position"] = pos
        row["position_change"] = row["starting_position"] - pos
        change = row["position_change"]
        if change >= 3:
            row["trend"] = "strong-riser"
        elif change > 0:
            row["trend"] = "riser"
        elif change <= -3:
            row["trend"] = "strong-faller"
        elif change < 0:
            row["trend"] = "faller"
        else:
            row["trend"] = "steady"

    practice_sessions_used = sorted(
        {
            session_name
            for row in projected
            for session_name in row.get("practice_sessions_used", [])
        }
    )
    confidence = "MEDIUM" if practice_sessions_used else "LOW"

    risers = [row for row in projected if row["position_change"] > 0]
    biggest_riser = max(risers, key=lambda x: x["position_change"])["driver"] if risers else None

    summary = {
        "predicted_winner": projected[0]["driver"] if projected else "-",
        "biggest_riser": biggest_riser or "-",
        "confidence": confidence,
        "practice_sessions_used": practice_sessions_used,
        "has_practice_pace": bool(practice_sessions_used),
    }

    return {
        "projected_finish": projected,
        "summary": summary,
    }
