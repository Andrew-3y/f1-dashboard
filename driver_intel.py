"""
driver_intel.py - Driver-level season intelligence
=================================================

Builds a dedicated driver page that answers a different question from the
season summary page: "What kind of season is this driver having, and how do
they compare with the rest of the grid right now?"
"""

import datetime
import logging
from statistics import mean, pstdev

import fastf1
import pandas as pd

from data_handler import load_session


logger = logging.getLogger(__name__)

_driver_cache = {}


def empty_driver_intelligence():
    """Return a stable empty payload for the driver template."""
    return {
        "meta": {
            "year": None,
            "window": 5,
            "window_label": "Last 5 rounds",
            "driver": "-",
            "completed_rounds": 0,
            "latest_event": "-",
            "available_drivers": [],
        },
        "summary": {
            "form_rank": "-",
            "trend": "-",
            "avg_quali": "-",
            "avg_race": "-",
            "avg_gain": "-",
            "recent_points": "-",
        },
        "profile": {},
        "grid_ranks": [],
        "teammate_context": {},
        "recent_results": [],
        "strengths": [],
        "watchouts": [],
    }


def _safe_int(value):
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value):
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values, digits=2):
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return round(mean(cleaned), digits)


def _recent_previous_split(values, window):
    if not values:
        return [], []
    recent = values[-window:]
    previous = values[-(window * 2):-window] if len(values) > window else []
    return recent, previous


def _trend(delta):
    if delta is None:
        return "NEW"
    if delta >= 7:
        return "SURGING"
    if delta >= 2:
        return "RISING"
    if delta <= -7:
        return "SLIDING"
    if delta <= -2:
        return "COOLING"
    return "STABLE"


def _trend_arrow(trend):
    return {
        "SURGING": "UP",
        "RISING": "UP",
        "STABLE": "FLAT",
        "COOLING": "DOWN",
        "SLIDING": "DOWN",
        "NEW": "NEW",
    }.get(trend, "FLAT")


def _round_score(quali_position=None, race_position=None, gained=None, points=None):
    """Match the season page's coarse form scoring so both views stay aligned."""
    score = 0.0
    if race_position is not None:
        score += max(0, 21 - race_position) * 3.0
    if quali_position is not None:
        score += max(0, 21 - quali_position) * 1.25
    if gained is not None:
        score += max(-5, min(8, gained)) + 5
    if points is not None:
        score += min(25.0, max(0.0, points)) * 1.4
    return round(score, 2)


def _completed_rounds(year):
    """Return completed grand prix rounds for a season."""
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    now = pd.Timestamp(datetime.datetime.now(datetime.timezone.utc))
    rounds = []

    for _, event in schedule.iterrows():
        round_number = _safe_int(event.get("RoundNumber"))
        if not round_number:
            continue

        race_ts = None
        for idx in range(1, 6):
            session_name = event.get(f"Session{idx}")
            session_date = event.get(f"Session{idx}DateUtc")
            if pd.isna(session_name) or pd.isna(session_date):
                continue

            timestamp = pd.Timestamp(session_date)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")

            if str(session_name) == "Race":
                race_ts = timestamp

        if race_ts is not None and race_ts <= now:
            rounds.append(
                {
                    "round_number": round_number,
                    "event_name": str(event.get("EventName", f"Round {round_number}")),
                }
            )

    return rounds


def _session_results_rows(session):
    """Normalize FastF1 session results to plain dictionaries."""
    if session is None:
        return []

    try:
        results = session.results
    except Exception:
        return []

    if results is None or getattr(results, "empty", True):
        return []

    rows = results.reset_index()
    if "Position" not in rows.columns:
        return []

    rows = rows.dropna(subset=["Position"]).copy()
    if rows.empty:
        return []

    normalized = []
    for _, row in rows.iterrows():
        position = _safe_int(row.get("Position"))
        driver = row.get("Abbreviation") or row.get("BroadcastName") or row.get("DriverNumber")
        team = row.get("TeamName") or "Unknown"
        if position is None or not driver:
            continue

        normalized.append(
            {
                "driver": str(driver),
                "team": str(team),
                "position": position,
                "grid_position": _safe_int(row.get("GridPosition")),
                "points": _safe_float(row.get("Points")) or 0.0,
                "status": str(row.get("Status")) if pd.notna(row.get("Status")) else "",
            }
        )

    return sorted(normalized, key=lambda row: row["position"])


def _build_round_snapshots(year, window):
    """Load the recent completed qualifying and race result sets."""
    completed_rounds = _completed_rounds(year)
    if not completed_rounds:
        return completed_rounds, []

    rounds_to_load = completed_rounds[-(window * 2):]
    snapshots = []

    for event in rounds_to_load:
        qualifying_rows = []
        race_rows = []

        try:
            qualifying_session, _ = load_session(year, event["round_number"], "Qualifying")
            qualifying_rows = _session_results_rows(qualifying_session)
        except Exception as exc:
            logger.info("Skipping driver-intel qualifying for round %s: %s", event["round_number"], exc)

        try:
            race_session, _ = load_session(year, event["round_number"], "Race")
            race_rows = _session_results_rows(race_session)
        except Exception as exc:
            logger.info("Skipping driver-intel race for round %s: %s", event["round_number"], exc)

        if not qualifying_rows and not race_rows:
            continue

        snapshots.append(
            {
                "round_number": event["round_number"],
                "event_name": event["event_name"],
                "qualifying": qualifying_rows,
                "race": race_rows,
            }
        )

    return completed_rounds, snapshots


def _aggregate_driver_entries(snapshots):
    """Build round-by-round entries for every driver in the loaded sample."""
    drivers = {}

    for snapshot in snapshots:
        qualifying_map = {row["driver"]: row for row in snapshot["qualifying"]}
        race_map = {row["driver"]: row for row in snapshot["race"]}
        all_drivers = sorted(set(qualifying_map) | set(race_map))

        for driver in all_drivers:
            quali = qualifying_map.get(driver, {})
            race = race_map.get(driver, {})
            team = race.get("team") or quali.get("team") or "Unknown"
            quali_position = quali.get("position")
            race_position = race.get("position")
            grid_position = race.get("grid_position")
            points = race.get("points") or 0.0
            gained = None
            if grid_position is not None and race_position is not None and grid_position > 0:
                gained = grid_position - race_position

            drivers.setdefault(driver, []).append(
                {
                    "round_number": snapshot["round_number"],
                    "event_name": snapshot["event_name"],
                    "team": team,
                    "quali_position": quali_position,
                    "race_position": race_position,
                    "grid_position": grid_position,
                    "points": round(points, 1),
                    "gained": gained,
                    "score": _round_score(quali_position, race_position, gained, points),
                    "status": race.get("status") or "",
                }
            )

    for driver in drivers:
        drivers[driver] = sorted(drivers[driver], key=lambda row: row["round_number"])

    return drivers


def _consistency_score(entries):
    """Return a simple consistency score based on recent results."""
    sample = []
    for row in entries:
        if row.get("quali_position") is not None:
            sample.append(float(row["quali_position"]))
        if row.get("race_position") is not None:
            sample.append(float(row["race_position"]))
    if len(sample) < 2:
        return None
    return round(pstdev(sample), 2)


def _consistency_label(score):
    if score is None:
        return "N/A"
    if score <= 1.5:
        return "VERY STEADY"
    if score <= 3.0:
        return "STEADY"
    if score <= 5.0:
        return "SWINGING"
    return "VOLATILE"


def _summarize_drivers(drivers, window):
    """Build comparable summaries for every driver in the loaded sample."""
    summaries = []

    for driver, entries in drivers.items():
        recent_entries = entries[-window:]
        recent_scores, previous_scores = _recent_previous_split([row["score"] for row in entries], window)
        recent_avg_score = _average(recent_scores, digits=2) or 0.0
        previous_avg_score = _average(previous_scores, digits=2)
        delta = None if previous_avg_score is None else round(recent_avg_score - previous_avg_score, 2)

        summaries.append(
            {
                "driver": driver,
                "team": recent_entries[-1]["team"] if recent_entries else "Unknown",
                "rounds_sampled": len(recent_entries),
                "form_index": round(min(100.0, recent_avg_score * 1.35), 1),
                "trend": _trend(delta),
                "trend_arrow": _trend_arrow(_trend(delta)),
                "trend_delta": delta,
                "avg_quali": _average([row["quali_position"] for row in recent_entries], digits=2),
                "avg_race": _average([row["race_position"] for row in recent_entries], digits=2),
                "avg_gain": _average([row["gained"] for row in recent_entries], digits=2),
                "recent_points": round(sum(row["points"] for row in recent_entries), 1),
                "wins": sum(1 for row in recent_entries if row.get("race_position") == 1),
                "podiums": sum(1 for row in recent_entries if row.get("race_position") and row["race_position"] <= 3),
                "consistency": _consistency_score(recent_entries),
                "latest_event": recent_entries[-1]["event_name"] if recent_entries else "-",
            }
        )

    return summaries


def _rank_value(summaries, metric, reverse=False):
    """Return rank lookup for a metric while ignoring empty values."""
    filtered = [row for row in summaries if row.get(metric) is not None]
    ordered = sorted(filtered, key=lambda row: row[metric], reverse=reverse)
    return {row["driver"]: index + 1 for index, row in enumerate(ordered)}, len(ordered)


def _grid_rank_rows(selected_summary, summaries):
    """Create compact rank cards for the selected driver."""
    total_drivers = len(summaries)
    rank_maps = {
        "form_index": _rank_value(summaries, "form_index", reverse=True),
        "avg_quali": _rank_value(summaries, "avg_quali", reverse=False),
        "avg_race": _rank_value(summaries, "avg_race", reverse=False),
        "avg_gain": _rank_value(summaries, "avg_gain", reverse=True),
        "recent_points": _rank_value(summaries, "recent_points", reverse=True),
        "consistency": _rank_value(summaries, "consistency", reverse=False),
    }

    def _rank_text(metric_name):
        rank_map, count = rank_maps[metric_name]
        rank = rank_map.get(selected_summary["driver"])
        total = count or total_drivers
        if rank is None or total <= 0:
            return "-"
        return f"P{rank}/{total}"

    return [
        {
            "label": "Form Rank",
            "value": _rank_text("form_index"),
            "detail": f"Form index {selected_summary['form_index']}",
        },
        {
            "label": "Qualifying Rank",
            "value": _rank_text("avg_quali"),
            "detail": f"Avg quali: P{selected_summary['avg_quali']}" if selected_summary["avg_quali"] is not None else "Avg quali not available",
        },
        {
            "label": "Race Finish Rank",
            "value": _rank_text("avg_race"),
            "detail": f"Avg finish: P{selected_summary['avg_race']}" if selected_summary["avg_race"] is not None else "Avg finish not available",
        },
        {
            "label": "Positions Gained Rank",
            "value": _rank_text("avg_gain"),
            "detail": f"Avg change: {selected_summary['avg_gain']:+}" if selected_summary["avg_gain"] is not None else "Avg change not available",
        },
        {
            "label": "Points Rank",
            "value": _rank_text("recent_points"),
            "detail": f"{selected_summary['recent_points']} points in window",
        },
        {
            "label": "Consistency Rank",
            "value": _rank_text("consistency"),
            "detail": selected_summary["consistency_label"],
        },
    ]


def _build_teammate_context(selected_driver, drivers, snapshots, window):
    """Summarize recent teammate results for the selected driver."""
    recent_rounds = {row["round_number"] for row in drivers.get(selected_driver, [])[-window:]}
    teammate_records = {}

    for snapshot in snapshots:
        if snapshot["round_number"] not in recent_rounds:
            continue

        qualifying_map = {row["driver"]: row for row in snapshot["qualifying"]}
        race_map = {row["driver"]: row for row in snapshot["race"]}
        selected_team = None

        if selected_driver in race_map:
            selected_team = race_map[selected_driver]["team"]
        elif selected_driver in qualifying_map:
            selected_team = qualifying_map[selected_driver]["team"]

        if not selected_team:
            continue

        teammate_candidates = {
            driver for driver, row in race_map.items() if row["team"] == selected_team and driver != selected_driver
        } | {
            driver for driver, row in qualifying_map.items() if row["team"] == selected_team and driver != selected_driver
        }

        if not teammate_candidates:
            continue

        teammate = sorted(teammate_candidates)[0]
        record = teammate_records.setdefault(
            teammate,
            {
                "team": selected_team,
                "rounds": 0,
                "qualifying_wins": 0,
                "qualifying_losses": 0,
                "race_wins": 0,
                "race_losses": 0,
                "selected_points": 0.0,
                "teammate_points": 0.0,
            },
        )
        record["rounds"] += 1

        selected_quali = qualifying_map.get(selected_driver, {}).get("position")
        teammate_quali = qualifying_map.get(teammate, {}).get("position")
        if selected_quali is not None and teammate_quali is not None:
            if selected_quali < teammate_quali:
                record["qualifying_wins"] += 1
            elif selected_quali > teammate_quali:
                record["qualifying_losses"] += 1

        selected_race = race_map.get(selected_driver, {}).get("position")
        teammate_race = race_map.get(teammate, {}).get("position")
        if selected_race is not None and teammate_race is not None:
            if selected_race < teammate_race:
                record["race_wins"] += 1
            elif selected_race > teammate_race:
                record["race_losses"] += 1

        record["selected_points"] += race_map.get(selected_driver, {}).get("points", 0.0) or 0.0
        record["teammate_points"] += race_map.get(teammate, {}).get("points", 0.0) or 0.0

    if not teammate_records:
        return {}

    teammate, record = max(teammate_records.items(), key=lambda item: item[1]["rounds"])
    record["teammate"] = teammate
    record["selected_points"] = round(record["selected_points"], 1)
    record["teammate_points"] = round(record["teammate_points"], 1)
    return record


def _insight_lists(selected_summary, teammate_context):
    """Generate short plain-language strengths and watchouts."""
    strengths = []
    watchouts = []

    if selected_summary["trend"] in ("SURGING", "RISING"):
        strengths.append(
            {
                "title": "Recent momentum",
                "detail": f"{selected_summary['driver']} is {selected_summary['trend'].lower()} over the selected window.",
            }
        )
    elif selected_summary["trend"] in ("COOLING", "SLIDING"):
        watchouts.append(
            {
                "title": "Momentum dip",
                "detail": f"Recent results are {selected_summary['trend'].lower()}, so the trend line is heading the wrong way.",
            }
        )

    if selected_summary["avg_quali"] is not None and selected_summary["avg_quali"] <= 6:
        strengths.append(
            {
                "title": "Saturday strength",
                "detail": f"Average qualifying position is P{selected_summary['avg_quali']}, which points to strong one-lap pace.",
            }
        )

    if selected_summary["avg_gain"] is not None and selected_summary["avg_gain"] >= 1:
        strengths.append(
            {
                "title": "Sunday mover",
                "detail": f"Average position change is {selected_summary['avg_gain']:+}, so this driver usually moves forward in races.",
            }
        )
    elif selected_summary["avg_gain"] is not None and selected_summary["avg_gain"] <= -1:
        watchouts.append(
            {
                "title": "Losing ground on Sundays",
                "detail": f"Average position change is {selected_summary['avg_gain']:+}, so race execution has been costing places recently.",
            }
        )

    if (
        selected_summary["avg_quali"] is not None
        and selected_summary["avg_race"] is not None
        and selected_summary["avg_race"] <= selected_summary["avg_quali"] - 1
    ):
        strengths.append(
            {
                "title": "Race conversion",
                "detail": "Average race result is better than average qualifying, which suggests strong race-day execution.",
            }
        )
    elif (
        selected_summary["avg_quali"] is not None
        and selected_summary["avg_race"] is not None
        and selected_summary["avg_race"] >= selected_summary["avg_quali"] + 2
    ):
        watchouts.append(
            {
                "title": "Race-day drop-off",
                "detail": "Average race result is notably worse than average qualifying, so converting starting position has been difficult.",
            }
        )

    if selected_summary["consistency"] is not None and selected_summary["consistency"] <= 3:
        strengths.append(
            {
                "title": "Steady output",
                "detail": f"Recent weekend results rate as {selected_summary['consistency_label'].lower()}, which is valuable over a run of rounds.",
            }
        )
    elif selected_summary["consistency"] is not None and selected_summary["consistency"] > 5:
        watchouts.append(
            {
                "title": "Wide swings",
                "detail": f"Results rate as {selected_summary['consistency_label'].lower()}, so the performance ceiling and floor have been far apart.",
            }
        )

    if teammate_context:
        if teammate_context["qualifying_wins"] > teammate_context["qualifying_losses"]:
            strengths.append(
                {
                    "title": "Teammate edge in qualifying",
                    "detail": f"Leading {teammate_context['teammate']} {teammate_context['qualifying_wins']}-{teammate_context['qualifying_losses']} on recent Saturdays.",
                }
            )
        elif teammate_context["qualifying_losses"] > teammate_context["qualifying_wins"]:
            watchouts.append(
                {
                    "title": "Teammate pressure in qualifying",
                    "detail": f"Trailing {teammate_context['teammate']} {teammate_context['qualifying_losses']}-{teammate_context['qualifying_wins']} on recent Saturdays.",
                }
            )

        if teammate_context["race_wins"] > teammate_context["race_losses"]:
            strengths.append(
                {
                    "title": "Teammate edge on Sundays",
                    "detail": f"Leading {teammate_context['teammate']} {teammate_context['race_wins']}-{teammate_context['race_losses']} in recent races.",
                }
            )
        elif teammate_context["race_losses"] > teammate_context["race_wins"]:
            watchouts.append(
                {
                    "title": "Teammate pressure on Sundays",
                    "detail": f"Trailing {teammate_context['teammate']} {teammate_context['race_losses']}-{teammate_context['race_wins']} in recent races.",
                }
            )

    return strengths[:4], watchouts[:4]


def build_driver_intelligence(year, driver=None, window=5):
    """Build a driver-focused season view for one selected driver."""
    year = int(year)
    window = max(3, min(int(window or 5), 8))
    cache_key = (year, driver or "", window)
    if cache_key in _driver_cache:
        return _driver_cache[cache_key]

    completed_rounds, snapshots = _build_round_snapshots(year, window)
    payload = empty_driver_intelligence()
    payload["meta"].update(
        {
            "year": year,
            "window": window,
            "window_label": f"Last {window} rounds",
            "completed_rounds": len(completed_rounds),
            "latest_event": snapshots[-1]["event_name"] if snapshots else (completed_rounds[-1]["event_name"] if completed_rounds else "-"),
        }
    )

    if not snapshots:
        _driver_cache[cache_key] = payload
        return payload

    drivers = _aggregate_driver_entries(snapshots)
    summaries = _summarize_drivers(drivers, window)
    summaries.sort(
        key=lambda row: (
            -row["form_index"],
            row["avg_race"] if row["avg_race"] is not None else 99,
            row["avg_quali"] if row["avg_quali"] is not None else 99,
        )
    )

    available_drivers = sorted(drivers)
    payload["meta"]["available_drivers"] = available_drivers

    selected_driver = driver if driver in drivers else (summaries[0]["driver"] if summaries else (available_drivers[0] if available_drivers else "-"))
    selected_summary = next((row for row in summaries if row["driver"] == selected_driver), None)
    if selected_summary is None:
        _driver_cache[cache_key] = payload
        return payload

    selected_summary["consistency_label"] = _consistency_label(selected_summary["consistency"])
    form_rank = next((index + 1 for index, row in enumerate(summaries) if row["driver"] == selected_driver), None)
    teammate_context = _build_teammate_context(selected_driver, drivers, snapshots, window)
    strengths, watchouts = _insight_lists(selected_summary, teammate_context)
    recent_results = drivers[selected_driver][-window:]

    payload["meta"]["driver"] = selected_driver
    payload["summary"] = {
        "form_rank": f"P{form_rank}/{len(summaries)}" if form_rank is not None else "-",
        "trend": f"{selected_summary['trend_arrow']} {selected_summary['trend']}",
        "avg_quali": f"P{selected_summary['avg_quali']}" if selected_summary["avg_quali"] is not None else "-",
        "avg_race": f"P{selected_summary['avg_race']}" if selected_summary["avg_race"] is not None else "-",
        "avg_gain": f"{selected_summary['avg_gain']:+}" if selected_summary["avg_gain"] is not None else "-",
        "recent_points": selected_summary["recent_points"],
    }
    payload["profile"] = {
        "driver": selected_driver,
        "team": selected_summary["team"],
        "trend": selected_summary["trend"],
        "trend_arrow": selected_summary["trend_arrow"],
        "form_index": selected_summary["form_index"],
        "wins": selected_summary["wins"],
        "podiums": selected_summary["podiums"],
        "consistency_score": selected_summary["consistency"],
        "consistency_label": selected_summary["consistency_label"],
        "rounds_sampled": selected_summary["rounds_sampled"],
    }
    payload["grid_ranks"] = _grid_rank_rows(selected_summary, summaries)
    payload["teammate_context"] = teammate_context
    payload["recent_results"] = recent_results
    payload["strengths"] = strengths
    payload["watchouts"] = watchouts

    _driver_cache[cache_key] = payload
    return payload
