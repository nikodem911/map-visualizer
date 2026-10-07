from __future__ import annotations

import logging
import hashlib
import math
import os
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from route_geometry import TrackPoint, distance_meters
from snap_strategies.common import (
    Route,
    deduplicate_route_edges,
    index_route_edges,
    split_track_at_gaps,
)


OSM_TILE_DEGREES = 0.5
OSM_TILE_PADDING_DEGREES = 0.02
OSM_TILE_SAMPLE_DISTANCE_METERS = 8_000
OSM_TILE_MEMORY_CACHE_SIZE = 8
MAPPY_TRACE_CHUNK_SIZE = 250
MAPPY_MATCH_MAX_DISTANCE_METERS = 10_000
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapOptions:
    osm_cache_dir: Path | None = None
    network_type: str = "drive"
    overpass_url: str | None = None


def _mappymatch_components() -> tuple[Any, Any, Any, Any, Any, Any]:
    try:
        from mappymatch.constructs.geofence import Geofence
        from mappymatch.constructs.trace import Trace
        from mappymatch.maps.nx.nx_map import NxMap
        from mappymatch.maps.nx.readers.osm_readers import NetworkType
        from mappymatch.matchers.lcss.lcss import LCSSMatcher
        import networkx as nx
    except ImportError as error:
        raise ValueError(
            "The mappymatch strategy requires its dependencies. "
            "Install them with: python -m pip install -r requirements.txt"
        ) from error
    return Geofence, Trace, NxMap, NetworkType, LCSSMatcher, nx


class OSMTileCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "map-visualizer" / "osm"
        self._memory_cache: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
        self._combined_cache: OrderedDict[tuple[str, tuple[tuple[int, int], ...]], Any] = OrderedDict()
        self.fetch_attempts = 0
        self.fetch_failures = 0
        self.memory_cache_hits = 0
        self.disk_cache_hits = 0

    @staticmethod
    def tile_for_point(point: TrackPoint) -> tuple[int, int]:
        return (
            math.floor(point.latitude / OSM_TILE_DEGREES),
            math.floor(point.longitude / OSM_TILE_DEGREES),
        )

    def tiles_for_trace(self, points: list[TrackPoint]) -> tuple[tuple[int, int], ...]:
        tiles: set[tuple[int, int]] = set()
        if not points:
            return ()
        tiles.add(self.tile_for_point(points[0]))
        for first, second in zip(points, points[1:]):
            distance = distance_meters(first, second)
            steps = max(1, math.ceil(distance / OSM_TILE_SAMPLE_DISTANCE_METERS))
            for step in range(1, steps + 1):
                fraction = step / steps
                point = TrackPoint(
                    first.latitude + (second.latitude - first.latitude) * fraction,
                    first.longitude + (second.longitude - first.longitude) * fraction,
                )
                tiles.add(self.tile_for_point(point))
        return tuple(sorted(tiles))

    def _tile_cache_path(self, tile: tuple[int, int], network_type: Any, endpoint: str) -> Path:
        try:
            mappymatch_version = version("mappymatch")
        except PackageNotFoundError:
            mappymatch_version = "not-installed"
        try:
            osmnx_version = version("osmnx")
        except PackageNotFoundError:
            osmnx_version = "not-installed"
        endpoint_id = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]
        folder = self.cache_dir / (
            f"mappymatch-{mappymatch_version}-osmnx-{osmnx_version}-"
            f"{network_type.value}-{endpoint_id}-v3"
        )
        return folder / f"{tile[0]}_{tile[1]}.pickle"

    def get_tile_map(self, tile: tuple[int, int], nx_map_type: Any, geofence_type: Any, network_type: Any) -> Any:
        cache_key = (network_type.value, *tile)
        cached = self._memory_cache.get(cache_key)
        if cached is not None:
            self.memory_cache_hits += 1
            self._memory_cache.move_to_end(cache_key)
            logger.debug("OSM tile memory-cache hit: profile=%s tile=%s", network_type.value, tile)
            return cached

        import osmnx as ox

        endpoint = ox.settings.overpass_url.rstrip("/")
        cache_path = self._tile_cache_path(tile, network_type, endpoint)
        if cache_path.is_file():
            try:
                tile_map = nx_map_type.from_file(cache_path)
            except (OSError, EOFError, ValueError, TypeError):
                cache_path.unlink(missing_ok=True)
            else:
                self.disk_cache_hits += 1
                logger.debug("OSM tile disk-cache hit: profile=%s tile=%s path=%s", network_type.value, tile, cache_path)
                return self._remember_tile(cache_key, tile_map)

        from pyproj import CRS
        from shapely.geometry import box

        latitude_index, longitude_index = tile
        minimum_latitude = max(-85.0, latitude_index * OSM_TILE_DEGREES - OSM_TILE_PADDING_DEGREES)
        maximum_latitude = min(85.0, (latitude_index + 1) * OSM_TILE_DEGREES + OSM_TILE_PADDING_DEGREES)
        minimum_longitude = max(-180.0, longitude_index * OSM_TILE_DEGREES - OSM_TILE_PADDING_DEGREES)
        maximum_longitude = min(180.0, (longitude_index + 1) * OSM_TILE_DEGREES + OSM_TILE_PADDING_DEGREES)
        geofence = geofence_type(
            crs=CRS.from_epsg(4326),
            geometry=box(minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude),
        )
        self.fetch_attempts += 1
        fetch_number = self.fetch_attempts
        started_at = time.monotonic()
        logger.info(
            "OSM tile fetch #%d starting: profile=%s tile=%s endpoint=%s",
            fetch_number,
            network_type.value,
            tile,
            endpoint,
        )
        try:
            tile_map = nx_map_type.from_geofence(
                geofence,
                xy=True,
                network_type=network_type,
                filter_to_largest_component=False,
            )
        except Exception as error:
            self.fetch_failures += 1
            logger.exception(
                "OSM tile fetch #%d failed: profile=%s tile=%s endpoint=%s after %.1fs",
                fetch_number,
                network_type.value,
                tile,
                endpoint,
                time.monotonic() - started_at,
            )
            raise ValueError(
                f"OSM tile fetch #{fetch_number} failed for profile={network_type.value}, tile={tile}, "
                f"endpoint={endpoint}: {type(error).__name__}: {error}"
            ) from error
        logger.info(
            "OSM tile fetch #%d succeeded: profile=%s tile=%s nodes=%s edges=%s elapsed=%.1fs",
            fetch_number,
            network_type.value,
            tile,
            tile_map.g.number_of_nodes() if hasattr(tile_map.g, "number_of_nodes") else "?",
            tile_map.g.number_of_edges() if hasattr(tile_map.g, "number_of_edges") else "?",
            time.monotonic() - started_at,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cache_path.parent,
            prefix=f"{cache_path.stem}-",
            suffix=".pickle",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        try:
            tile_map.to_file(temporary_path)
            os.replace(temporary_path, cache_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return self._remember_tile(cache_key, tile_map)

    def _remember_tile(self, cache_key: tuple[str, int, int], tile_map: Any) -> Any:
        self._memory_cache[cache_key] = tile_map
        self._memory_cache.move_to_end(cache_key)
        while len(self._memory_cache) > OSM_TILE_MEMORY_CACHE_SIZE:
            self._memory_cache.popitem(last=False)
        return tile_map

    def get_combined_map(
        self,
        tiles: tuple[tuple[int, int], ...],
        nx_map_type: Any,
        geofence_type: Any,
        network_type: Any,
        nx: Any,
    ) -> Any:
        if not tiles:
            raise ValueError("Cannot build an OSM map for an empty GPS trace")
        cache_key = (network_type.value, tiles)
        cached = self._combined_cache.get(cache_key)
        if cached is not None:
            self._combined_cache.move_to_end(cache_key)
            return cached
        maps = [self.get_tile_map(tile, nx_map_type, geofence_type, network_type) for tile in tiles]
        road_map = maps[0] if len(maps) == 1 else nx_map_type(nx.compose_all([tile_map.g for tile_map in maps]))
        self._combined_cache[cache_key] = road_map
        self._combined_cache.move_to_end(cache_key)
        while len(self._combined_cache) > 4:
            self._combined_cache.popitem(last=False)
        return road_map

    def log_summary(self) -> None:
        logger.info(
            "OSM tile summary: fetch attempts=%d failures=%d disk-cache hits=%d memory-cache hits=%d",
            self.fetch_attempts,
            self.fetch_failures,
            self.disk_cache_hits,
            self.memory_cache_hits,
        )


def _split_match_chunks(points: list[TrackPoint]) -> list[list[TrackPoint]]:
    chunks: list[list[TrackPoint]] = []
    for gap_chunk in split_track_at_gaps(points):
        start = 0
        while start < len(gap_chunk) - 1:
            end = min(start + MAPPY_TRACE_CHUNK_SIZE, len(gap_chunk))
            chunks.append(gap_chunk[start:end])
            if end == len(gap_chunk):
                break
            start = end - 1
    return chunks


def _coordinates_from_match_result(result: Any, road_map: Any) -> list[Route]:
    matched_routes: list[Route] = []
    if result.path:
        from pyproj import Transformer
        from shapely.geometry import LineString, MultiLineString
        from shapely.ops import transform

        project_to_wgs84 = Transformer.from_crs(road_map.crs, "EPSG:4326", always_xy=True).transform
        current_route: Route = []
        for road in result.path:
            geometry = transform(project_to_wgs84, road.geom)
            lines = geometry.geoms if isinstance(geometry, MultiLineString) else (geometry,)
            for line in lines:
                if not isinstance(line, LineString):
                    continue
                coordinates = [(latitude, longitude) for longitude, latitude in line.coords]
                if current_route and distance_meters(
                    TrackPoint(*current_route[-1]), TrackPoint(*coordinates[0])
                ) <= 5:
                    current_route.extend(coordinates[1:])
                else:
                    if len(current_route) >= 2:
                        matched_routes.append(current_route)
                    current_route = coordinates
        if len(current_route) >= 2:
            matched_routes.append(current_route)
        if matched_routes:
            return matched_routes

    matched_points: Route = []
    if result.matches:
        from pyproj import Transformer

        project_to_wgs84 = Transformer.from_crs(road_map.crs, "EPSG:4326", always_xy=True).transform
        for match in result.matches:
            if match.road is None:
                continue
            snapped_point = match.road.geom.interpolate(match.road.geom.project(match.coordinate.geom))
            longitude, latitude = project_to_wgs84(snapped_point.x, snapped_point.y)
            matched_points.append((latitude, longitude))
    if len(matched_points) >= 2:
        return [matched_points]
    return []


def snap_runs_mappymatch(runs: list[list[TrackPoint]], options: SnapOptions) -> list[Route]:
    Geofence, Trace, NxMap, NetworkType, LCSSMatcher, nx = _mappymatch_components()
    import osmnx as ox

    original_overpass_url = ox.settings.overpass_url
    if options.overpass_url:
        ox.settings.overpass_url = options.overpass_url.rstrip("/")
    map_cache = OSMTileCache(options.osm_cache_dir)
    snapped_runs: list[Route] = []
    seen_road_samples: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}

    try:
        for run in runs:
            for chunk in _split_match_chunks(run):
                tiles = map_cache.tiles_for_trace(chunk)
                network_type = getattr(NetworkType, options.network_type.upper(), None)
                if network_type is None:
                    raise ValueError(f"Unsupported Mappymatch OSM network type: {options.network_type}")
                road_map = map_cache.get_combined_map(tiles, NxMap, Geofence, network_type, nx)
                import pandas as pd

                trace_frame = pd.DataFrame(
                    {
                        "latitude": [point.latitude for point in chunk],
                        "longitude": [point.longitude for point in chunk],
                    },
                    index=[point.timestamp for point in chunk],
                )
                trace = Trace.from_dataframe(trace_frame, xy=True)
                matcher = LCSSMatcher(
                    road_map,
                    distance_epsilon=50,
                    similarity_cutoff=0.85,
                    distance_threshold=MAPPY_MATCH_MAX_DISTANCE_METERS,
                )
                matched_routes = _coordinates_from_match_result(matcher.match_trace(trace), road_map)
                for route in matched_routes:
                    unique_routes = deduplicate_route_edges(route, seen_road_samples)
                    for unique_route in unique_routes:
                        index_route_edges(unique_route, seen_road_samples)
                    snapped_runs.extend(unique_routes)
    finally:
        map_cache.log_summary()
        ox.settings.overpass_url = original_overpass_url
    return snapped_runs