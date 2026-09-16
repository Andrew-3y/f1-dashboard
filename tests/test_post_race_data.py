"""Regression checks for factual post-race dashboard calculations."""

import unittest

import pandas as pd

from app import (
    app as flask_app,
    _build_close_finishes,
    _build_race_progression,
    _build_race_story,
    _build_strategy_rows,
    _is_retirement,
)
from driver_intel import _aggregate_driver_entries, _summarize_drivers, empty_driver_intelligence
from season_form import _driver_form_rows, _team_form_rows, empty_season_form


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


if __name__ == "__main__":
    unittest.main()
