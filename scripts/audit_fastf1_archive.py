"""Audit every completed FastF1 session from 2018 through a chosen end year.

The script never changes dashboard data. It writes a JSON evidence report with
one record per scheduled session so missing source data and failed integrity
checks are visible instead of being silently accepted.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import fastf1
import pandas as pd

# Allow this script to run directly from the repository root or from scripts/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_handler import (
    build_leaderboard,
    load_session,
    normalize_session_type,
    validate_session_data,
)


def _is_rate_limited(error):
    """Return whether FastF1 stopped the run because of its public API limit."""
    message = str(error).lower()
    return "ratelimitexceeded" in message or "calls/h" in message


def _record_key(record):
    """Return the stable identity for one scheduled session audit record."""
    return record.get("year"), record.get("round"), record.get("session")


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _completed_session(event, session_name, start, now):
    """Return true once a scheduled session is safely beyond its buffer."""
    if pd.isna(session_name) or pd.isna(start):
        return False
    normalized = normalize_session_type(session_name)
    if normalized is None:
        return False
    buffer_hours = 4 if normalized == "Race" else 2
    return _utc_timestamp(start) + pd.Timedelta(hours=buffer_hours) <= now


def _event_sessions(event, now):
    """Yield unique, completed, supported session names from a schedule row."""
    seen = set()
    for index in range(1, 6):
        name = event.get(f"Session{index}")
        start = event.get(f"Session{index}DateUtc")
        normalized = normalize_session_type(name)
        if normalized and normalized not in seen and _completed_session(event, name, start, now):
            seen.add(normalized)
            yield normalized


def audit_year(year, now, verified_keys=None):
    """Return audit records for all completed sessions in one championship year."""
    verified_keys = verified_keys or set()
    try:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
    except Exception as exc:
        return [{
            "year": year,
            "round": None,
            "event": None,
            "session": None,
            "source": "FastF1",
            "status": "unavailable",
            "drivers": 0,
            "checks": [],
            "errors": [str(exc)],
        }]
    records = []
    for _, event in schedule.iterrows():
        round_number = event.get("RoundNumber")
        if pd.isna(round_number) or int(round_number) <= 0:
            continue
        round_number = int(round_number)
        event_name = str(event.get("EventName", f"Round {round_number}"))
        for session_type in _event_sessions(event, now):
            if (year, round_number, session_type) in verified_keys:
                continue
            record = {
                "year": year,
                "round": round_number,
                "event": event_name,
                "session": session_type,
                "source": "FastF1",
            }
            try:
                session, laps = load_session(year, round_number, session_type)
                leaderboard = build_leaderboard(laps, session_type=session_type, session=session)
                validation = validate_session_data(session, laps, leaderboard, session_type)
                record.update(
                    {
                        "status": "passed" if validation["passed"] else "failed",
                        "drivers": len(leaderboard),
                        "checks": validation["checks"],
                        "errors": validation["errors"],
                    }
                )
            except Exception as exc:  # source failures are reportable audit outcomes
                status = "unavailable" if _is_rate_limited(exc) else "failed"
                record.update({"status": status, "drivers": 0, "checks": [], "errors": [str(exc)]})
            records.append(record)
            if record["status"] == "unavailable" and _is_rate_limited(record["errors"][0]):
                return records
    return records


def main():
    parser = argparse.ArgumentParser(description="Audit completed FastF1 sessions across the modern archive.")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=dt.datetime.now(dt.timezone.utc).year)
    parser.add_argument("--output", type=Path, default=Path("audit-reports/fastf1-archive-audit.json"))
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse already-passed records in the output report and retry only unfinished sessions.",
    )
    args = parser.parse_args()
    if args.start_year < 2018 or args.end_year < args.start_year:
        parser.error("Choose a valid range beginning in 2018.")

    now = pd.Timestamp(dt.datetime.now(dt.timezone.utc))
    records_by_key = {}
    if args.resume and args.output.exists():
        previous_report = json.loads(args.output.read_text(encoding="utf-8"))
        for record in previous_report.get("records", []):
            key = _record_key(record)
            if args.start_year <= (record.get("year") or 0) <= args.end_year:
                records_by_key[key] = record

    for year in range(args.start_year, args.end_year + 1):
        print(f"Auditing {year}...", flush=True)
        verified_keys = {
            key for key, record in records_by_key.items()
            if record.get("year") == year and record.get("status") == "passed"
        }
        year_records = audit_year(year, now, verified_keys)
        for record in year_records:
            records_by_key[_record_key(record)] = record
        if any(record["status"] == "unavailable" and _is_rate_limited(record["errors"][0]) for record in year_records):
            print("Stopped because FastF1's public API rate limit was reached.", flush=True)
            break

    records = sorted(
        records_by_key.values(),
        key=lambda record: (record.get("year") or 0, record.get("round") or 0, record.get("session") or ""),
    )
    passed = sum(record["status"] == "passed" for record in records)
    failed = sum(record["status"] == "failed" for record in records)
    unavailable = sum(record["status"] == "unavailable" for record in records)
    report = {
        "coverage": {"start_year": args.start_year, "end_year": args.end_year, "session_scope": "completed scheduled sessions"},
        "generated_at": now.isoformat(),
        "source": "FastF1",
        "summary": {"sessions_checked": len(records), "passed": passed, "failed": failed, "unavailable": unavailable},
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{passed}/{len(records)} sessions passed. Report: {args.output}")
    raise SystemExit(1 if failed or unavailable else 0)


if __name__ == "__main__":
    main()
