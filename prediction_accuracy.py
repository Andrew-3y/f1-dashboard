"""
prediction_accuracy.py - Compare projections against official results
====================================================================

Builds deterministic accuracy summaries for:
  - FP3 projected qualifying order vs actual qualifying classification
  - pre-race projected finish vs actual race classification

These summaries only use official loaded results/leaderboards. If the
actual result is missing or incomplete, the accuracy block stays empty.
"""


def empty_accuracy():
    """Return a stable empty accuracy payload."""
    return {
        "available": False,
        "rating": "N/A",
        "compared_drivers": 0,
        "exact_matches": 0,
        "exact_match_pct": 0.0,
        "mean_abs_error": None,
        "top_3_hits": 0,
        "top_3_total": 0,
        "top_10_hits": 0,
        "top_10_total": 0,
        "leader_hit": False,
        "predicted_leader": "-",
        "actual_leader": "-",
        "rows": [],
    }


def _safe_position(row, key):
    """Return an integer position when possible."""
    value = row.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rating(exact_match_pct, mean_abs_error):
    """Classify leaderboard accuracy in broad human-readable terms."""
    if mean_abs_error is None:
        return "N/A"
    if exact_match_pct >= 45 or mean_abs_error <= 1.5:
        return "STRONG"
    if exact_match_pct >= 25 or mean_abs_error <= 3.0:
        return "SOLID"
    return "ROUGH"


def compare_predictions(
    predicted_rows,
    actual_rows,
    *,
    predicted_pos_key="projected_position",
    actual_pos_key="position",
    driver_key="driver",
    predicted_name_key="driver_display",
    actual_name_key="driver",
):
    """Compare an ordered prediction against an official ordered result."""
    if not predicted_rows or not actual_rows:
        return empty_accuracy()

    actual_by_driver = {}
    for row in actual_rows:
        driver = row.get(driver_key)
        position = _safe_position(row, actual_pos_key)
        if not driver or position is None:
            continue
        actual_by_driver[str(driver)] = row

    compared_rows = []
    exact_matches = 0
    total_abs_error = 0

    for row in predicted_rows:
        driver = row.get(driver_key)
        predicted_position = _safe_position(row, predicted_pos_key)
        if not driver or predicted_position is None:
            continue

        actual = actual_by_driver.get(str(driver))
        if not actual:
            continue

        actual_position = _safe_position(actual, actual_pos_key)
        if actual_position is None:
            continue

        error = abs(predicted_position - actual_position)
        if error == 0:
            exact_matches += 1
        total_abs_error += error

        compared_rows.append(
            {
                "driver": str(driver),
                "driver_display": row.get(predicted_name_key) or actual.get(actual_name_key) or str(driver),
                "predicted_position": predicted_position,
                "actual_position": actual_position,
                "position_error": error,
                "delta": actual_position - predicted_position,
                "exact_match": error == 0,
            }
        )

    if not compared_rows:
        return empty_accuracy()

    compared_rows.sort(key=lambda row: row["predicted_position"])
    compared_drivers = len(compared_rows)
    exact_match_pct = round((exact_matches / compared_drivers) * 100, 1)
    mean_abs_error = round(total_abs_error / compared_drivers, 2)

    predicted_top_3 = {row["driver"] for row in compared_rows if row["predicted_position"] <= 3}
    actual_top_3 = {row["driver"] for row in compared_rows if row["actual_position"] <= 3}
    top_3_total = min(3, compared_drivers)
    top_3_hits = len(predicted_top_3 & actual_top_3)

    predicted_top_10 = {row["driver"] for row in compared_rows if row["predicted_position"] <= 10}
    actual_top_10 = {row["driver"] for row in compared_rows if row["actual_position"] <= 10}
    top_10_total = min(10, compared_drivers)
    top_10_hits = len(predicted_top_10 & actual_top_10)

    predicted_leader = next((row["driver_display"] for row in compared_rows if row["predicted_position"] == 1), "-")
    actual_leader = next((row["driver_display"] for row in compared_rows if row["actual_position"] == 1), "-")
    leader_hit = any(
        row["predicted_position"] == 1 and row["actual_position"] == 1
        for row in compared_rows
    )

    return {
        "available": True,
        "rating": _rating(exact_match_pct, mean_abs_error),
        "compared_drivers": compared_drivers,
        "exact_matches": exact_matches,
        "exact_match_pct": exact_match_pct,
        "mean_abs_error": mean_abs_error,
        "top_3_hits": top_3_hits,
        "top_3_total": top_3_total,
        "top_10_hits": top_10_hits,
        "top_10_total": top_10_total,
        "leader_hit": leader_hit,
        "predicted_leader": predicted_leader,
        "actual_leader": actual_leader,
        "rows": compared_rows,
    }
