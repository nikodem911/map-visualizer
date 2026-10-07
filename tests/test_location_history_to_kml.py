import json
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import osmnx as ox
from pyproj import CRS, Transformer
from shapely.geometry import LineString

from route_geometry import TrackPoint
from location_history_to_kml import (
    activity_runs_from_data,
    input_json_files,
    main,
    parse_date_filter,
    write_kml,
)
from snap_strategies import snap_runs
from snap_strategies.local import snap_chunk
from snap_strategies.mappymatch import OSMTileCache, SnapOptions


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
                    "timelinePath": [
                        {"time": "2025-07-31T23:59:00+02:00", "point": "48.9°, 18.9°"},
                        {"time": "2025-08-01T00:01:00+02:00", "point": "49.03°, 19.03°"},
                        {"time": "2025-08-01T00:03:00+02:00", "point": "49.07°, 19.07°"},
                        {"time": "2025-08-01T00:06:00+02:00", "point": "49.2°, 19.2°"},
                    ],
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
            [[(49.0, 19.0), (49.03, 19.03), (49.07, 19.07), (49.1, 19.1)]],
        )

    def test_semantic_route_drops_impossible_activity_endpoint(self):
        data = {
            "semanticSegments": [
                {
                    "startTime": "2025-08-01T10:00:00+02:00",
                    "endTime": "2025-08-01T10:05:00+02:00",
                    "activity": {
                        "start": {"latLng": "47.24298°, 15.99675°"},
                        "end": {"latLng": "46.65°, 14.52°"},
                        "topCandidate": {"type": "IN_PASSENGER_VEHICLE"},
                    },
                    "timelinePath": [
                        {"time": "2025-08-01T10:00:03+02:00", "point": "46.63903°, 14.44321°"},
                        {"time": "2025-08-01T10:01:00+02:00", "point": "46.64034°, 14.47017°"},
                        {"time": "2025-08-01T10:05:00+02:00", "point": "46.65°, 14.52°"},
                    ],
                }
            ]
        }

        runs = activity_runs_from_data(data, "car")

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][0].latitude, 46.63903)

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
        with patch("snap_strategies.local.snap_chunk", side_effect=lambda chunk: [(p.latitude, p.longitude) for p in chunk]) as snap:
            snap_runs([points])

        self.assertEqual(len(snap.call_args_list), 1)
        self.assertEqual(len(snap.call_args_list[0].args[0]), 103)

    def test_snap_runs_splits_impossible_jumps(self):
        start = datetime(2025, 8, 1, tzinfo=timezone.utc)
        points = [
            TrackPoint(49.0, 19.0, start),
            TrackPoint(49.001, 19.001, start + timedelta(minutes=1)),
            TrackPoint(48.0, 17.0, start + timedelta(minutes=1, seconds=3)),
            TrackPoint(48.001, 17.001, start + timedelta(minutes=2)),
        ]

        with patch("snap_strategies.local.snap_chunk", side_effect=lambda chunk: [(p.latitude, p.longitude) for p in chunk]) as snap:
            routes = snap_runs([points])

        self.assertEqual(len(routes), 2)
        self.assertEqual(len(snap.call_args_list), 2)
        self.assertEqual(routes[0], [(49.0, 19.0), (49.001, 19.001)])
        self.assertEqual(routes[1], [(48.0, 17.0), (48.001, 17.001)])

    def test_snap_runs_deduplicates_same_road_but_keeps_parallel_road(self):
        runs = [
            [TrackPoint(49.0, 19.0), TrackPoint(49.0, 19.01)],
            [TrackPoint(49.00003, 19.0), TrackPoint(49.00003, 19.01)],
            [TrackPoint(49.00003, 19.01), TrackPoint(49.00003, 19.0)],
            [TrackPoint(49.00045, 19.0), TrackPoint(49.00045, 19.01)],
        ]

        routes = snap_runs(runs)

        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0], [(49.0, 19.0), (49.0, 19.01)])
        self.assertEqual(routes[1], [(49.00045, 19.0), (49.00045, 19.01)])

    def test_snap_runs_keeps_unique_part_of_partially_repeated_route(self):
        runs = [
            [TrackPoint(49.0, 19.0), TrackPoint(49.0, 19.01)],
            [TrackPoint(49.0, 19.003), TrackPoint(49.0, 19.01), TrackPoint(49.003, 19.01)],
        ]

        routes = snap_runs(runs)

        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[1], [(49.0, 19.01), (49.003, 19.01)])

    def test_cli_dispatches_selected_snap_strategy(self):
        data = {
            "rawSignals": [
                {
                    "activityRecord": {
                        "timestamp": "2024-01-01T08:00:00Z",
                        "probableActivities": [{"type": "IN_ROAD_VEHICLE", "confidence": 0.9}],
                    }
                },
                {"position": {"timestamp": "2024-01-01T08:00:01Z", "LatLng": "geo:40.0,-73.0"}},
                {"position": {"timestamp": "2024-01-01T08:00:02Z", "LatLng": "geo:40.0001,-73.0001"}},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "timeline.json"
            output = Path(directory) / "route.kml"
            source.write_text(json.dumps(data), encoding="utf-8")
            with patch(
                "sys.argv",
                ["location_history_to_kml.py", str(source), str(output), "--car", "--snap", "-S", "local"],
            ), patch("location_history_to_kml.snap_runs", return_value=[[(40.0, -73.0), (40.1, -73.1)]]) as snap:
                self.assertEqual(main(), 0)

        self.assertEqual(snap.call_args.args[1], "local")

    def test_osm_tile_cache_persists_and_reuses_downloaded_map(self):
        class FakeMap:
            downloads = 0
            loads = 0

            def __init__(self):
                self.g = object()

            @classmethod
            def from_geofence(cls, *_args, **_kwargs):
                cls.downloads += 1
                return cls()

            @classmethod
            def from_file(cls, _path):
                cls.loads += 1
                return cls()

            def to_file(self, path):
                Path(path).write_bytes(b"cached OSM graph")

        class FakeGeofence:
            def __init__(self, **_kwargs):
                pass

        class FakeNetworkType:
            DRIVE = SimpleNamespace(value="drive")
            BIKE = SimpleNamespace(value="bike")

        with tempfile.TemporaryDirectory() as directory:
            cache = OSMTileCache(Path(directory))
            cache.get_tile_map((98, 38), FakeMap, FakeGeofence, FakeNetworkType.DRIVE)
            disk_cache = OSMTileCache(Path(directory))
            disk_cache.get_tile_map((98, 38), FakeMap, FakeGeofence, FakeNetworkType.DRIVE)

        self.assertEqual(FakeMap.downloads, 1)
        self.assertEqual(FakeMap.loads, 1)
        self.assertEqual(cache.fetch_attempts, 1)
        self.assertEqual(disk_cache.disk_cache_hits, 1)

    def test_osm_tile_fetch_error_includes_attempt_number_and_cause(self):
        class FakeNetworkType:
            DRIVE = SimpleNamespace(value="drive")

        class FakeGeofence:
            def __init__(self, **_kwargs):
                pass

        class FakeMap:
            @classmethod
            def from_geofence(cls, *_args, **_kwargs):
                raise ConnectionError("connection refused")

        with tempfile.TemporaryDirectory() as directory:
            cache = OSMTileCache(Path(directory))
            with self.assertLogs("snap_strategies.mappymatch", level="ERROR") as log_output:
                with self.assertRaisesRegex(
                    ValueError,
                    r"OSM tile fetch #1 failed.*ConnectionError: connection refused",
                ):
                    cache.get_tile_map((99, 38), FakeMap, FakeGeofence, FakeNetworkType.DRIVE)

        self.assertEqual(cache.fetch_attempts, 1)
        self.assertEqual(cache.fetch_failures, 1)
        self.assertTrue(any("OSM tile fetch #1 failed" in message for message in log_output.output))

    def test_mappymatch_strategy_reuses_map_and_returns_road_geometry(self):
        class FakeNetworkType:
            DRIVE = SimpleNamespace(value="drive")
            BIKE = SimpleNamespace(value="bike")

        class FakeGeofence:
            def __init__(self, **_kwargs):
                pass

        class FakeMap:
            downloads = 0
            requested_networks = []
            requested_endpoints = []

            def __init__(self, graph=None):
                self.g = graph or object()
                self.crs = CRS.from_epsg(3857)

            @classmethod
            def from_geofence(cls, _geofence, **kwargs):
                cls.downloads += 1
                cls.requested_networks.append(kwargs["network_type"].value)
                cls.requested_endpoints.append(ox.settings.overpass_url)
                return cls()

            @classmethod
            def from_file(cls, _path):
                return cls()

            def to_file(self, path):
                Path(path).write_bytes(b"cached OSM graph")

        class FakeTrace:
            @classmethod
            def from_dataframe(cls, frame, xy=True):
                return frame

        class FakeMatcher:
            calls = 0

            def __init__(self, _road_map, **_kwargs):
                pass

            def match_trace(self, _trace):
                type(self).calls += 1
                project = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
                road = LineString([project(19.0, 49.0), project(19.01, 49.0)])
                return SimpleNamespace(path=[SimpleNamespace(geom=road)], matches=[])

        fake_networkx = SimpleNamespace(compose_all=lambda graphs: graphs[0])
        start = datetime(2024, 8, 1, tzinfo=timezone.utc)
        run = [
            TrackPoint(49.0, 19.0, start),
            TrackPoint(49.0, 19.005, start + timedelta(minutes=1)),
            TrackPoint(49.0, 19.01, start + timedelta(minutes=2)),
        ]
        original_overpass_url = ox.settings.overpass_url

        with tempfile.TemporaryDirectory() as directory, patch(
            "snap_strategies.mappymatch._mappymatch_components",
            return_value=(FakeGeofence, FakeTrace, FakeMap, FakeNetworkType, FakeMatcher, fake_networkx),
        ), patch.object(ox.settings, "overpass_url", ox.settings.overpass_url):
            routes = snap_runs(
                [run, run],
                "mappymatch",
                SnapOptions(
                    Path(directory),
                    network_type="bike",
                    overpass_url="http://127.0.0.1:12345/api/",
                ),
            )
            self.assertEqual(ox.settings.overpass_url, original_overpass_url)

        self.assertEqual(FakeMap.downloads, 1)
        self.assertEqual(FakeMap.requested_networks, ["bike"])
        self.assertEqual(FakeMap.requested_endpoints, ["http://127.0.0.1:12345/api"])
        self.assertEqual(FakeMatcher.calls, 2)
        self.assertEqual(len(routes), 1)
        self.assertTrue(math.isclose(routes[0][0][0], 49.0, abs_tol=1e-6))
        self.assertTrue(math.isclose(routes[0][-1][1], 19.01, abs_tol=1e-6))

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