"""Regression checks for factual post-race dashboard calculations."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
import app as dashboard_app

from app import (
    app as flask_app,
    _build_close_finishes,
    _build_championship_impact_from_standings,
    _build_race_conditions,
    _build_race_finish,
    _build_retirement_details,
    _build_race_start,
    _build_race_progression,
    _build_race_control_events,
    _build_race_story,
    _build_pit_stop_timeline,
    _group_pit_stops,
    _build_strategy_rows,
    _build_teammate_battles,
    _build_weekend_results,
    _prewarm_latest_completed_race,
    _scheduled_weekend_sessions,
    _build_session_summary,
    _is_retirement,
    _session_category,
    _read_analysis_cache_snapshot,
    _read_warm_cache_snapshot,
    _write_analysis_cache_snapshot,
    _write_warm_cache_snapshot,
)
from data_handler import (
    _build_quali_leaderboard,
    _session_is_safely_complete,
    build_leaderboard,
    load_sessions_concurrently,
    load_session,
    normalize_session_type,
    validate_session_data,
)
import data_handler
from driver_intel import _aggregate_driver_entries, _build_round_snapshots, _summarize_drivers, empty_driver_intelligence
from season_form import _driver_form_rows, _team_form_rows, empty_season_form


class PostRaceDataTests(unittest.TestCase):
    def test_weekend_session_matrix_uses_only_scheduled_validated_sessions(self):
        schedule = pd.DataFrame(
            [{
                "RoundNumber": 1,
                "EventName": "Australian Grand Prix",
                "Session1": "Practice 1",
                "Session2": "Practice 2",
                "Session3": "Practice 3",
                "Session4": "Qualifying",
                "Session5": "Race",
            }]
        )

        def completed_session(year, round_num, session_name):
            position = {"Practice 1": 3, "Practice 2": 2, "Practice 3": 1, "Qualifying": 2, "Race": 1}[session_name]
            return {
                "error": None,
                "session_info": {"event_name": "Australian Grand Prix"},
                "leaderboard": [{"driver": "AAA", "team": "Alpha", "position": position}],
            }

        with patch("app.get_event_schedule", return_value=schedule), patch(
            "app.get_dashboard_data", side_effect=completed_session
        ):
            weekend = _build_weekend_results(2025, 1)

        self.assertEqual([session["name"] for session in weekend["sessions"]], [
            "Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race",
        ])
        self.assertNotIn("Sprint", [session["name"] for session in weekend["sessions"]])
        self.assertEqual(weekend["rows"][0]["sessions"]["Race"]["position"], 1)
        self.assertEqual(weekend["rows"][0]["sessions"]["Qualifying"]["position"], 2)
        self.assertEqual(weekend["rows"][0]["sessions"]["Practice 2"]["change"], 1)
        self.assertEqual(weekend["rows"][0]["sessions"]["Race"]["change"], 1)

    def test_weekend_session_matrix_includes_sprint_format_and_withholds_failed_session(self):
        schedule = pd.DataFrame(
            [{
                "RoundNumber": 6,
                "EventName": "Miami Grand Prix",
                "Session1": "Practice 1",
                "Session2": "Sprint Shootout",
                "Session3": "Sprint",
                "Session4": "Qualifying",
                "Session5": "Race",
            }]
        )

        def session_data(year, round_num, session_name):
            if session_name == "Sprint":
                return {"error": "Session data failed integrity checks: incomplete timing feed"}
            return {
                "error": None,
                "session_info": {"event_name": "Miami Grand Prix"},
                "leaderboard": [{"driver": "BBB", "team": "Beta", "position": 1}],
            }

        with patch("app.get_event_schedule", return_value=schedule), patch(
            "app.get_dashboard_data", side_effect=session_data
        ):
            weekend = _build_weekend_results(2025, 6)

        self.assertEqual([session["name"] for session in weekend["sessions"]], [
            "Practice 1", "Sprint Qualifying", "Qualifying", "Race",
        ])
        self.assertEqual(weekend["withheld_sessions"], [{"name": "Sprint", "short_name": "Sprint"}])

    def test_weekend_session_schedule_uses_sprint_shootout_alias(self):
        schedule = pd.DataFrame(
            [{
                "RoundNumber": 2,
                "EventName": "Sprint Grand Prix",
                "Session1": "Practice 1",
                "Session2": "Sprint Shootout",
                "Session3": "Sprint",
                "Session4": "Qualifying",
                "Session5": "Race",
            }]
        )
        with patch("app.get_event_schedule", return_value=schedule):
            sessions, event_name = _scheduled_weekend_sessions(2025, 2)

        self.assertEqual(event_name, "Sprint Grand Prix")
        self.assertEqual(sessions, ["Practice 1", "Sprint Qualifying", "Sprint", "Qualifying", "Race"])

    def test_weekend_page_renders_a_position_matrix_from_warmed_data(self):
        warm_state = {
            "key": (3, 2025, 1),
            "data": {
                "meta": {"year": 2025, "round_number": 1, "event_name": "Australian Grand Prix"},
                "sessions": [{"name": "Practice 1", "short_name": "FP1", "rows": []}],
                "withheld_sessions": [],
                "rows": [{
                    "driver": "AAA",
                    "team": "Alpha",
                    "sessions": {"Practice 1": {"position": 1, "url": "/?year=2025"}},
                }],
            },
            "error": None,
            "in_progress": False,
            "updated_at": 1.0,
        }
        with patch("app._read_available_weekend_warm_cache", return_value=warm_state):
            response = flask_app.test_client().get("/weekend?year=2025&round=1")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Session-by-Session Classification", html)
        self.assertIn(">P1<", html)

    def test_weekend_page_starts_requested_weekend_when_another_is_warming(self):
        other_weekend = {
            "key": (3, 2024, 9),
            "data": None,
            "error": None,
            "in_progress": True,
            "updated_at": None,
        }
        with patch("app._read_available_weekend_warm_cache", return_value=other_weekend), patch(
            "app._start_weekend_warmup"
        ) as start_warmup:
            response = flask_app.test_client().get("/weekend?year=2021&round=15")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Loading weekend results", response.get_data(as_text=True))
        start_warmup.assert_called_once_with(2021, 15)

    def test_stale_secondary_warmups_do_not_overwrite_newer_requests(self):
        class CapturingThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                targets.append(self.target)

        cases = [
            ("_season_warm_cache", dashboard_app._start_season_warmup, "app.build_season_form", (2025, 3), (2024, 5)),
            ("_circuit_warm_cache", dashboard_app._start_circuit_warmup, "app.build_circuit_intelligence", (2025, 3), (2024, 4)),
            ("_driver_warm_cache", dashboard_app._start_driver_warmup, "app.build_driver_intelligence", (2025, "NOR", 3), (2024, "VER", 5)),
        ]
        for cache_name, start_warmup, build_function, request_args, newer_key in cases:
            targets = []
            cache = {"key": None, "data": None, "error": None, "in_progress": False, "updated_at": None}
            with patch.object(dashboard_app, cache_name, cache), patch(
                "app._write_analysis_cache_snapshot"
            ), patch("app.threading.Thread", CapturingThread), patch(build_function, return_value={"old": "data"}):
                start_warmup(*request_args)
                cache.update({"key": newer_key, "data": None, "error": None, "in_progress": True})
                targets.pop()()

            self.assertEqual(cache["key"], newer_key)
            self.assertIsNone(cache["data"])
            self.assertTrue(cache["in_progress"])

    def test_completed_dashboard_warm_cache_is_available_across_workers(self):
        snapshot = {
            "key": (2025, 1, "Race"),
            "data": {
                "session_info": {"event_name": "Australian Grand Prix"},
                "validation": {"passed": True},
                "leaderboard": [{"driver": "Lando Norris"}],
                "session": object(),
                "laps": pd.DataFrame({"LapNumber": [1]}),
            },
            "analysis": {"race_summary": {"winner": "Lando Norris"}},
            "session_category": "race",
            "error": None,
            "updated_at": 1.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = os.path.join(directory, "warm-cache.pkl")
            with patch("app._warm_cache_file", cache_path):
                _write_warm_cache_snapshot(snapshot)
                restored = _read_warm_cache_snapshot()

        self.assertEqual(restored["key"], (2025, 1, "Race"))
        self.assertEqual(restored["data"]["leaderboard"], [{"driver": "Lando Norris"}])
        self.assertNotIn("session", restored["data"])
        self.assertNotIn("laps", restored["data"])

    def test_completed_secondary_warm_cache_is_available_across_workers(self):
        snapshot = {
            "key": (2026, 3),
            "data": {"meta": {"event_name": "Japanese Grand Prix"}},
            "error": None,
            "updated_at": 1.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = os.path.join(directory, "analysis-cache.pkl")
            _write_analysis_cache_snapshot(cache_path, snapshot)
            restored = _read_analysis_cache_snapshot(cache_path)

        self.assertEqual(restored["key"], (2026, 3))
        self.assertEqual(restored["data"], snapshot["data"])
        self.assertFalse(restored["in_progress"])

    def test_in_progress_secondary_warm_cache_prevents_duplicate_builds(self):
        snapshot = {
            "key": (2026, 3),
            "data": None,
            "error": None,
            "in_progress": True,
            "updated_at": time.time(),
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = os.path.join(directory, "analysis-cache.pkl")
            _write_analysis_cache_snapshot(cache_path, snapshot)
            restored = _read_analysis_cache_snapshot(cache_path)

        self.assertEqual(restored["key"], (2026, 3))
        self.assertIsNone(restored["data"])
        self.assertTrue(restored["in_progress"])

    def test_stale_secondary_warm_cache_allows_a_retry(self):
        snapshot = {
            "key": (2026, 3),
            "data": None,
            "error": None,
            "in_progress": True,
            "updated_at": 0.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = os.path.join(directory, "analysis-cache.pkl")
            _write_analysis_cache_snapshot(cache_path, snapshot)
            restored = _read_analysis_cache_snapshot(cache_path)

        self.assertEqual(restored["key"], (2026, 3))
        self.assertFalse(restored["in_progress"])

    def test_startup_prewarm_resolves_the_latest_completed_session(self):
        with patch("app._start_latest_warmup") as start_latest_warmup:
            _prewarm_latest_completed_race()

        start_latest_warmup.assert_called_once_with()

    def test_api_returns_422_when_integrity_checks_withhold_a_session(self):
        with patch(
            "app.get_dashboard_data",
            return_value={"error": "Session data failed integrity checks: incomplete timing feed"},
        ):
            response = flask_app.test_client().get(
                "/api/data?year=2025&round=1&session_type=Practice%202"
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "Session data failed integrity checks: incomplete timing feed")

    def test_api_serializes_teammate_rows_without_raw_timing_objects(self):
        leaderboard = [
            {
                "position": 1,
                "driver": "AAA",
                "team": "Alpha",
                "best_lap": pd.Timedelta(seconds=80),
                "best_lap_display": "1:20.000",
            }
        ]
        with patch(
            "app.get_dashboard_data",
            return_value={
                "error": None,
                "session_info": {"session_type": "Race"},
                "validation": {"passed": True},
                "leaderboard": leaderboard,
                "session": None,
                "laps": pd.DataFrame(),
            },
        ), patch(
            "app._run_race_analysis",
            return_value={
                "teammate_battles": [
                    {"first": leaderboard[0], "second": leaderboard[0]}
                ]
            },
        ):
            response = flask_app.test_client().get(
                "/api/data?year=2025&round=1&session_type=Race"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertNotIn("best_lap", payload["leaderboard"][0])
        self.assertNotIn("best_lap", payload["teammate_battles"][0]["first"])

    def test_api_reuses_a_matching_warm_dashboard_snapshot(self):
        warm_state = {
            "key": (2025, 1, "Race"),
            "data": {
                "session_info": {"session_type": "Race"},
                "validation": {"passed": True},
                "leaderboard": [{"driver": "AAA", "best_lap": pd.Timedelta(seconds=80)}],
            },
            "analysis": {"race_summary": {"winner": "AAA"}},
            "session_category": "race",
        }
        with patch("app._read_available_warm_cache", return_value=warm_state), patch(
            "app.get_dashboard_data", side_effect=AssertionError("should use the warm snapshot")
        ):
            response = flask_app.test_client().get(
                "/api/data?year=2025&round=1&session_type=Race"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["race_summary"]["winner"], "AAA")
        self.assertEqual(payload["load_time"], 0)
        self.assertNotIn("best_lap", payload["leaderboard"][0])

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

    def test_race_control_timeline_uses_recorded_events_and_completed_lap_references(self):
        track_status = pd.DataFrame(
            {
                "Time": [pd.Timedelta(seconds=55), pd.Timedelta(seconds=65), pd.Timedelta(seconds=90), pd.Timedelta(seconds=130), pd.Timedelta(seconds=180)],
                "Status": ["4", "4", "1", "6", "5"],
                "Message": ["SCDeployed", "SCDeployed", "AllClear", "VSCDeployed", "Red"],
            }
        )
        laps = pd.DataFrame(
            {
                "Time": [pd.Timedelta(seconds=50), pd.Timedelta(seconds=110), pd.Timedelta(seconds=170)],
                "LapNumber": [1, 2, 3],
            }
        )

        events = _build_race_control_events(track_status, laps=laps)

        self.assertEqual(events, [
            {"label": "Safety Car deployed", "kind": "safety-car", "lap_reference": "L2"},
            {"label": "Virtual Safety Car deployed", "kind": "vsc", "lap_reference": "L3"},
            {"label": "Red flag", "kind": "red-flag", "lap_reference": "L4"},
        ])

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

    def test_pit_stop_timeline_uses_only_recorded_pit_in_times_and_next_lap_compounds(self):
        leaderboard = [
            {"driver": "AAA", "team": "Alpha"},
            {"driver": "BBB", "team": "Beta"},
        ]
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "AAA", "AAA", "BBB", "BBB"],
                "LapNumber": [1, 9, 10, 4, 5],
                "PitInTime": [pd.NaT, pd.Timedelta(seconds=900), pd.NaT, pd.Timedelta(seconds=450), pd.NaT],
                "Compound": ["Medium", "Medium", "Hard", "Soft", "Intermediate"],
                "FastF1Generated": [False, False, False, False, False],
            }
        )

        stops = _build_pit_stop_timeline(leaderboard, laps)

        self.assertEqual(stops, [
            {"driver": "BBB", "team": "Beta", "lap_reference": "L4", "compound_code": "I", "compound": "Intermediate", "pit_in_time": pd.Timedelta(seconds=450)},
            {"driver": "AAA", "team": "Alpha", "lap_reference": "L9", "compound_code": "H", "compound": "Hard", "pit_in_time": pd.Timedelta(seconds=900)},
        ])

    def test_pit_stop_timeline_groups_same_post_lap_window_without_dropping_stops(self):
        stops = [
            {"driver": "AAA", "lap_reference": "L10"},
            {"driver": "CCC", "lap_reference": "L11"},
            {"driver": "BBB", "lap_reference": "L10"},
        ]

        windows = _group_pit_stops(stops)

        self.assertEqual([(window["lap_reference"], window["count"]) for window in windows], [
            ("L10", 2),
            ("L11", 1),
        ])
        self.assertEqual([stop["driver"] for stop in windows[0]["stops"]], ["AAA", "BBB"])

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

    def test_race_start_uses_recorded_lap_one_positions_and_excludes_pit_lane_starters(self):
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "BBB", "CCC", "AAA"],
                "LapNumber": [1, 1, 1, 1],
                "Position": [6, 5, 1, 7],
                "FastF1Generated": [False, False, False, True],
            }
        )
        leaderboard = [
            {"driver": "AAA", "team": "Alpha", "grid_position": 10},
            {"driver": "BBB", "team": "Beta", "grid_position": 2},
            {"driver": "CCC", "team": "Gamma", "grid_position": 0},
            {"driver": "DDD", "team": "Delta", "grid_position": 5},
        ]

        race_start = _build_race_start(leaderboard, laps)

        self.assertEqual(race_start["gainers"], [{"driver": "AAA", "team": "Alpha", "grid_position": 10, "lap_one_position": 6, "change": 4}])
        self.assertEqual(race_start["losers"], [{"driver": "BBB", "team": "Beta", "grid_position": 2, "lap_one_position": 5, "change": -3}])

    def test_race_finish_uses_only_complete_recorded_final_phase_positions(self):
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "AAA", "BBB", "BBB", "CCC"],
                "LapNumber": [60, 70, 60, 70, 60],
                "Position": [8, 4, 2, 6, 12],
                "FastF1Generated": [False, False, False, False, False],
            }
        )
        leaderboard = [
            {"driver": "AAA", "team": "Alpha"},
            {"driver": "BBB", "team": "Beta"},
            {"driver": "CCC", "team": "Gamma"},
        ]

        race_finish = _build_race_finish(leaderboard, laps)

        self.assertEqual(race_finish["reference_lap"], 60)
        self.assertEqual(race_finish["final_lap"], 70)
        self.assertEqual(race_finish["gainers"], [{"driver": "AAA", "team": "Alpha", "reference_position": 8, "final_position": 4, "change": 4}])
        self.assertEqual(race_finish["losers"], [{"driver": "BBB", "team": "Beta", "reference_position": 2, "final_position": 6, "change": -4}])

    def test_retirement_details_keep_official_status_and_latest_non_generated_lap(self):
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "AAA", "BBB", "BBB"],
                "LapNumber": [11, 12, 20, 21],
                "FastF1Generated": [False, True, False, False],
            }
        )
        leaderboard = [
            {"driver": "AAA", "team": "Alpha", "status": "Engine"},
            {"driver": "BBB", "team": "Beta", "status": "Collision"},
            {"driver": "CCC", "team": "Gamma", "status": "Finished"},
        ]

        details = _build_retirement_details(leaderboard, laps)

        self.assertEqual(details, [
            {"driver": "AAA", "team": "Alpha", "status": "Engine", "last_recorded_lap": 11},
            {"driver": "BBB", "team": "Beta", "status": "Collision", "last_recorded_lap": 21},
        ])

    def test_championship_impact_uses_published_before_and_after_standings(self):
        driver_before = pd.DataFrame([
            {"position": 1, "driverId": "alpha", "driverCode": "AAA", "points": 100},
            {"position": 2, "driverId": "beta", "driverCode": "BBB", "points": 99},
        ])
        driver_after = pd.DataFrame([
            {"position": 1, "driverId": "beta", "driverCode": "BBB", "points": 124},
            {"position": 2, "driverId": "alpha", "driverCode": "AAA", "points": 110},
        ])
        constructor_before = pd.DataFrame([
            {"position": 1, "constructorId": "alpha", "constructorName": "Alpha", "points": 150},
            {"position": 2, "constructorId": "beta", "constructorName": "Beta", "points": 149},
        ])
        constructor_after = pd.DataFrame([
            {"position": 1, "constructorId": "beta", "constructorName": "Beta", "points": 184},
            {"position": 2, "constructorId": "alpha", "constructorName": "Alpha", "points": 160},
        ])

        impact = _build_championship_impact_from_standings(
            driver_before, driver_after, constructor_before, constructor_after
        )

        self.assertEqual(impact["driver_leader"]["name"], "BBB")
        self.assertEqual(impact["driver_biggest_rise"], {
            "name": "BBB", "before_position": 2, "after_position": 1,
            "position_change": 1, "weekend_points": 25.0, "season_points": 124.0,
        })
        self.assertEqual(impact["constructor_leader"]["name"], "Beta")
        self.assertEqual(impact["constructor_biggest_rise"]["weekend_points"], 35.0)

    def test_race_conditions_use_recorded_readings_and_leader_lap_references(self):
        session = type(
            "Session",
            (),
            {
                "weather_data": pd.DataFrame(
                    {
                        "Time": [pd.Timedelta(seconds=5), pd.Timedelta(seconds=60), pd.Timedelta(seconds=110), pd.Timedelta(seconds=170)],
                        "AirTemp": [20.0, 20.5, 19.8, 18.7],
                        "TrackTemp": [30.0, 29.5, 27.0, 24.0],
                        "Humidity": [40.0, 42.0, 51.0, 60.0],
                        "WindSpeed": [1.0, 1.2, 2.0, 2.4],
                        "Rainfall": [False, True, True, False],
                    }
                )
            },
        )()
        laps = pd.DataFrame(
            {
                "Time": [pd.Timedelta(seconds=50), pd.Timedelta(seconds=100), pd.Timedelta(seconds=160)],
                "LapNumber": [1, 2, 3],
            }
        )

        conditions = _build_race_conditions(session, laps)

        self.assertEqual(conditions["start"], {"air_temp": 20.0, "track_temp": 30.0, "humidity": 40.0, "wind_speed": 1.0})
        self.assertEqual(conditions["finish"]["track_temp"], 24.0)
        self.assertEqual(conditions["rain_periods"], [{"samples": 2, "first_observed_lap": 2, "last_observed_lap": 3}])

    def test_close_finishes_exclude_non_numeric_classification_gaps(self):
        rows = [
            {"driver": "AAA", "position": 1, "gap_seconds": 0.0},
            {"driver": "BBB", "position": 2, "gap_seconds": 0.902},
            {"driver": "CCC", "position": 3, "gap_seconds": None},
        ]
        self.assertEqual(_build_close_finishes(rows), [{"ahead_driver": "AAA", "ahead_position": 1, "behind_driver": "BBB", "behind_position": 2, "margin_seconds": 0.902, "margin_display": "0.902s"}])

    def test_teammate_battles_use_only_the_selected_race_classification(self):
        rows = [
            {"team": "Alpha", "driver": "AAA", "position": 3, "grid_position": 5, "positions_gained": 2, "points": 15},
            {"team": "Alpha", "driver": "BBB", "position": 7, "grid_position": 4, "positions_gained": -3, "points": 6},
            {"team": "Solo", "driver": "CCC", "position": 9, "grid_position": 9, "positions_gained": 0, "points": 2},
        ]

        battles = _build_teammate_battles(rows)

        self.assertEqual(len(battles), 1)
        self.assertEqual(battles[0]["team"], "Alpha")
        self.assertEqual(battles[0]["first"]["driver"], "AAA")
        self.assertEqual(battles[0]["winner"], "AAA")

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

    def test_driver_snapshot_loader_uses_exactly_the_selected_round_window(self):
        rounds = [
            {"round_number": number, "event_name": f"Round {number}"}
            for number in range(1, 7)
        ]
        empty_session = type("Session", (), {"results": pd.DataFrame()})()
        def result_only_loader(requests):
            return {request: (empty_session, pd.DataFrame()) for request in requests}

        with patch("driver_intel._completed_rounds", return_value=rounds), patch(
            "driver_intel.load_sessions_concurrently", side_effect=result_only_loader
        ) as load_sessions_mock:
            completed, snapshots = _build_round_snapshots(2025, window=3)

        self.assertEqual(completed, rounds)
        self.assertEqual(snapshots, [])
        self.assertEqual(load_sessions_mock.call_count, 1)
        self.assertEqual(
            load_sessions_mock.call_args.args[0],
            [
                (2025, 4, "Qualifying", False), (2025, 4, "Race", False),
                (2025, 5, "Qualifying", False), (2025, 5, "Race", False),
                (2025, 6, "Qualifying", False), (2025, 6, "Race", False),
            ],
        )

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

    def test_result_only_session_load_skips_lap_timing_download(self):
        fake_session = type(
            "Session",
            (),
            {
                "laps": pd.DataFrame({"LapTime": [pd.Timedelta(seconds=80)]}),
                "load": lambda self, **kwargs: self.load_kwargs.update(kwargs),
                "load_kwargs": {},
            },
        )()
        data_handler._session_cache.clear()
        try:
            with patch("data_handler.fastf1.get_session", return_value=fake_session):
                _, laps = load_session(2025, 1, "Race", include_laps=False)

            self.assertFalse(fake_session.load_kwargs["laps"])
            self.assertTrue(laps.empty)
            self.assertTrue(data_handler.is_session_cached(2025, 1, "Race", include_laps=False))
            self.assertFalse(data_handler.is_session_cached(2025, 1, "Race", include_laps=True))
        finally:
            data_handler._session_cache.clear()

    def test_parallel_result_loader_keeps_each_session_result_or_error(self):
        requests = [
            (2025, 1, "Race", False),
            (2025, 2, "Qualifying", False),
        ]

        def fake_loader(year, round_number, session_type, include_laps):
            if round_number == 2:
                raise RuntimeError("timing unavailable")
            return ("session", pd.DataFrame())

        with patch("data_handler.load_session", side_effect=fake_loader):
            loaded = load_sessions_concurrently(requests, max_workers=2)

        self.assertEqual(loaded[requests[0]][0], "session")
        self.assertIsInstance(loaded[requests[1]], RuntimeError)

    def test_schedule_lookup_never_uses_ergast_to_reject_a_sprint_session(self):
        sprint_schedule = pd.DataFrame({"RoundNumber": [7], "Session1": ["Sprint"], "Session1DateUtc": [pd.Timestamp("2021-07-17", tz="UTC")]})
        with patch("data_handler.fastf1.get_event_schedule", side_effect=[ValueError("unavailable"), sprint_schedule]) as get_schedule:
            self.assertIs(data_handler._get_session_schedule(2021), sprint_schedule)
        self.assertEqual([call.kwargs["backend"] for call in get_schedule.call_args_list], ["fastf1", "f1timing"])

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

    def test_practice_keeps_a_listed_driver_without_a_timed_lap_unranked(self):
        session = type(
            "Session",
            (),
            {
                "results": pd.DataFrame(
                    {
                        "Abbreviation": ["AAA", "BBB"],
                        "TeamName": ["Alpha", "Beta"],
                        "Status": ["", "No time"],
                    }
                )
            },
        )()
        laps = pd.DataFrame(
            {
                "Driver": ["AAA", "BBB"],
                "Team": ["Alpha", "Beta"],
                "LapTime": [pd.Timedelta(seconds=80), pd.NaT],
                "LapNumber": [1, 1],
                "Deleted": [False, False],
            }
        )

        rows = build_leaderboard(laps, "Practice 1", session)

        self.assertEqual([row["driver"] for row in rows], ["AAA", "BBB"])
        self.assertEqual(rows[0]["position"], 1)
        self.assertIsNone(rows[1]["position"])
        self.assertEqual(rows[1]["gap_display"], "No time")
        self.assertTrue(validate_session_data(session, laps, rows, "Practice 1")["passed"])


if __name__ == "__main__":
    unittest.main()
