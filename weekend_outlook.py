"""
weekend_outlook.py - Weekend briefing and outlook
=================================================

Builds a pre-weekend page that ties together the app's broader context:
- recent season form
- circuit demands
- teammate pressure points
- likely teams and drivers to watch
"""

from season_form import build_season_form
from circuit_intel import build_circuit_intelligence


_outlook_cache = {}


def empty_weekend_outlook():
    """Return a stable empty payload for the weekend outlook template."""
    return {
        "meta": {
            "year": None,
            "round_number": None,
            "window": 5,
            "window_label": "Last 5 rounds",
            "event_name": "-",
            "circuit_name": "-",
            "location": "-",
            "country": "-",
        },
        "summary": {
            "favorite_driver": "-",
            "team_to_beat": "-",
            "passing_outlook": "-",
            "tyre_pressure": "-",
            "strategy_shape": "-",
        },
        "storylines": [],
        "drivers_to_watch": [],
        "teams_to_watch": [],
        "midfield_picture": [],
        "teammate_focus": {},
        "supporting_data": {
            "season": None,
            "circuit": None,
        },
    }


def _parse_score(score):
    try:
        left, right = str(score).split("-", 1)
        return int(left), int(right)
    except Exception:
        return 0, 0


def _driver_watch_reason(row, season_summary):
    reasons = []
    if row.get("rank") == 1:
        reasons.append("best recent form on the grid")
    if row.get("trend") in ("SURGING", "RISING"):
        reasons.append(f"{row['trend'].lower()} trend")
    if row.get("recent_quali_avg") is not None and row["recent_quali_avg"] <= 4:
        reasons.append("strong recent qualifying pace")
    if row.get("avg_positions_gained") is not None and row["avg_positions_gained"] >= 1:
        reasons.append("usually moves forward on Sundays")
    if row.get("driver_display") == season_summary.get("positions_gained_leader"):
        reasons.append("best recent average position gain")
    return reasons[0] if reasons else "steady recent results"


def _team_watch_reason(row):
    reasons = []
    if row.get("rank") == 1:
        reasons.append("strongest recent team form")
    if row.get("trend") in ("SURGING", "RISING"):
        reasons.append(f"{row['trend'].lower()} trend")
    if row.get("recent_quali_avg") is not None and row["recent_quali_avg"] <= 5:
        reasons.append("usually starts near the front")
    if row.get("recent_points", 0) >= 25:
        reasons.append("banking strong recent points")
    return reasons[0] if reasons else "solid recent baseline"


def _build_storylines(season_data, circuit_data):
    """Create the main plain-language weekend briefing items."""
    storylines = []

    profile = circuit_data.get("profile", {})
    recent_patterns = circuit_data.get("recent_patterns", [])
    driver_form = season_data.get("driver_form", [])
    team_form = season_data.get("team_form", [])
    teammate_battles = season_data.get("teammate_battles", [])
    season_summary = season_data.get("summary", {})

    if profile:
        storylines.append(
            {
                "title": "Track demand",
                "detail": profile.get("track_style", "Mixed circuit demands"),
            }
        )

    if recent_patterns:
        pattern = recent_patterns[1] if len(recent_patterns) > 1 else recent_patterns[0]
        storylines.append(
            {
                "title": pattern.get("title", "Strategy outlook"),
                "detail": pattern.get("detail", ""),
            }
        )

    if driver_form:
        leader = driver_form[0]
        storylines.append(
            {
                "title": "Driver to beat",
                "detail": f"{leader['driver_display']} arrives with the strongest recent form for {leader['team']}.",
            }
        )

    if team_form:
        leader = team_form[0]
        storylines.append(
            {
                "title": "Team benchmark",
                "detail": f"{leader['team']} sets the recent team pace after scoring {leader['recent_points']} points over the selected window.",
            }
        )

    if teammate_battles:
        closest = min(
            teammate_battles,
            key=lambda row: abs(_parse_score(row.get("qualifying_score"))[0] - _parse_score(row.get("qualifying_score"))[1])
            + abs(_parse_score(row.get("race_score"))[0] - _parse_score(row.get("race_score"))[1]),
        )
        storylines.append(
            {
                "title": "Garage pressure point",
                "detail": f"{closest['driver_a']} vs {closest['driver_b']} is one of the closest recent teammate fights on the grid.",
            }
        )

    mover = next(
        (row for row in driver_form if row.get("driver_display") == season_summary.get("positions_gained_leader")),
        None,
    )
    if mover and mover.get("avg_positions_gained") is not None:
        storylines.append(
            {
                "title": "Sunday mover",
                "detail": f"{mover['driver_display']} has the best recent average position gain at {mover['avg_positions_gained']:+} places.",
            }
        )

    return storylines[:5]


def _build_drivers_to_watch(season_data):
    """Select the main drivers to watch this weekend."""
    driver_rows = season_data.get("driver_form", [])
    season_summary = season_data.get("summary", {})
    selected = []
    seen = set()

    for row in driver_rows:
        if len(selected) >= 5:
            break
        key = row["driver_display"]
        if key in seen:
            continue
        selected.append(
            {
                "driver": row["driver_display"],
                "team": row["team"],
                "trend": row["trend"].title(),
                "form_index": row["form_index"],
                "avg_quali": row["recent_quali_avg"],
                "avg_race": row["recent_race_avg"],
                "avg_gain": row["avg_positions_gained"],
                "reason": _driver_watch_reason(row, season_summary),
            }
        )
        seen.add(key)

    return selected


def _build_teams_to_watch(season_data):
    """Select the main teams to watch this weekend."""
    team_rows = season_data.get("team_form", [])
    selected = []
    for row in team_rows[:5]:
        selected.append(
            {
                "team": row["team"],
                "trend": row["trend"].title(),
                "form_index": row["form_index"],
                "avg_quali": row["recent_quali_avg"],
                "avg_race": row["recent_race_avg"],
                "recent_points": row["recent_points"],
                "reason": _team_watch_reason(row),
            }
        )
    return selected


def _build_midfield_picture(season_data):
    """Describe the midfield teams in the current recent window."""
    teams = season_data.get("team_form", [])
    midfield = teams[3:7]
    if not midfield:
        return []

    return [
        {
            "team": row["team"],
            "form_index": row["form_index"],
            "trend": row["trend"].title(),
            "recent_points": row["recent_points"],
            "avg_race": row["recent_race_avg"],
        }
        for row in midfield
    ]


def _build_teammate_focus(season_data):
    """Pick one teammate battle that looks most interesting for the weekend."""
    teammate_battles = season_data.get("teammate_battles", [])
    if not teammate_battles:
        return {}

    def _battle_score(row):
        q_left, q_right = _parse_score(row.get("qualifying_score"))
        r_left, r_right = _parse_score(row.get("race_score"))
        closeness = abs(q_left - q_right) + abs(r_left - r_right)
        return (closeness, -row.get("rounds_sampled", 0))

    focus = min(teammate_battles, key=_battle_score)
    return {
        "team": focus["team"],
        "driver_a": focus["driver_a"],
        "driver_b": focus["driver_b"],
        "qualifying_score": focus["qualifying_score"],
        "race_score": focus["race_score"],
        "qualifying_leader": focus["qualifying_leader"],
        "race_leader": focus["race_leader"],
        "rounds_sampled": focus["rounds_sampled"],
    }


def build_weekend_outlook(year, round_number, window=5):
    """Build the full weekend outlook payload."""
    year = int(year)
    round_number = int(round_number)
    window = max(3, min(int(window or 5), 8))
    cache_key = (year, round_number, window)
    if cache_key in _outlook_cache:
        return _outlook_cache[cache_key]

    season_data = build_season_form(year, window=window)
    circuit_data = build_circuit_intelligence(year, round_number)

    payload = empty_weekend_outlook()
    payload["meta"].update(
        {
            "year": year,
            "round_number": round_number,
            "window": window,
            "window_label": f"Last {window} rounds",
            "event_name": circuit_data.get("meta", {}).get("event_name", "-"),
            "circuit_name": circuit_data.get("meta", {}).get("circuit_name", "-"),
            "location": circuit_data.get("meta", {}).get("location", "-"),
            "country": circuit_data.get("meta", {}).get("country", "-"),
        }
    )

    payload["summary"] = {
        "favorite_driver": season_data.get("summary", {}).get("hottest_driver", "-"),
        "team_to_beat": season_data.get("summary", {}).get("hottest_team", "-"),
        "passing_outlook": circuit_data.get("summary", {}).get("overtaking", "-"),
        "tyre_pressure": circuit_data.get("summary", {}).get("tyre_stress", "-"),
        "strategy_shape": circuit_data.get("summary", {}).get("strategy_bias", "-"),
    }
    payload["storylines"] = _build_storylines(season_data, circuit_data)
    payload["drivers_to_watch"] = _build_drivers_to_watch(season_data)
    payload["teams_to_watch"] = _build_teams_to_watch(season_data)
    payload["midfield_picture"] = _build_midfield_picture(season_data)
    payload["teammate_focus"] = _build_teammate_focus(season_data)
    payload["supporting_data"] = {
        "season": season_data,
        "circuit": circuit_data,
    }

    _outlook_cache[cache_key] = payload
    return payload
