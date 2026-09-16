"""Cross-check dashboard session rows against Formula 1's official result pages.

This is deliberately separate from the dashboard: it produces evidence for a
verification run but never changes what users see. Formula1.com is the primary
comparison source for every scheduled session. FIA classification documents are
the escalation source for any row that does not agree with Formula1.com.
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_handler import build_leaderboard, load_session, normalize_session_type


F1_BASE_URL = "https://www.formula1.com"
F1_SESSION_PATHS = {
    "Practice 1": "practice/1",
    "Practice 2": "practice/2",
    "Practice 3": "practice/3",
    "Qualifying": "qualifying",
    "Sprint Qualifying": "sprint-qualifying",
    "Sprint": "sprint-results",
    "Race": "race-result",
}


class _PageParser(HTMLParser):
    """Extract links and semantic HTML table rows without a new dependency."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._link_href = None
        self._link_text = []
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self._link_href = attributes.get("href")
            self._link_text = []
        elif tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._link_href is not None:
            self._link_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._link_href:
            self.links.append((self._link_href, _clean_text(" ".join(self._link_text))))
            self._link_href = None
            self._link_text = []
        elif tag in {"th", "td"} and self._cell is not None:
            self._row.append(_clean_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _clean_text(value):
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _fetch(url):
    response = requests.get(
        url,
        headers={"User-Agent": "f1-dashboard-source-verifier/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _result_table(html):
    parser = _PageParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        headers = table[0]
        if "Driver" in headers and any(header in headers for header in {"Pos.", "Pos"}):
            return headers, table[1:]
    raise ValueError("Could not find an official Formula 1 result table")


def _race_result_urls(year):
    parser = _PageParser()
    parser.feed(_fetch(f"{F1_BASE_URL}/en/results/{year}/races"))
    urls = []
    seen = set()
    for href, _ in parser.links:
        if not href or "/races/" not in href or not href.endswith("/race-result"):
            continue
        url = urljoin(F1_BASE_URL, href)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _as_int(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _result_number_map(session):
    results = session.results
    return {
        str(row["Abbreviation"]): str(row["DriverNumber"])
        for _, row in results.iterrows()
        if pd.notna(row.get("Abbreviation")) and pd.notna(row.get("DriverNumber"))
    }


def _official_rows(headers, rows):
    index = {header: position for position, header in enumerate(headers)}
    if "No." not in index:
        raise ValueError("Official result table does not contain driver numbers")
    return [
        {
            "number": str(row[index["No."]]),
            "laps": _as_int(row[index["Laps"]]) if "Laps" in index else None,
            "position": row[index.get("Pos.", index.get("Pos"))],
        }
        for row in rows
        if len(row) > index["No."] and _as_int(row[index["No."]]) is not None
    ]


def _compare_session(year, round_number, race_url, session_type):
    source_url = urljoin(f"{race_url.rsplit('/', 1)[0]}/", F1_SESSION_PATHS[session_type])
    try:
        headers, rows = _result_table(_fetch(source_url))
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return {"status": "not_scheduled", "source_url": source_url}
        raise

    session, laps = load_session(year, round_number, session_type)
    dashboard_rows = build_leaderboard(laps, session_type=session_type, session=session)
    number_map = _result_number_map(session)
    expected_numbers = [number_map.get(str(row["driver"])) for row in dashboard_rows]
    official = _official_rows(headers, rows)
    official_numbers = [row["number"] for row in official]

    errors = []
    if None in expected_numbers:
        errors.append("FastF1 did not provide a driver number for every displayed row")
    if expected_numbers != official_numbers:
        errors.append("displayed driver order does not match Formula 1's official table")

    if session_type in {"Race", "Sprint"}:
        expected_laps = {number_map.get(str(row["driver"])): row.get("total_laps") for row in dashboard_rows}
        for official_row in official:
            number = official_row["number"]
            if official_row["laps"] is None or number not in expected_laps:
                continue
            if expected_laps[number] != official_row["laps"]:
                errors.append(f"lap count differs for car {number}")

    report = {
        "status": "passed" if not errors else "mismatch",
        "source_url": source_url,
        "official_rows": len(official),
        "dashboard_rows": len(dashboard_rows),
        "errors": errors,
    }
    if errors:
        report["official_driver_numbers"] = official_numbers
        report["dashboard_driver_numbers"] = expected_numbers
    return report


def verify_year(year, max_sessions=None):
    """Verify all completed, scheduled dashboard session types for one year."""
    import fastf1

    schedule = fastf1.get_event_schedule(year, include_testing=False)
    race_urls = _race_result_urls(year)
    events = schedule[schedule["RoundNumber"].notna() & (schedule["RoundNumber"] > 0)].sort_values("RoundNumber")
    records = []

    for (_, event), race_url in zip(events.iterrows(), race_urls):
        round_number = int(event["RoundNumber"])
        for index in range(1, 6):
            session_type = normalize_session_type(event.get(f"Session{index}"))
            session_date = event.get(f"Session{index}DateUtc")
            if not session_type or pd.isna(session_date):
                continue
            if _utc_timestamp(session_date) > pd.Timestamp.now(tz="UTC"):
                continue
            record = {
                "year": year,
                "round": round_number,
                "event": str(event["EventName"]),
                "session": session_type,
            }
            try:
                record.update(_compare_session(year, round_number, race_url, session_type))
            except Exception as exc:
                record.update({"status": "unavailable", "errors": [str(exc)]})
            records.append(record)
            if max_sessions and len(records) >= max_sessions:
                return records
            time.sleep(0.25)
    return records


def main():
    parser = argparse.ArgumentParser(description="Verify dashboard rows against official Formula 1 result tables.")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=dt.datetime.now().year)
    parser.add_argument("--max-sessions", type=int)
    parser.add_argument("--output", type=Path, default=Path("verification-reports/official-results.json"))
    args = parser.parse_args()
    if args.start_year < 2018 or args.end_year < args.start_year:
        parser.error("Choose a range beginning in 2018.")

    records = []
    for year in range(args.start_year, args.end_year + 1):
        records.extend(verify_year(year, args.max_sessions))
        if args.max_sessions and len(records) >= args.max_sessions:
            break
    summary = {status: sum(record["status"] == status for record in records) for status in {record["status"] for record in records}}
    report = {"generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(), "records": records, "summary": summary}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    raise SystemExit(1 if summary.get("mismatch") or summary.get("unavailable") else 0)


if __name__ == "__main__":
    main()
