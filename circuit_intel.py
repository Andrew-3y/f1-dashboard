"""
circuit_intel.py - Track and circuit intelligence
=================================================

Builds a dedicated circuit page that answers the pre-weekend question:
"What kind of race track is this, and what usually matters here?"

The page mixes curated track characteristics with a lightweight recent
history pull from official FastF1 race and qualifying results.
"""

import logging
import re
import unicodedata

import fastf1
import pandas as pd

from data_handler import load_session


logger = logging.getLogger(__name__)

_circuit_cache = {}


_CIRCUIT_PROFILES = [
    {
        "keywords": ["australian"],
        "circuit_name": "Albert Park",
        "lap_length_km": 5.278,
        "corners": 14,
        "drs_zones": 4,
        "overtaking": "MEDIUM",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Flexible 1-stop / 2-stop",
        "track_style": "Fast semi-street circuit with rhythm changes",
        "watch_for": "Track evolution, confidence through medium-speed changes, and undercut timing.",
    },
    {
        "keywords": ["chinese"],
        "circuit_name": "Shanghai International Circuit",
        "lap_length_km": 5.451,
        "corners": 16,
        "drs_zones": 2,
        "overtaking": "HIGH",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "2-stop live",
        "track_style": "Long-radius corners and one of the longest straights on the calendar",
        "watch_for": "Front-left management and strong slipstream passes into Turn 14.",
    },
    {
        "keywords": ["japanese"],
        "circuit_name": "Suzuka",
        "lap_length_km": 5.807,
        "corners": 18,
        "drs_zones": 1,
        "overtaking": "LOW",
        "tyre_stress": "HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "VERY HIGH",
        "strategy_bias": "Track position first",
        "track_style": "High-speed, flowing classic with long lateral load phases",
        "watch_for": "Qualifying confidence, front-end precision, and tyre wear through the Esses.",
    },
    {
        "keywords": ["bahrain"],
        "circuit_name": "Bahrain International Circuit",
        "lap_length_km": 5.412,
        "corners": 15,
        "drs_zones": 3,
        "overtaking": "HIGH",
        "tyre_stress": "HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "2-stop favored",
        "track_style": "Big braking zones and heavy rear traction demands",
        "watch_for": "Rear degradation, undercut strength, and braking stability into Turns 1 and 4.",
    },
    {
        "keywords": ["saudi", "jeddah"],
        "circuit_name": "Jeddah Corniche Circuit",
        "lap_length_km": 6.174,
        "corners": 27,
        "drs_zones": 3,
        "overtaking": "MEDIUM",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "VERY HIGH",
        "strategy_bias": "Track position first",
        "track_style": "Ultra-fast street circuit with huge commitment through blind sweeps",
        "watch_for": "Confidence on entry, wall proximity, and restarts after neutralizations.",
    },
    {
        "keywords": ["miami"],
        "circuit_name": "Miami International Autodrome",
        "lap_length_km": 5.412,
        "corners": 19,
        "drs_zones": 3,
        "overtaking": "MEDIUM",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Flexible",
        "track_style": "Stop-start sections mixed with one long straight and technical middle sector",
        "watch_for": "Traction out of slow corners and tyre warm-up around safety-car phases.",
    },
    {
        "keywords": ["emilia", "imola"],
        "circuit_name": "Imola",
        "lap_length_km": 4.909,
        "corners": 19,
        "drs_zones": 1,
        "overtaking": "LOW",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "LOW",
        "qualifying_importance": "VERY HIGH",
        "strategy_bias": "1-stop / track position",
        "track_style": "Narrow old-school circuit with kerb usage and precision changes of direction",
        "watch_for": "Clean qualifying laps, pit timing, and traffic management.",
    },
    {
        "keywords": ["monaco"],
        "circuit_name": "Circuit de Monaco",
        "lap_length_km": 3.337,
        "corners": 19,
        "drs_zones": 1,
        "overtaking": "VERY LOW",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "EXTREME",
        "strategy_bias": "Track position above all",
        "track_style": "Tightest street circuit on the calendar with almost no passing opportunities",
        "watch_for": "Saturday pace, traffic windows, and safety-car timing.",
    },
    {
        "keywords": ["spanish", "spain", "catalunya"],
        "circuit_name": "Circuit de Barcelona-Catalunya",
        "lap_length_km": 4.657,
        "corners": 14,
        "drs_zones": 2,
        "overtaking": "MEDIUM",
        "tyre_stress": "HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Tyre management heavy",
        "track_style": "Reference circuit with long corners that expose balance and tyre life",
        "watch_for": "Front-left wear, aero balance, and late-stint drop-off.",
    },
    {
        "keywords": ["canadian", "canada", "montreal"],
        "circuit_name": "Circuit Gilles Villeneuve",
        "lap_length_km": 4.361,
        "corners": 14,
        "drs_zones": 3,
        "overtaking": "HIGH",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Opportunistic safety-car strategy",
        "track_style": "Heavy braking and traction circuit built around chicanes and wall risk",
        "watch_for": "Brake confidence, kerb aggression, and safety-car swings.",
    },
    {
        "keywords": ["austrian", "austria", "red bull ring"],
        "circuit_name": "Red Bull Ring",
        "lap_length_km": 4.318,
        "corners": 10,
        "drs_zones": 3,
        "overtaking": "HIGH",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Aggressive overtaking and short-lap strategy swings",
        "track_style": "Short lap with three major passing zones and hill-climb braking zones",
        "watch_for": "Fine margins in qualifying and strong race repositioning.",
    },
    {
        "keywords": ["british", "great britain", "silverstone"],
        "circuit_name": "Silverstone",
        "lap_length_km": 5.891,
        "corners": 18,
        "drs_zones": 2,
        "overtaking": "MEDIUM",
        "tyre_stress": "VERY HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Tyre-limited",
        "track_style": "Very high-speed sequence track with huge aero and lateral-load demands",
        "watch_for": "High-speed commitment, tyre protection, and wind sensitivity.",
    },
    {
        "keywords": ["belgian", "belgium", "spa"],
        "circuit_name": "Spa-Francorchamps",
        "lap_length_km": 7.004,
        "corners": 19,
        "drs_zones": 2,
        "overtaking": "HIGH",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Weather and setup compromise",
        "track_style": "Very long lap mixing high-speed sectors with long full-throttle sections",
        "watch_for": "Weather swings, drag vs downforce tradeoff, and overtakes into Les Combes.",
    },
    {
        "keywords": ["hungarian", "hungary", "hungaroring"],
        "circuit_name": "Hungaroring",
        "lap_length_km": 4.381,
        "corners": 14,
        "drs_zones": 2,
        "overtaking": "LOW",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "VERY HIGH",
        "strategy_bias": "Track position and tyre life",
        "track_style": "Twisty, technical circuit often compared to Monaco without walls",
        "watch_for": "Qualifying pace, dirty-air management, and undercut windows.",
    },
    {
        "keywords": ["dutch", "netherlands", "zandvoort"],
        "circuit_name": "Zandvoort",
        "lap_length_km": 4.259,
        "corners": 14,
        "drs_zones": 2,
        "overtaking": "LOW",
        "tyre_stress": "HIGH",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "VERY HIGH",
        "strategy_bias": "Track position with tyre pressure on fronts",
        "track_style": "Short, quick circuit with banking and narrow racing line",
        "watch_for": "Commitment through high-speed turns and traffic in qualifying.",
    },
    {
        "keywords": ["italian", "italy", "monza"],
        "circuit_name": "Monza",
        "lap_length_km": 5.793,
        "corners": 11,
        "drs_zones": 2,
        "overtaking": "VERY HIGH",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Low-drag straight-line speed",
        "track_style": "Temple of speed with low downforce and giant braking zones",
        "watch_for": "Top speed, braking into Turn 1, and tow effects in qualifying.",
    },
    {
        "keywords": ["azerbaijan", "baku"],
        "circuit_name": "Baku City Circuit",
        "lap_length_km": 6.003,
        "corners": 20,
        "drs_zones": 2,
        "overtaking": "HIGH",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Safety-car swing track",
        "track_style": "Street circuit with castle section precision and one enormous straight",
        "watch_for": "Confidence in the narrow middle sector and late-braking passes.",
    },
    {
        "keywords": ["singapore"],
        "circuit_name": "Marina Bay",
        "lap_length_km": 4.94,
        "corners": 19,
        "drs_zones": 4,
        "overtaking": "MEDIUM",
        "tyre_stress": "HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Race management and safety-car timing",
        "track_style": "Long, punishing street race with heat, traction zones, and constant concentration load",
        "watch_for": "Brake temps, rear traction, and race interruptions.",
    },
    {
        "keywords": ["united states", "usa", "austin"],
        "circuit_name": "Circuit of the Americas",
        "lap_length_km": 5.513,
        "corners": 20,
        "drs_zones": 2,
        "overtaking": "MEDIUM",
        "tyre_stress": "HIGH",
        "degradation_risk": "HIGH",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Tyre-sensitive but passable",
        "track_style": "Mixed-layout circuit with long corners, stop-start zones, and bumps",
        "watch_for": "Tyre overheating, track limits, and front-end bite in sector one.",
    },
    {
        "keywords": ["mexico", "mexico city"],
        "circuit_name": "Autodromo Hermanos Rodriguez",
        "lap_length_km": 4.304,
        "corners": 17,
        "drs_zones": 3,
        "overtaking": "HIGH",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Drag-sensitive with long run to Turn 1",
        "track_style": "High-altitude circuit with low drag, cooling pressure, and long straight-line battles",
        "watch_for": "Engine cooling, low-downforce stability, and Turn 1 positioning.",
    },
    {
        "keywords": ["sao paulo", "são paulo", "brazilian", "brazil", "interlagos"],
        "circuit_name": "Interlagos",
        "lap_length_km": 4.309,
        "corners": 15,
        "drs_zones": 2,
        "overtaking": "HIGH",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Opportunity-heavy",
        "track_style": "Short anti-clockwise track with elevation and a strong slipstream finish",
        "watch_for": "Weather swings, restarts, and overtakes into Turns 1 and 4.",
    },
    {
        "keywords": ["las vegas"],
        "circuit_name": "Las Vegas Strip Circuit",
        "lap_length_km": 6.201,
        "corners": 17,
        "drs_zones": 2,
        "overtaking": "HIGH",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Straight-line and braking confidence",
        "track_style": "Cold-night street circuit with long straights and heavy braking zones",
        "watch_for": "Tyre warm-up, top speed, and brake stability.",
    },
    {
        "keywords": ["qatar", "lusail"],
        "circuit_name": "Lusail",
        "lap_length_km": 5.419,
        "corners": 16,
        "drs_zones": 1,
        "overtaking": "MEDIUM",
        "tyre_stress": "VERY HIGH",
        "degradation_risk": "VERY HIGH",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Tyre-limited strategy",
        "track_style": "Fast-flowing circuit with relentless sustained cornering load",
        "watch_for": "Tyre life, track limits, and pace drop-off in long stints.",
    },
    {
        "keywords": ["abu dhabi", "yas marina"],
        "circuit_name": "Yas Marina",
        "lap_length_km": 5.281,
        "corners": 16,
        "drs_zones": 2,
        "overtaking": "MEDIUM",
        "tyre_stress": "LOW",
        "degradation_risk": "LOW",
        "qualifying_importance": "HIGH",
        "strategy_bias": "Track position with some overcut potential",
        "track_style": "Modern twilight circuit with long straights and a technical final sector",
        "watch_for": "Warm-up under cooler conditions and undercut timing.",
    },
]


def empty_circuit_intelligence():
    """Return a stable empty payload for the circuit template."""
    return {
        "meta": {
            "year": None,
            "round_number": None,
            "event_name": "-",
            "circuit_name": "-",
            "location": "-",
            "country": "-",
            "history_years": [],
        },
        "summary": {
            "overtaking": "-",
            "tyre_stress": "-",
            "degradation_risk": "-",
            "qualifying_importance": "-",
            "strategy_bias": "-",
        },
        "profile": {},
        "recent_history": [],
        "recent_patterns": [],
    }


def _safe_int(value):
    """Convert a value to int when possible."""
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_name(value):
    """Normalize event names for broad matching across seasons."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = normalized.replace("grand prix", "")
    normalized = normalized.replace("formula 1", "")
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _match_profile(event_name):
    """Return the best static profile match for an event name."""
    normalized = _normalize_name(event_name)
    for profile in _CIRCUIT_PROFILES:
        if any(keyword in normalized for keyword in profile["keywords"]):
            return profile
    return {
        "circuit_name": event_name or "Unknown Circuit",
        "lap_length_km": None,
        "corners": None,
        "drs_zones": None,
        "overtaking": "MEDIUM",
        "tyre_stress": "MEDIUM",
        "degradation_risk": "MEDIUM",
        "qualifying_importance": "MEDIUM",
        "strategy_bias": "Mixed",
        "track_style": "Mixed-demand circuit",
        "watch_for": "Track evolution, tyre life, and passing opportunities around the main braking zones.",
    }


def _schedule_event(year, round_number):
    """Return the schedule row for a year and round."""
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    if schedule is None or schedule.empty:
        raise RuntimeError(f"No schedule available for {year}.")

    rows = schedule[schedule["RoundNumber"].astype(int) == int(round_number)]
    if rows.empty:
        raise RuntimeError(f"Round {round_number} does not exist in the {year} schedule.")
    return rows.iloc[0]


def _result_summary(session, position=1):
    """Return a compact summary row from official session results."""
    if session is None:
        return {}
    try:
        results = session.results
    except Exception:
        return {}

    if results is None or getattr(results, "empty", True):
        return {}

    rows = results.reset_index()
    if "Position" not in rows.columns:
        return {}

    rows = rows.dropna(subset=["Position"]).copy()
    if rows.empty:
        return {}

    row = rows[rows["Position"].astype(int) == int(position)]
    if row.empty:
        row = rows.sort_values("Position").head(1)
        if row.empty:
            return {}

    record = row.iloc[0]
    return {
        "driver": str(record.get("Abbreviation") or record.get("BroadcastName") or record.get("DriverNumber") or "-"),
        "team": str(record.get("TeamName") or "Unknown"),
    }


def _recent_history(year, event_name, limit=3):
    """Build recent race and pole history for the same event."""
    target = _match_profile(event_name)["circuit_name"]
    history = []

    for attempt_year in range(year, max(2018, year - 5), -1):
        try:
            schedule = fastf1.get_event_schedule(attempt_year, include_testing=False)
        except Exception as exc:
            logger.info("Skipping schedule lookup for %s: %s", attempt_year, exc)
            continue

        if schedule is None or schedule.empty:
            continue

        for _, event in schedule.iloc[::-1].iterrows():
            event_name_current = str(event.get("EventName", ""))
            if _match_profile(event_name_current)["circuit_name"] != target:
                continue

            round_number = _safe_int(event.get("RoundNumber"))
            if not round_number:
                continue

            winner = {}
            pole = {}

            try:
                race_session, _ = load_session(attempt_year, round_number, "Race")
                winner = _result_summary(race_session, position=1)
            except Exception as exc:
                logger.info("Skipping race history for %s round %s: %s", attempt_year, round_number, exc)

            try:
                qualifying_session, _ = load_session(attempt_year, round_number, "Qualifying")
                pole = _result_summary(qualifying_session, position=1)
            except Exception as exc:
                logger.info("Skipping qualifying history for %s round %s: %s", attempt_year, round_number, exc)

            history.append(
                {
                    "year": attempt_year,
                    "winner": winner.get("driver", "-"),
                    "winning_team": winner.get("team", "-"),
                    "pole_sitter": pole.get("driver", "-"),
                    "pole_team": pole.get("team", "-"),
                }
            )

            if len(history) >= limit:
                return history

    return history


def _recent_patterns(history, profile, meta):
    """Derive plain-language recent circuit patterns."""
    venue_label = meta.get("circuit_name") or meta.get("event_name") or meta.get("location") or "this circuit"
    if not history:
        return [
            {
                "title": "History still loading",
                "detail": f"Recent winner and pole history is not available yet for {venue_label}, so this page is leaning more on the track profile.",
            }
        ]

    winners = [row["winner"] for row in history if row.get("winner") and row["winner"] != "-"]
    poles = [row["pole_sitter"] for row in history if row.get("pole_sitter") and row["pole_sitter"] != "-"]
    winner_repeat = max(((driver, winners.count(driver)) for driver in set(winners)), key=lambda item: item[1], default=None)
    pole_repeat = max(((driver, poles.count(driver)) for driver in set(poles)), key=lambda item: item[1], default=None)

    patterns = [
        {
            "title": "Track emphasis",
            "detail": f"{profile['track_style']}. Watch for {profile['watch_for']}",
        },
        {
            "title": "Strategy bias",
            "detail": f"{profile['strategy_bias']} with qualifying importance rated {profile['qualifying_importance'].lower()}.",
        },
    ]

    if winner_repeat and winner_repeat[1] >= 2:
        patterns.append(
            {
                "title": "Recent winner trend",
                "detail": f"{winner_repeat[0]} has won {winner_repeat[1]} of the last {len(history)} visits to {venue_label}.",
            }
        )
    else:
        patterns.append(
            {
                "title": "Recent winner trend",
                "detail": "Recent winners are mixed here, which suggests execution and weekend form can outweigh pure historical trend.",
            }
        )

    if pole_repeat and pole_repeat[1] >= 2:
        patterns.append(
            {
                "title": "Saturday pattern",
                "detail": f"{pole_repeat[0]} has taken pole {pole_repeat[1]} times in the recent {venue_label} sample, reinforcing the value of qualifying here.",
            }
        )
    else:
        patterns.append(
            {
                "title": "Saturday pattern",
                "detail": "Pole has changed hands recently, so this is not a one-car-only qualifying venue in the loaded sample.",
            }
        )

    return patterns


def build_circuit_intelligence(year, round_number):
    """Build the circuit intelligence payload for a selected event."""
    event = _schedule_event(year, round_number)
    event_name = str(event.get("EventName", f"Round {round_number}"))
    cache_key = (int(year), int(round_number), event_name)
    if cache_key in _circuit_cache:
        return _circuit_cache[cache_key]

    profile = _match_profile(event_name)
    history = _recent_history(int(year), event_name, limit=3)

    payload = {
        "meta": {
            "year": int(year),
            "round_number": int(round_number),
            "event_name": event_name,
            "circuit_name": profile["circuit_name"],
            "location": str(event.get("Location", "-")),
            "country": str(event.get("Country", "-")),
            "history_years": [row["year"] for row in history],
        },
        "summary": {
            "overtaking": profile["overtaking"],
            "tyre_stress": profile["tyre_stress"],
            "degradation_risk": profile["degradation_risk"],
            "qualifying_importance": profile["qualifying_importance"],
            "strategy_bias": profile["strategy_bias"],
        },
        "profile": profile,
        "recent_history": history,
        "recent_patterns": [],
    }

    payload["recent_patterns"] = _recent_patterns(history, profile, payload["meta"])

    _circuit_cache[cache_key] = payload
    return payload
