import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from location_history_to_kml import (
    TrackPoint,
    activity_runs_from_data,
    input_json_files,
    main,
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

    def test_snap_chunk_sends_google_roads_request_and_reads_response(self):
        response = BytesIO(
            json.dumps(
                {
                    "snappedPoints": [
                        {"location": {"latitude": 40.001, "longitude": -73.001}},
                        {"location": {"latitude": 40.101, "longitude": -73.101}},
                    ]
                }
            ).encode("utf-8")
        )
        with patch("location_history_to_kml.urllib.request.urlopen", return_value=response) as urlopen:
            coordinates = snap_chunk(
                [
                    TrackPoint(40.0, -73.0),
                    TrackPoint(40.1, -73.1),
                ],
                "test-key",
            )

        request = urlopen.call_args.args[0]
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["key"], ["test-key"])
        self.assertEqual(query["interpolate"], ["true"])
        self.assertEqual(len(query["path"][0].split("|")), 2)
        self.assertEqual(coordinates, [(40.001, -73.001), (40.101, -73.101)])

    def test_snap_runs_chunks_at_google_limit_with_overlap(self):
        points = [TrackPoint(40.0 + index / 10_000, -73.0) for index in range(103)]
        with patch("location_history_to_kml.snap_chunk", side_effect=lambda chunk, key: [(p.latitude, p.longitude) for p in chunk]) as snap:
            snap_runs([points], "test-key")

        self.assertEqual([len(call.args[0]) for call in snap.call_args_list], [100, 4])

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