"""Regression checks for factual post-race dashboard calculations."""

import unittest
from unittest.mock import patch

import pandas as pd

from app import (
    app as flask_app,
    _build_close_finishes,
    _build_race_progression,
    _build_race_story,
    _build_strategy_rows,
    _build_session_summary,
    _is_retirement,
    _session_category,
)
from data_handler import (
    _build_quali_leaderboard,
    _session_is_safely_complete,
    build_leaderboard,
    load_session,
    normalize_session_type,
    validate_session_data,
)
import data_handler
from driver_intel import _aggregate_driver_entries, _summarize_drivers, empty_driver_intelligence
from season_form import _driver_form_rows, _team_form_rows, empty_season_form
from scripts.verify_official_results import _fia_exception, _official_rows, _result_table


class PostRaceDataTests(unittest.TestCase):
    def test_retirement_detection_uses_result_status(self):
        self.assertTrue(_is_retirement("Accident"))
        self.assertTrue(_is_retirement("Engine"))
        self.assertFalse(_is_retirement("Finished"))
        self.assertFalse(_is_retirement("+1 Lap"))
        self.assertFalse(_is_retirement("Disqualified"))

    def test_race_story_counts_deployment_events_not_status_rows(self):
        session = type(
            "Session",
            (),
            {
                "track_status": pd.DataFrame(
                    {
                        "Status": ["4", "4", "6", "1"],
                        "Message": ["SCDeployed", "AllClear", "VSCDeployed", "AllClear"],
                    }
                )
            },
        )()
        story = _build_race_story([], session=session)
        self.assertEqual(story["safety_cars"], 1)
        self.assertEqual(story["virtual_safety_cars"], 1)

    def test_strategy_merges_same_compound_fragments_and_skips_generated_laps(self):
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "AAA", "AAA", "AAA", "AAA"],
                "Stint": [1, 1, 2, 2, 3],
                "LapNumber": [1, 2, 3, 4, 5],
                "Compound": ["Medium", "Medium", "Medium", "Medium", "Hard"],
                "FastF1Generated": [False, False, False, False, True],
            }
        )
        strategy = _build_strategy_rows([{"position": 1, "driver": "AAA", "team": "Team"}], laps)
        self.assertEqual(strategy[0]["stops"], 0)
        self.assertEqual(strategy[0]["stints"], [{"compound_code": "M", "compound": "Medium", "lap_count": 4, "lap_range": "L1–4"}])

    def test_progression_keeps_only_recorded_end_of_lap_positions(self):
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "AAA", "AAA"],
                "LapNumber": [1, 1, 2],
                "Position": [2, 1, 1],
                "FastF1Generated": [False, True, False],
            }
        )
        progression = _build_race_progression([{"position": 1, "driver": "AAA", "team": "Team"}], laps)
        self.assertEqual(progression["drivers"][0]["points"], [{"lap": 1, "position": 2}, {"lap": 2, "position": 1}])

    def test_close_finishes_exclude_non_numeric_classification_gaps(self):
        rows = [
            {"driver": "AAA", "position": 1, "gap_seconds": 0.0},
            {"driver": "BBB", "position": 2, "gap_seconds": 0.902},
            {"driver": "CCC", "position": 3, "gap_seconds": None},
        ]
        self.assertEqual(_build_close_finishes(rows), [{"ahead_driver": "AAA", "ahead_position": 1, "behind_driver": "BBB", "behind_position": 2, "margin_seconds": 0.902, "margin_display": "0.902s"}])

    def test_season_rows_rank_by_actual_window_points(self):
        snapshots = [
            {
                "round_number": 1,
                "event_name": "Round 1",
                "qualifying": [
                    {"driver": "AAA", "team": "Alpha", "position": 2},
                    {"driver": "BBB", "team": "Beta", "position": 1},
                ],
                "race": [
                    {"driver": "AAA", "team": "Alpha", "position": 2, "grid_position": 2, "points": 18},
                    {"driver": "BBB", "team": "Beta", "position": 1, "grid_position": 1, "points": 25},
                ],
            }
        ]
        driver_rows = _driver_form_rows(snapshots, window=3)
        team_rows = _team_form_rows(snapshots, window=3)
        self.assertEqual(driver_rows[0]["driver"], "BBB")
        self.assertEqual(driver_rows[0]["recent_points"], 25)
        self.assertNotIn("form_index", driver_rows[0])
        self.assertEqual(team_rows[0]["team"], "Beta")

    def test_driver_summary_contains_only_observed_or_explicitly_aggregated_metrics(self):
        snapshots = [
            {
                "round_number": 1,
                "event_name": "Round 1",
                "qualifying": [{"driver": "AAA", "team": "Alpha", "position": 2}],
                "race": [{"driver": "AAA", "team": "Alpha", "position": 1, "grid_position": 2, "points": 25, "status": "Finished"}],
            }
        ]
        summaries = _summarize_drivers(_aggregate_driver_entries(snapshots), window=3)
        self.assertEqual(summaries[0]["recent_points"], 25)
        self.assertEqual(summaries[0]["avg_gain"], 1)
        self.assertNotIn("form_index", summaries[0])
        self.assertNotIn("consistency", summaries[0])

    def test_secondary_templates_render_without_subjective_metrics(self):
        season = empty_season_form()
        driver = empty_driver_intelligence()
        with flask_app.test_client() as client:
            with flask_app.test_request_context("/season"):
                season_html = flask_app.jinja_env.get_template("season.html").render(
                    season_data=season,
                    season_meta=season["meta"],
                    season_summary=season["summary"],
                    error=None,
                    loading=False,
                )
            with flask_app.test_request_context("/driver"):
                driver_html = flask_app.jinja_env.get_template("driver.html").render(
                    driver_data=driver,
                    driver_meta=driver["meta"],
                    driver_summary=driver["summary"],
                    error=None,
                    loading=False,
                )

        self.assertIn("Most Window Points", season_html)
        self.assertIn("Window Points Rank", driver_html)
        self.assertNotIn("Form Score", season_html)
        self.assertNotIn("Form Score", driver_html)
        self.assertNotIn("Recent Trend", driver_html)

    def test_all_public_session_names_normalize_and_get_the_right_view(self):
        expected = {
            "fp1": ("Practice 1", "practice"),
            "FP2": ("Practice 2", "practice"),
            "Practice 3": ("Practice 3", "practice"),
            "qualifying": ("Qualifying", "qualifying"),
            "Sprint Shootout": ("Sprint Qualifying", "qualifying"),
            "Sprint": ("Sprint", "race"),
            "Race": ("Race", "race"),
        }
        for supplied, (normalized, category) in expected.items():
            self.assertEqual(normalize_session_type(supplied), normalized)
            self.assertEqual(_session_category(supplied), category)
        self.assertIsNone(normalize_session_type("warm-up"))

    def test_official_source_parser_reads_driver_numbers_and_laps(self):
        html = """
        <table><thead><tr><th>Pos.</th><th>No.</th><th>Driver</th><th>Laps</th></tr></thead>
        <tbody><tr><td>1</td><td>44</td><td>Lewis Hamilton</td><td>58</td></tr>
        <tr><td>2</td><td>1</td><td>Max Verstappen</td><td>58</td></tr></tbody></table>
        """
        headers, rows = _result_table(html)
        self.assertEqual(_official_rows(headers, rows), [
            {"number": "44", "laps": 58, "position": "1"},
            {"number": "1", "laps": 58, "position": "2"},
        ])

    def test_fia_override_is_limited_to_recorded_final_classification_cases(self):
        italian_gp = _fia_exception(2018, 14, "Race")
        self.assertIn("fia.com", italian_gp["source_url"])
        self.assertIsNone(_fia_exception(2018, 14, "Qualifying"))
        self.assertIsNone(_fia_exception(2019, 14, "Race"))

    def test_every_public_session_type_uses_the_correct_fastf1_identifier(self):
        expected_identifiers = {
            "Practice 1": "FP1",
            "Practice 2": "FP2",
            "Practice 3": "FP3",
            "Qualifying": "Q",
            "Sprint Qualifying": "SQ",
            "Sprint": "S",
            "Race": "R",
        }
        fake_session = type(
            "Session",
            (),
            {
                "laps": pd.DataFrame({"LapTime": [pd.Timedelta(seconds=80)]}),
                "load": lambda self, **kwargs: None,
            },
        )()

        data_handler._session_cache.clear()
        try:
            with patch("data_handler.fastf1.get_session", return_value=fake_session) as get_session:
                for index, (session_type, identifier) in enumerate(expected_identifiers.items(), start=1):
                    load_session(2025, index, session_type)
                    self.assertEqual(get_session.call_args.args, (2025, index, identifier))
                self.assertEqual(get_session.call_count, len(expected_identifiers))
        finally:
            data_handler._session_cache.clear()

    def test_sprint_qualifying_uses_official_classification_not_practice_order(self):
        session = type(
            "Session",
            (),
            {
                "results": pd.DataFrame(
                    {
                        "DriverNumber": ["1", "2"],
                        "Position": [1, 2],
                        "Abbreviation": ["AAA", "BBB"],
                        "TeamName": ["Alpha", "Beta"],
                        "Q1": [pd.Timedelta(seconds=82), pd.Timedelta(seconds=81)],
                        "Q2": [pd.Timedelta(seconds=83), pd.Timedelta(seconds=82)],
                        "Q3": [pd.Timedelta(seconds=84), pd.Timedelta(seconds=84.2)],
                        "Status": ["Finished", "Finished"],
                    }
                )
            },
        )()
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "BBB"],
                "LapTime": [pd.Timedelta(seconds=84), pd.Timedelta(seconds=81)],
                "LapNumber": [1, 1],
                "Deleted": [False, False],
            }
        )

        rows = build_leaderboard(laps, "Sprint Qualifying", session)
        self.assertEqual([row["driver"] for row in rows], ["AAA", "BBB"])
        self.assertEqual(rows[1]["gap_display"], "+0.200s")

    def test_completion_check_accepts_sprint_qualifying_schedule_alias(self):
        schedule = pd.DataFrame(
            {
                "RoundNumber": [1],
                "Session1": ["Sprint Shootout"],
                "Session1DateUtc": [pd.Timestamp("2020-01-01", tz="UTC")],
            }
        )
        self.assertTrue(_session_is_safely_complete(schedule, 1, "Sprint Qualifying"))
        self.assertIsNone(_session_is_safely_complete(schedule, 1, "Practice 1"))

    def test_dashboard_template_renders_a_qualifying_timing_view_not_race_cards(self):
        leaderboard = [
            {"position": 1, "driver": "AAA", "team": "Alpha", "gap_display": "LEADER", "best_lap_display": "1:20.000", "best_lap": pd.Timedelta(seconds=80), "total_laps": 12},
            {"position": 2, "driver": "BBB", "team": "Beta", "gap_display": "+0.100s", "best_lap_display": "1:20.100", "best_lap": pd.Timedelta(seconds=80.1), "total_laps": 10},
        ]
        with flask_app.test_request_context("/?year=2025&round=1&session_type=Qualifying"):
            html = flask_app.jinja_env.get_template("dashboard.html").render(
                error=None,
                session_info={"year": 2025, "round_number": 1, "event_name": "Test Grand Prix", "session_type": "Qualifying"},
                session_category="qualifying",
                leaderboard=leaderboard,
                session_summary=_build_session_summary(leaderboard),
                race_summary={},
                race_story={},
                strategy_rows=[],
                race_progression={"drivers": []},
                close_finishes=[],
            )
        self.assertIn("Official Classification", html)
        self.assertIn("Best Session Lap", html)
        self.assertNotIn("Race Strategy", html)

    def test_qualifying_keeps_officially_classified_driver_with_no_time(self):
        session = type(
            "Session",
            (),
            {
                "results": pd.DataFrame(
                    {
                        "DriverNumber": ["1", "2"],
                        "Position": [1, 2],
                        "Abbreviation": ["AAA", "BBB"],
                        "TeamName": ["Alpha", "Beta"],
                        "Q1": [pd.Timedelta(seconds=80), pd.NaT],
                        "Q2": [pd.NaT, pd.NaT],
                        "Q3": [pd.Timedelta(seconds=79), pd.NaT],
                        "Laps": [17, 0],
                        "Status": ["Finished", "No time"],
                    }
                )
            },
        )()
        laps = pd.DataFrame(
            {
                "Driver": ["AAA"],
                "LapTime": [pd.Timedelta(seconds=79)],
                "LapNumber": [1],
                "Deleted": [False],
            }
        )
        rows = _build_quali_leaderboard(session, laps)
        self.assertEqual([row["driver"] for row in rows], ["AAA", "BBB"])
        self.assertEqual(rows[1]["best_lap_display"], "N/A")
        self.assertEqual(rows[1]["gap_display"], "No time")
        self.assertEqual(rows[0]["total_laps"], 17)
        self.assertEqual(rows[1]["total_laps"], 0)

    def test_qualifying_only_compares_q3_times_to_pole(self):
        session = type(
            "Session",
            (),
            {
                "results": pd.DataFrame(
                    {
                        "DriverNumber": ["1", "2", "3"],
                        "Position": [1, 2, 3],
                        "Abbreviation": ["AAA", "BBB", "CCC"],
                        "TeamName": ["Alpha", "Beta", "Gamma"],
                        "Q1": [pd.Timedelta(seconds=80), pd.Timedelta(seconds=80.2), pd.Timedelta(seconds=79)],
                        "Q2": [pd.Timedelta(seconds=81), pd.Timedelta(seconds=81.1), pd.Timedelta(seconds=79.5)],
                        "Q3": [pd.Timedelta(seconds=82), pd.Timedelta(seconds=82.2), pd.NaT],
                        "Status": ["Finished", "Finished", "Finished"],
                    }
                )
            },
        )()
        laps = pd.DataFrame(
            {"Driver": ["AAA"], "LapTime": [pd.Timedelta(seconds=82)], "LapNumber": [1], "Deleted": [False]}
        )

        rows = _build_quali_leaderboard(session, laps)
        self.assertEqual(rows[1]["gap_display"], "+0.200s")
        self.assertEqual(rows[2]["gap_display"], "Q2")
        self.assertIsNone(rows[2]["gap_seconds"])
        self.assertTrue(validate_session_data(session, laps, rows, "Sprint Qualifying")["passed"])

    def test_session_validation_blocks_invalid_timing_data(self):
        laps = pd.DataFrame({"Driver": ["AAA"], "LapTime": [pd.Timedelta(seconds=80)], "LapNumber": [1]})
        leaderboard = [
            {
                "position": 1,
                "driver": "AAA",
                "best_lap": pd.Timedelta(seconds=80),
                "gap_seconds": 0.0,
            }
        ]
        passed = validate_session_data(object(), laps, leaderboard, "Practice 1")
        self.assertTrue(passed["passed"])

        leaderboard[0]["gap_seconds"] = -0.1
        failed = validate_session_data(object(), laps, leaderboard, "Practice 1")
        self.assertFalse(failed["passed"])
        self.assertIn("invalid timing gap", failed["errors"][0])

    def test_practice_validation_blocks_an_incomplete_timing_feed(self):
        session = type(
            "Session",
            (),
            {"results": pd.DataFrame({"Abbreviation": ["AAA", "BBB"]})},
        )()
        laps = pd.DataFrame({"Driver": ["AAA"], "LapTime": [pd.Timedelta(seconds=80)], "LapNumber": [1]})
        leaderboard = [{"position": 1, "driver": "AAA", "best_lap": pd.Timedelta(seconds=80), "gap_seconds": 0.0}]
        validation = validate_session_data(session, laps, leaderboard, "Practice 1")
        self.assertFalse(validation["passed"])
        self.assertTrue(any("does not include every listed session participant" in error for error in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
