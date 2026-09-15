"""Factual event history for the circuit page."""

import logging
import re
import unicodedata

import fastf1
import pandas as pd

from data_handler import load_session


logger = logging.getLogger(__name__)

_circuit_cache = {}


def empty_circuit_intelligence():
    """Return a stable empty payload for the circuit template."""
    return {
        "meta": {
            "year": None,
            "round_number": None,
            "event_name": "-",
            "location": "-",
            "country": "-",
            "history_years": [],
        },
        "recent_history": [],
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
    """Normalize Grand Prix names for matching across seasons."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = normalized.replace("grand prix", "")
    normalized = normalized.replace("formula 1", "")
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


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
    """Build official race-winner and pole history for the same Grand Prix."""
    target = _normalize_name(event_name)
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
            current_name = str(event.get("EventName", ""))
            if _normalize_name(current_name) != target:
                continue

            round_number = _safe_int(event.get("RoundNumber"))
            if not round_number:
                continue

            winner = {}
            pole = {}
            try:
                race_session, _ = load_session(attempt_year, round_number, "Race")
                winner = _result_summary(race_session)
            except Exception as exc:
                logger.info("Skipping race history for %s round %s: %s", attempt_year, round_number, exc)

            try:
                qualifying_session, _ = load_session(attempt_year, round_number, "Qualifying")
                pole = _result_summary(qualifying_session)
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


def build_circuit_intelligence(year, round_number):
    """Build the factual event-history payload for a selected round."""
    event = _schedule_event(year, round_number)
    event_name = str(event.get("EventName", f"Round {round_number}"))
    cache_key = (int(year), int(round_number), event_name)
    if cache_key in _circuit_cache:
        return _circuit_cache[cache_key]

    history = _recent_history(int(year), event_name, limit=3)
    payload = {
        "meta": {
            "year": int(year),
            "round_number": int(round_number),
            "event_name": event_name,
            "location": str(event.get("Location", "-")),
            "country": str(event.get("Country", "-")),
            "history_years": [row["year"] for row in history],
        },
        "recent_history": history,
    }

    _circuit_cache[cache_key] = payload
    return payload
