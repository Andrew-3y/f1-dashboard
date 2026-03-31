"""
validation.py - Session data audit checks
=========================================

Runs lightweight sanity checks over official and derived dashboard data so
the UI can surface whether a session looks trustworthy, questionable, or
clearly broken.
"""

def empty_validation():
    """Return a stable empty validation payload."""
    return {
        "overall_status": "WARN",
        "summary": "No validation run",
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
        "checks": [],
    }


def _make_check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _overall_status(checks):
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if any(check["status"] == "WARN" for check in checks):
        return "WARN"
    return "PASS"


def _summarize(checks):
    pass_count = sum(1 for check in checks if check["status"] == "PASS")
    warn_count = sum(1 for check in checks if check["status"] == "WARN")
    fail_count = sum(1 for check in checks if check["status"] == "FAIL")
    overall = _overall_status(checks)
    if overall == "PASS":
        summary = "No obvious data issues detected"
    elif overall == "WARN":
        summary = "Some checks need caution"
    else:
        summary = "Suspicious data detected"
    return {
        "overall_status": overall,
        "summary": summary,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "checks": checks,
    }


def _is_sequential_positions(rows):
    positions = [row.get("position") for row in rows if row.get("position") is not None]
    if not positions:
        return False
    return positions == list(range(1, len(positions) + 1))


def _numeric_gaps(rows):
    return [row["gap_seconds"] for row in rows if row.get("gap_seconds") is not None]


def _check_leaderboard(rows, label):
    checks = []
    if not rows:
        checks.append(_make_check(f"{label} leaderboard", "FAIL", "No rows available"))
        return checks

    if _is_sequential_positions(rows):
        checks.append(_make_check(f"{label} positions", "PASS", "Positions are sequential"))
    else:
        checks.append(_make_check(f"{label} positions", "FAIL", "Positions are not sequential"))

    numeric_gaps = _numeric_gaps(rows)
    if numeric_gaps and any(gap < 0 for gap in numeric_gaps):
        checks.append(_make_check(f"{label} gaps", "FAIL", "Negative gaps detected"))
    elif numeric_gaps and numeric_gaps != sorted(numeric_gaps):
        checks.append(_make_check(f"{label} gaps", "WARN", "Gaps are not monotonic"))
    else:
        checks.append(_make_check(f"{label} gaps", "PASS", "Gap ordering looks sane"))

    if label in ("Race", "Sprint"):
        absurd_gaps = [gap for gap in numeric_gaps if gap > 1500]
        if absurd_gaps:
            checks.append(_make_check(f"{label} gap scale", "FAIL", f"One or more {label.lower()} gaps are implausibly large"))
        else:
            checks.append(_make_check(f"{label} gap scale", "PASS", f"{label} gaps are within a plausible range"))

    laps = [row.get("total_laps") for row in rows if row.get("total_laps") is not None]
    if laps and all(isinstance(lap, int) and lap >= 0 for lap in laps):
        checks.append(_make_check(f"{label} lap counts", "PASS", "Lap counts are non-negative"))
    else:
        checks.append(_make_check(f"{label} lap counts", "WARN", "Some lap counts are missing or invalid"))

    return checks


def _check_sorted_times(rows, key, label):
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return _make_check(label, "WARN", "No timing rows available")
    return (
        _make_check(label, "PASS", "Times are sorted fastest-to-slowest")
        if values == sorted(values)
        else _make_check(label, "WARN", "Timing order does not match the displayed ranking")
    )


def _check_anomalies(alerts):
    if not alerts:
        return [_make_check("Anomaly inputs", "PASS", "No anomalies were flagged")]

    bad_rows = 0
    for alert in alerts:
        delta = alert.get("delta")
        lap_time = alert.get("lap_time_s")
        rolling_avg = alert.get("rolling_avg")
        if delta is None or lap_time is None or rolling_avg is None:
            bad_rows += 1
            continue
        if delta <= 0 or lap_time <= rolling_avg:
            bad_rows += 1

    if bad_rows:
        return [_make_check("Anomaly inputs", "FAIL", f"{bad_rows} anomaly row(s) failed the pace-loss sanity check")]
    return [_make_check("Anomaly inputs", "PASS", "Anomaly rows match the displayed pace-loss logic")]


def _check_accuracy_block(block, label):
    if not block:
        return [_make_check(label, "WARN", "No accuracy data available")]
    if not block.get("available"):
        return [_make_check(label, "WARN", "Accuracy comparison not available for this session")]

    compared = block.get("compared_drivers", 0)
    exact = block.get("exact_matches", 0)
    mae = block.get("mean_abs_error")
    if compared <= 0 or exact < 0 or (mae is not None and mae < 0):
        return [_make_check(label, "FAIL", "Accuracy metrics contain invalid values")]
    return [_make_check(label, "PASS", f"Compared {compared} shared driver(s) against official results")]


def validate_session(session_category, leaderboard, analysis, session_info=None):
    """Build a lightweight audit report for the current dashboard session."""
    checks = []
    session_type = (session_info or {}).get("session_type")

    if session_category == "race":
        leaderboard_label = "Sprint" if session_type == "Sprint" else "Race"
        checks.extend(_check_leaderboard(leaderboard, leaderboard_label))
        checks.extend(_check_anomalies(analysis.get("alerts", [])))
        if session_type == "Sprint":
            checks.extend(_check_accuracy_block(analysis.get("race_projection_accuracy", {}), "Sprint projection accuracy"))
        else:
            checks.extend(_check_accuracy_block(analysis.get("race_projection_accuracy", {}), "Race projection accuracy"))
    elif session_category == "qualifying":
        label = "Sprint Shootout" if session_type == "Sprint Shootout" else "Qualifying"
        sectors = analysis.get("quali_analysis", {}).get("sectors", [])
        if sectors:
            if _is_sequential_positions(sectors):
                checks.append(_make_check(f"{label} positions", "PASS", "Positions are sequential"))
            else:
                checks.append(_make_check(f"{label} positions", "FAIL", "Positions are not sequential"))

            lap_values = [row.get("best_lap_s") for row in sectors]
            if lap_values and all(value is not None and value > 0 for value in lap_values):
                checks.append(_make_check(f"{label} lap values", "PASS", "Official best-lap values look valid"))
            else:
                checks.append(_make_check(f"{label} lap values", "WARN", "One or more official best-lap values are missing"))
        else:
            checks.append(_make_check(f"{label} leaderboard", "FAIL", "No rows available"))
        accuracy_label = "Sprint projection accuracy" if session_type == "Sprint Shootout" else "FP3 projection accuracy"
        checks.extend(_check_accuracy_block(analysis.get("quali_summary", {}).get("qualifying_projection_accuracy", {}), accuracy_label))
    elif session_category == "practice":
        checks.extend(_check_leaderboard(leaderboard, "Practice"))
        short_runs = analysis.get("practice_analysis", {}).get("short_runs", [])
        checks.append(_check_sorted_times(short_runs, "best_lap_s", "Practice short-run order"))
        projection = analysis.get("practice_analysis", {}).get("qualifying_projection", [])
        if session_type == "Practice 3":
            if projection:
                fp3_positions = [row.get("fp3_position") for row in projection if row.get("fp3_position") is not None]
                if fp3_positions and any(position <= 0 for position in fp3_positions):
                    checks.append(_make_check("FP3 reference positions", "FAIL", "Projection contains invalid FP3 positions"))
                else:
                    checks.append(_make_check("FP3 reference positions", "PASS", "Projection positions look valid"))
            else:
                checks.append(_make_check("FP3 projection", "WARN", "No FP3 qualifying projection available"))
        else:
            checks.append(_make_check("FP3 projection", "PASS", "FP3-only projection is not expected on this session"))
    else:
        return empty_validation()

    if not checks:
        return empty_validation()

    return _summarize(checks)
