import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from location_history_to_kml import (
    TrackPoint,
    activity_runs_from_data,
    input_json_files,
    main,
    parse_date_filter,
    snap_chunk,
    snap_runs,
    write_kml,
)


class LocationHistoryRoutesTests(unittest.TestCase):
    def test_finds_json_files_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "history"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            first = folder / "one.json"
            second = nested / "two.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")

            self.assertEqual(set(input_json_files([folder])), {first, second})

    def test_activity_samples_are_separated_by_mode(self):
        data = {
            "rawSignals": [
                {
                    "activityRecord": {
                        "timestamp": "2024-01-01T08:00:00Z",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-01T08:00:01Z", "LatLng": "geo:40.0,-73.0"}},
                {"position": {"timestamp": "2024-01-01T08:00:02Z", "LatLng": "geo:40.1,-73.1"}},
                {
                    "activityRecord": {
                        "timestamp": "2024-01-01T08:00:03Z",
                        "probableActivities": [{"type": "ON_BICYCLE", "confidence": 0.95}],
                    }
                },
                {"position": {"timestamp": "2024-01-01T08:00:04Z", "LatLng": "geo:40.2,-73.2"}},
                {"position": {"timestamp": "2024-01-01T08:00:05Z", "LatLng": "geo:40.3,-73.3"}},
            ]
        }

        car_runs = activity_runs_from_data(data, "car")
        bike_runs = activity_runs_from_data(data, "bike")

        self.assertEqual([[point.latitude for point in run] for run in car_runs], [[40.0, 40.1]])
        self.assertEqual([[point.latitude for point in run] for run in bike_runs], [[40.2, 40.3]])

    def test_parse_date_filter_supports_day_and_range(self):
        single = parse_date_filter("2024-01-15")
        range_filter = parse_date_filter("2024-01-15:2024-01-18")

        self.assertEqual(single, ("2024-01-15", "2024-01-15"))
        self.assertEqual(range_filter, ("2024-01-15", "2024-01-18"))

    def test_activity_runs_from_data_respects_date_filter(self):
        data = {
            "rawSignals": [
                {
                    "activityRecord": {
                        "timestamp": "2024-01-01T08:00:00Z",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-01T08:00:01Z", "LatLng": "geo:40.0,-73.0"}},
                {"position": {"timestamp": "2024-01-01T08:00:02Z", "LatLng": "geo:40.1,-73.1"}},
                {"position": {"timestamp": "2024-01-02T08:00:02Z", "LatLng": "geo:40.2,-73.2"}},
            ]
        }

        runs = activity_runs_from_data(data, "car", date_filter=parse_date_filter("2024-01-01"))

        self.assertEqual(len(runs), 1)
        self.assertEqual([[point.latitude for point in run] for run in runs], [[40.0, 40.1]])

    def test_date_filter_ignores_time_of_day_and_preserves_timestamp_date(self):
        data = {
            "rawSignals": [
                {
                    "activityRecord": {
                        "timestamp": "2024-01-14T23:59:59-07:00",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-14T23:59:59-07:00", "LatLng": "geo:39.9,-73.0"}},
                {"position": {"timestamp": "2024-01-14T23:59:59-07:00", "LatLng": "geo:39.8,-73.1"}},
                {
                    "activityRecord": {
                        "timestamp": "2024-01-15T00:00:00+14:00",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-15T00:00:01+14:00", "LatLng": "geo:40.0,-73.0"}},
                {
                    "activityRecord": {
                        "timestamp": "2024-01-15T23:59:58-07:00",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-15T23:59:59-07:00", "LatLng": "geo:40.1,-73.1"}},
                {"position": {"timestamp": "2024-01-16T00:00:00+14:00", "LatLng": "geo:40.2,-73.2"}},
            ]
        }

        runs = activity_runs_from_data(data, "car", date_filter=parse_date_filter("2024-01-15"))

        self.assertEqual([[point.latitude for point in run] for run in runs], [[40.0, 40.1]])

    def test_semantic_segments_provide_vehicle_routes_when_raw_signals_are_empty(self):
        data = {
            "rawSignals": [],
            "semanticSegments": [
                {
                    "startTime": "2025-08-01T00:00:00+02:00",
                    "endTime": "2025-08-01T00:05:00+02:00",
                    "activity": {
                        "start": {"latLng": "49.0°, 19.0°"},
                        "end": {"latLng": "49.1°, 19.1°"},
                        "topCandidate": {"type": "IN_PASSENGER_VEHICLE"},
                    },
                },
                {
                    "startTime": "2025-08-02T08:00:00+02:00",
                    "endTime": "2025-08-02T08:05:00+02:00",
                    "activity": {
                        "start": {"latLng": "49.2°, 19.2°"},
                        "end": {"latLng": "49.3°, 19.3°"},
                        "topCandidate": {"type": "WALKING"},
                    },
                },
                {
                    "startTime": "2025-09-01T08:00:00+02:00",
                    "endTime": "2025-09-01T08:05:00+02:00",
                    "activity": {
                        "start": {"latLng": "49.4°, 19.4°"},
                        "end": {"latLng": "49.5°, 19.5°"},
                        "topCandidate": {"type": "IN_PASSENGER_VEHICLE"},
                    },
                },
            ],
        }

        runs = activity_runs_from_data(data, "car", parse_date_filter("2025-08-01:2025-08-31"))

        self.assertEqual(
            [[(point.latitude, point.longitude) for point in run] for run in runs],
            [[(49.0, 19.0), (49.1, 19.1)]],
        )

    def test_snap_chunk_only_corrects_small_local_jitter(self):
        coordinates = snap_chunk(
            [
                TrackPoint(0.0, 0.0),
                TrackPoint(5.0, 5.0),
                TrackPoint(10.0, 0.0),
            ]
        )

        self.assertEqual(len(coordinates), 3)
        self.assertEqual(coordinates[1], (5.0, 5.0))

    def test_snap_chunk_projects_points_onto_local_route(self):
        coordinates = snap_chunk(
            [
                TrackPoint(40.0, -73.0),
                TrackPoint(40.1, -73.1),
                TrackPoint(40.2, -73.2),
            ]
        )

        self.assertEqual(len(coordinates), 3)
        self.assertAlmostEqual(coordinates[0][0], 40.0)
        self.assertAlmostEqual(coordinates[0][1], -73.0)
        self.assertAlmostEqual(coordinates[1][0], 40.1)
        self.assertAlmostEqual(coordinates[1][1], -73.1)
        self.assertAlmostEqual(coordinates[2][0], 40.2)
        self.assertAlmostEqual(coordinates[2][1], -73.2)

    def test_snap_runs_uses_one_call_per_run(self):
        points = [TrackPoint(40.0 + index / 10_000, -73.0) for index in range(103)]
        with patch("location_history_to_kml.snap_chunk", side_effect=lambda chunk, key: [(p.latitude, p.longitude) for p in chunk]) as snap:
            snap_runs([points], "test-key")

        self.assertEqual(len(snap.call_args_list), 1)
        self.assertEqual(len(snap.call_args_list[0].args[0]), 103)

    def test_without_snap_writes_recorded_points_without_an_api_key(self):
        data = {
            "rawSignals": [
                {
                    "activityRecord": {
                        "timestamp": "2024-01-01T08:00:00Z",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-01T08:00:01Z", "LatLng": "geo:40.0,-73.0"}},
                {"position": {"timestamp": "2024-01-01T08:00:02Z", "LatLng": "geo:40.1,-73.1"}},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "timeline.json"
            output = Path(directory) / "recorded.kml"
            source.write_text(json.dumps(data), encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True), patch(
                "sys.argv", ["location_history_to_kml.py", str(source), str(output), "--car"]
            ):
                self.assertEqual(main(), 0)

            root = ET.parse(output).getroot()
            namespace = "http://www.opengis.net/kml/2.2"
            coordinates = root.find(f".//{{{namespace}}}coordinates")
            self.assertEqual(coordinates.text, "-73.000000,40.000000,0 -73.100000,40.100000,0")

    def test_writes_routed_coordinates_as_kml(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "route.kml"
            count = write_kml([[(40.0, -73.0), (40.1, -73.1)]], output, "Test route")

            root = ET.parse(output).getroot()
            namespace = "http://www.opengis.net/kml/2.2"
            coordinates = root.find(f".//{{{namespace}}}coordinates")
            self.assertEqual(count, 2)
            self.assertEqual(coordinates.text, "-73.000000,40.000000,0 -73.100000,40.100000,0")
            self.assertIn(b'<kml xmlns="http://www.opengis.net/kml/2.2"', output.read_bytes())


if __name__ == "__main__":
    unittest.main()