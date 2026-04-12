"""
season_form.py - Season-level form and momentum analysis
=======================================================

Adds a missing season dimension to the dashboard by aggregating official
qualifying and race results across completed rounds. The existing app is
very strong at session intelligence; this module focuses on multi-weekend
form so users can answer "who is trending up right now?" without leaving
the project.
"""

import datetime
import logging
from statistics import mean

import fastf1
import pandas as pd

from data_handler import load_session


logger = logging.getLogger(__name__)

_season_cache = {}


def empty_season_form():
    """Return a stable empty payload for template rendering."""
    return {
        "meta": {
            "year": None,
            "completed_rounds": 0,
            "window": 5,
            "window_label": "Last 5 rounds",
            "latest_event": "-",
            "latest_round": None,
            "rounds_used": [],
        },
        "summary": {
            "hottest_driver": "-",
            "hottest_team": "-",
            "qualifying_benchmark": "-",
            "positions_gained_leader": "-",
            "positions_lost_leader": "-",
        },
        "driver_form": [],
        "team_form": [],
        "teammate_battles": [],
    }


def _safe_int(value):
    """Convert a value to int when possible."""
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value):
    """Convert a value to float when possible."""
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trend(delta):
    """Classify momentum from recent-vs-previous form delta."""
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
    """Human-readable arrow for templates."""
    return {
        "SURGING": "UP",
        "RISING": "UP",
        "STABLE": "FLAT",
        "COOLING": "DOWN",
        "SLIDING": "DOWN",
        "NEW": "NEW",
    }.get(trend, "FLAT")


def _battle_leader(score_a, score_b, driver_a, driver_b):
    """Return a readable leader label for teammate battles."""
    if score_a > score_b:
        return driver_a
    if score_b > score_a:
        return driver_b
    return "Tied"


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
        qualifying_ts = None
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
            elif str(session_name) == "Qualifying":
                qualifying_ts = timestamp

        if race_ts is not None and race_ts <= now:
            rounds.append(
                {
                    "round_number": round_number,
                    "event_name": str(event.get("EventName", f"Round {round_number}")),
                    "race_ts": race_ts,
                    "qualifying_ts": qualifying_ts,
                }
            )

    return rounds


def _session_results_rows(session):
    """Normalize FastF1 session results to a list of dicts."""
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
        if not driver or position is None:
            continue

        normalized.append(
            {
                "driver": str(driver),
                "driver_display": str(driver),
                "team": str(team),
                "position": position,
                "grid_position": _safe_int(row.get("GridPosition")),
                "points": _safe_float(row.get("Points")),
                "status": str(row.get("Status")) if pd.notna(row.get("Status")) else "",
            }
        )

    return sorted(normalized, key=lambda row: row["position"])


def _round_score(quali_position=None, race_position=None, gained=None, points=None):
    """
    Blend weekend outcomes into one coarse form score.

    The score intentionally favors race result and points, then uses
    qualifying and positions gained as supporting signals.
    """
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


def _recent_previous_split(values, window):
    """Return recent and previous chunks from a time-ordered list."""
    if not values:
        return [], []

    recent = values[-window:]
    previous = values[-(window * 2):-window] if len(values) > window else []
    return recent, previous


def _average(values, digits=2):
    """Return rounded mean for non-empty numeric lists."""
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return round(mean(cleaned), digits)


def _driver_form_rows(round_snapshots, window):
    """Aggregate driver-level momentum across completed rounds."""
    drivers = {}

    for snapshot in round_snapshots:
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

            entry = {
                "round_number": snapshot["round_number"],
                "event_name": snapshot["event_name"],
                "team": team,
                "quali_position": quali_position,
                "race_position": race_position,
                "grid_position": grid_position,
                "points": points,
                "gained": gained,
                "score": _round_score(
                    quali_position=quali_position,
                    race_position=race_position,
                    gained=gained,
                    points=points,
                ),
            }
            drivers.setdefault(driver, []).append(entry)

    rows = []
    for driver, entries in drivers.items():
        entries = sorted(entries, key=lambda item: item["round_number"])
        recent_entries = entries[-window:]
        recent_scores, previous_scores = _recent_previous_split([item["score"] for item in entries], window)
        recent_quali = [item["quali_position"] for item in recent_entries]
        recent_race = [item["race_position"] for item in recent_entries]
        recent_gained = [item["gained"] for item in recent_entries]
        recent_points = [item["points"] for item in recent_entries]

        recent_avg_score = _average(recent_scores, digits=2) or 0.0
        previous_avg_score = _average(previous_scores, digits=2)
        delta = None if previous_avg_score is None else round(recent_avg_score - previous_avg_score, 2)
        trend = _trend(delta)
        form_index = round(min(100.0, recent_avg_score * 1.35), 1)

        wins = sum(1 for item in recent_entries if item["race_position"] == 1)
        podiums = sum(1 for item in recent_entries if item["race_position"] and item["race_position"] <= 3)
        points_total = round(sum(recent_points), 1)
        latest = recent_entries[-1]

        rows.append(
            {
                "driver": driver,
                "driver_display": driver,
                "team": latest["team"],
                "rounds_count": len(entries),
                "form_index": form_index,
                "trend": trend,
                "trend_arrow": _trend_arrow(trend),
                "trend_delta": delta,
                "recent_quali_avg": _average(recent_quali, digits=2),
                "recent_race_avg": _average(recent_race, digits=2),
                "recent_points": points_total,
                "avg_positions_gained": _average(recent_gained, digits=2),
                "wins": wins,
                "podiums": podiums,
                "latest_event": latest["event_name"],
                "latest_race_result": latest["race_position"],
            }
        )

    rows.sort(
        key=lambda row: (
            -row["form_index"],
            row["recent_race_avg"] if row["recent_race_avg"] is not None else 99,
            row["recent_quali_avg"] if row["recent_quali_avg"] is not None else 99,
        )
    )

    for position, row in enumerate(rows, start=1):
        row["rank"] = position

    return rows


def _team_form_rows(round_snapshots, window):
    """Aggregate team-level form across completed rounds."""
    team_rounds = {}

    for snapshot in round_snapshots:
        by_team = {}

        for row in snapshot["qualifying"]:
            by_team.setdefault(row["team"], {}).setdefault("qualifying_positions", []).append(row["position"])

        for row in snapshot["race"]:
            team_bucket = by_team.setdefault(row["team"], {})
            team_bucket.setdefault("race_positions", []).append(row["position"])
            team_bucket.setdefault("points", []).append(row.get("points") or 0.0)
            grid = row.get("grid_position")
            if grid is not None and grid > 0:
                team_bucket.setdefault("gained", []).append(grid - row["position"])

        for team, bucket in by_team.items():
            quali_avg = _average(bucket.get("qualifying_positions", []), digits=2)
            race_avg = _average(bucket.get("race_positions", []), digits=2)
            points_total = round(sum(bucket.get("points", [])), 1)
            gained_avg = _average(bucket.get("gained", []), digits=2)
            score = _round_score(
                quali_position=quali_avg,
                race_position=race_avg,
                gained=gained_avg,
                points=points_total,
            )
            team_rounds.setdefault(team, []).append(
                {
                    "round_number": snapshot["round_number"],
                    "event_name": snapshot["event_name"],
                    "quali_avg": quali_avg,
                    "race_avg": race_avg,
                    "points": points_total,
                    "gained_avg": gained_avg,
                    "score": score,
                }
            )

    rows = []
    for team, entries in team_rounds.items():
        entries = sorted(entries, key=lambda item: item["round_number"])
        recent_entries = entries[-window:]
        recent_scores, previous_scores = _recent_previous_split([item["score"] for item in entries], window)
        recent_avg_score = _average(recent_scores, digits=2) or 0.0
        previous_avg_score = _average(previous_scores, digits=2)
        delta = None if previous_avg_score is None else round(recent_avg_score - previous_avg_score, 2)
        trend = _trend(delta)

        rows.append(
            {
                "team": team,
                "form_index": round(min(100.0, recent_avg_score * 1.1), 1),
                "trend": trend,
                "trend_arrow": _trend_arrow(trend),
                "trend_delta": delta,
                "recent_quali_avg": _average([item["quali_avg"] for item in recent_entries], digits=2),
                "recent_race_avg": _average([item["race_avg"] for item in recent_entries], digits=2),
                "recent_points": round(sum(item["points"] for item in recent_entries), 1),
                "avg_positions_gained": _average([item["gained_avg"] for item in recent_entries], digits=2),
                "best_recent_finish": min(
                    [item["race_avg"] for item in recent_entries if item["race_avg"] is not None],
                    default=None,
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            -row["form_index"],
            row["recent_race_avg"] if row["recent_race_avg"] is not None else 99,
            -row["recent_points"],
        )
    )

    for position, row in enumerate(rows, start=1):
        row["rank"] = position

    return rows


def _teammate_battles(round_snapshots, window):
    """Build season-level teammate head-to-head summaries."""
    round_snapshots = round_snapshots[-window:]
    battles = {}

    for snapshot in round_snapshots:
        quali_by_team = {}
        race_by_team = {}

        for row in snapshot["qualifying"]:
            quali_by_team.setdefault(row["team"], []).append(row)
        for row in snapshot["race"]:
            race_by_team.setdefault(row["team"], []).append(row)

        for team in sorted(set(quali_by_team) | set(race_by_team)):
            quali_rows = sorted(quali_by_team.get(team, []), key=lambda item: item["position"])
            race_rows = sorted(race_by_team.get(team, []), key=lambda item: item["position"])

            if len(quali_rows) < 2 and len(race_rows) < 2:
                continue

            quali_pair = tuple(sorted({row["driver"] for row in quali_rows[:2]} or {row["driver"] for row in race_rows[:2]}))
            if len(quali_pair) != 2:
                continue

            record = battles.setdefault(
                (team, quali_pair[0], quali_pair[1]),
                {
                    "team": team,
                    "driver_a": quali_pair[0],
                    "driver_b": quali_pair[1],
                    "qualifying_a": 0,
                    "qualifying_b": 0,
                    "race_a": 0,
                    "race_b": 0,
                    "rounds": [],
                },
            )

            if len(quali_rows) >= 2:
                winner = quali_rows[0]["driver"]
                if winner == record["driver_a"]:
                    record["qualifying_a"] += 1
                elif winner == record["driver_b"]:
                    record["qualifying_b"] += 1

            if len(race_rows) >= 2:
                winner = race_rows[0]["driver"]
                if winner == record["driver_a"]:
                    record["race_a"] += 1
                elif winner == record["driver_b"]:
                    record["race_b"] += 1

            record["rounds"].append(snapshot["round_number"])

    rows = []
    for _, battle in battles.items():
        total_duels = battle["qualifying_a"] + battle["qualifying_b"] + battle["race_a"] + battle["race_b"]
        if total_duels == 0:
            continue

        qual_winner = _battle_leader(
            battle["qualifying_a"], battle["qualifying_b"], battle["driver_a"], battle["driver_b"]
        )
        race_winner = _battle_leader(
            battle["race_a"], battle["race_b"], battle["driver_a"], battle["driver_b"]
        )
        rows.append(
            {
                "team": battle["team"],
                "driver_a": battle["driver_a"],
                "driver_b": battle["driver_b"],
                "qualifying_score": f"{battle['qualifying_a']}-{battle['qualifying_b']}",
                "race_score": f"{battle['race_a']}-{battle['race_b']}",
                "qualifying_leader": qual_winner,
                "race_leader": race_winner,
                "rounds_sampled": len(set(battle["rounds"])),
            }
        )

    rows.sort(key=lambda row: row["team"])
    return rows


def build_season_form(year, window=5):
    """Build season-level form tables for a given year."""
    window = max(3, min(int(window or 5), 8))
    completed_rounds = _completed_rounds(year)
    if not completed_rounds:
        payload = empty_season_form()
        payload["meta"]["year"] = year
        payload["meta"]["window"] = window
        payload["meta"]["window_label"] = f"Last {window} rounds"
        return payload

    latest_round = completed_rounds[-1]["round_number"]
    cache_key = (year, window, latest_round)
    if cache_key in _season_cache:
        return _season_cache[cache_key]

    rounds_to_load = completed_rounds[-(window * 2):]
    snapshots = []
    for event in rounds_to_load:
        qualifying_rows = []
        race_rows = []

        try:
            qualifying_session, _ = load_session(year, event["round_number"], "Qualifying")
            qualifying_rows = _session_results_rows(qualifying_session)
        except Exception as exc:
            logger.info("Skipping qualifying results for round %s: %s", event["round_number"], exc)

        try:
            race_session, _ = load_session(year, event["round_number"], "Race")
            race_rows = _session_results_rows(race_session)
        except Exception as exc:
            logger.info("Skipping race results for round %s: %s", event["round_number"], exc)

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

    if not snapshots:
        payload = empty_season_form()
        payload["meta"].update(
            {
                "year": year,
                "completed_rounds": len(completed_rounds),
                "window": window,
                "window_label": f"Last {window} rounds",
                "latest_event": completed_rounds[-1]["event_name"],
                "latest_round": completed_rounds[-1]["round_number"],
            }
        )
        _season_cache[cache_key] = payload
        return payload

    driver_form = _driver_form_rows(snapshots, window)
    team_form = _team_form_rows(snapshots, window)
    teammate_battles = _teammate_battles(snapshots, window)

    gain_rows = [
        row for row in driver_form
        if row.get("avg_positions_gained") is not None
    ]
    gain_rows.sort(key=lambda row: row["avg_positions_gained"], reverse=True)
    positions_gained_leader = gain_rows[0]["driver_display"] if gain_rows else "-"
    loss_rows = [row for row in gain_rows if row["avg_positions_gained"] < 0]
    loss_rows.sort(key=lambda row: row["avg_positions_gained"])
    positions_lost_leader = loss_rows[0]["driver_display"] if loss_rows else "-"

    payload = {
        "meta": {
            "year": year,
            "completed_rounds": len(completed_rounds),
            "window": window,
            "window_label": f"Last {window} rounds",
            "latest_event": snapshots[-1]["event_name"],
            "latest_round": snapshots[-1]["round_number"],
            "rounds_used": [f"R{row['round_number']} {row['event_name']}" for row in snapshots[-window:]],
        },
        "summary": {
            "hottest_driver": driver_form[0]["driver_display"] if driver_form else "-",
            "hottest_team": team_form[0]["team"] if team_form else "-",
            "qualifying_benchmark": next(
                (
                    row["driver_display"]
                    for row in sorted(
                        driver_form,
                        key=lambda item: item["recent_quali_avg"] if item["recent_quali_avg"] is not None else 99,
                    )
                    if row["recent_quali_avg"] is not None
                ),
                "-",
            ),
            "positions_gained_leader": positions_gained_leader,
            "positions_lost_leader": positions_lost_leader,
        },
        "driver_form": driver_form,
        "team_form": team_form,
        "teammate_battles": teammate_battles,
    }

    _season_cache[cache_key] = payload
    return payload
