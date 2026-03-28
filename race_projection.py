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
import re

from practice import analyze_race_pace_prediction

logger = logging.getLogger(__name__)

PRACTICE_SESSION_ORDER = {
    "Practice 1": 1,
    "Practice 2": 2,
    "Practice 3": 3,
}
PRACTICE_SESSION_LABELS = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
}


def _sort_practice_sessions(session_names):
    """Return practice session labels in weekend order."""
    return sorted(
        set(session_names),
        key=lambda name: (PRACTICE_SESSION_ORDER.get(name, 99), name),
    )


def _format_practice_sessions(session_names):
    """Return ordered practice sessions with compact display labels."""
    return [
        PRACTICE_SESSION_LABELS.get(name, name)
        for name in _sort_practice_sessions(session_names)
    ]


def _driver_display_name(code, full_name=None, broadcast_name=None):
    """Return a readable surname-style driver label for projection tables."""
    for raw_value in (full_name, broadcast_name):
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if not value:
                continue
            if "," in value:
                parts = [part.strip() for part in value.split(",") if part.strip()]
                if parts:
                    return parts[0].title()
            parts = re.split(r"\s+", value)
            if parts:
                return parts[-1].replace(".", "").title()
    return str(code).strip() if code is not None else "-"


def _projection_driver_display_map(session):
    """Build readable projection labels keyed by driver code."""
    if session is None:
        return {}

    try:
        results = session.results.reset_index()
    except Exception:
        return {}

    display_map = {}
    for _, row in results.iterrows():
        code = row.get("Abbreviation") or row.get("BroadcastName") or row.get("DriverNumber")
        if not code:
            continue
        display_map[str(code)] = _driver_display_name(
            code,
            full_name=row.get("FullName"),
            broadcast_name=row.get("BroadcastName"),
        )
    return display_map


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
                "sessions_used": _sort_practice_sessions(data["sessions"]),
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


def project_race_finish(quali_analysis, practice_sessions=None, session=None):
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
    driver_display_map = _projection_driver_display_map(session)
    theoretical_map, improvement_map, tyre_usage_map = _build_maps(quali_analysis)

    projected = []
    for grid_row in sectors:
        driver = grid_row["driver"]
        score = grid_row["position"] * 0.55
        reasons = [f"Qualifying position: P{grid_row['position']}"]

        pace_row = practice_pace.get(driver)
        if pace_row:
            score += pace_row["position"] * 0.35
            reasons.append(f"Weekend race pace rank: P{pace_row['position']}")
            if pace_row["gap_to_best"] <= 0.15:
                score -= 0.2
            elif pace_row["gap_to_best"] >= 0.6:
                score += 0.35
        else:
            reasons.append("No clear long-run pace sample")
            score += grid_row["position"] * 0.10

        theory_row = theoretical_map.get(driver)
        if theory_row:
            score += theory_row["theoretical_position"] * 0.10
            if theory_row["time_lost_s"] >= 0.2:
                score -= 0.15
                reasons.append(f"Theoretical qualifying lap left {theory_row['time_lost_s']:.3f}s on the table")
            else:
                reasons.append(f"Theoretical qualifying rank: P{theory_row['theoretical_position']}")

        improvement_row = improvement_map.get(driver)
        if improvement_row:
            if improvement_row["improvement_s"] >= 0.45:
                score -= 0.1
            reasons.append(f"Built {improvement_row['improvement_s']:.3f}s through qualifying runs")

        tyre_row = tyre_usage_map.get(driver)
        if tyre_row and tyre_row.get("best_compound") == "MEDIUM":
            reasons.append("Best qualifying lap came on medium tyres")

        projected.append(
            {
                "driver": driver,
                "driver_display": driver_display_map.get(driver, driver),
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

    practice_sessions_used = _sort_practice_sessions(
        session_name
        for row in projected
        for session_name in row.get("practice_sessions_used", [])
    )
    confidence = "MEDIUM" if practice_sessions_used else "LOW"

    risers = [row for row in projected if row["position_change"] > 0]
    biggest_riser = max(risers, key=lambda x: x["position_change"])["driver"] if risers else None
    biggest_riser_display = next(
        (row["driver_display"] for row in projected if row["driver"] == biggest_riser),
        "-",
    ) if biggest_riser else "-"

    summary = {
        "predicted_winner": projected[0]["driver_display"] if projected else "-",
        "biggest_riser": biggest_riser_display,
        "confidence": confidence,
        "practice_sessions_used": _format_practice_sessions(practice_sessions_used),
        "has_practice_pace": bool(practice_sessions_used),
    }

    return {
        "projected_finish": projected,
        "summary": summary,
    }
