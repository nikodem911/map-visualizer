from __future__ import annotations

import json
import logging
import shlex
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from route_geometry import TrackPoint, distance_meters
from snap_strategies.common import Route
from snap_strategies.mappymatch import SnapOptions


OSRM_MATCH_MAX_POINTS = 50
OSRM_MATCH_TIMEOUT_SECONDS = 30
OSRM_MATCH_OVERVIEW = "simplified" # or "full"

logger = logging.getLogger(__name__)


class _OSRMTooBigError(ValueError):
    pass


def _profile_for_options(options: SnapOptions) -> str:
    if options.osrm_profile:
        return options.osrm_profile
    profiles = {"drive": "driving", "bike": "cycling"}
    try:
        return profiles[options.network_type]
    except KeyError as error:
        raise ValueError(f"Unsupported OSRM network type: {options.network_type}") from error


def _timestamp_seconds(points: list[TrackPoint]) -> list[int] | None:
    if any(point.timestamp is None for point in points):
        return None
    timestamps = [int(point.timestamp.timestamp()) for point in points if point.timestamp is not None]
    if any(second <= first for first, second in zip(timestamps, timestamps[1:])):
        return None
    return timestamps


def _match_chunk(points: list[TrackPoint], options: SnapOptions, profile: str) -> list[Route]:
    coordinates = ";".join(f"{point.longitude:.6f},{point.latitude:.6f}" for point in points)
    endpoint = f"{options.osrm_url.rstrip('/')}/match/v1/{quote(profile, safe='')}/{coordinates}"
    query: dict[str, str] = {"geometries": "geojson", "overview": OSRM_MATCH_OVERVIEW, "steps": "false"}
    timestamps = _timestamp_seconds(points)
    if timestamps is not None:
        query["timestamps"] = ";".join(str(timestamp) for timestamp in timestamps)
    request_url = f"{endpoint}?{urlencode(query)}"

    logger.info("OSRM Match request: curl --max-time %d %s", OSRM_MATCH_TIMEOUT_SECONDS, shlex.quote(request_url))
    try:
        with urlopen(request_url, timeout=OSRM_MATCH_TIMEOUT_SECONDS) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            details = json.load(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            details = {}
        message = details.get("message") or details.get("code") or str(error)
        if details.get("code") == "TooBig" or "too many trace coordinates" in message.lower():
            raise _OSRMTooBigError(message) from error
        raise ValueError(f"OSRM Match request failed: {message}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f"OSRM Match request failed: {error}") from error

    if result.get("code") == "TooBig":
        raise _OSRMTooBigError(result.get("message") or "Too many trace coordinates")
    if result.get("code") != "Ok":
        message = result.get("message") or result.get("code") or "unknown response"
        raise ValueError(f"OSRM Match request failed: {message}")

    routes: list[Route] = []
    for matching in result.get("matchings", []):
        geometry = matching.get("geometry", {})
        if geometry.get("type") != "LineString":
            continue
        route = [(float(latitude), float(longitude)) for longitude, latitude in geometry.get("coordinates", [])]
        if len(route) >= 2:
            routes.append(route)
    return routes


def _chunks(points: list[TrackPoint]) -> list[list[TrackPoint]]:
    chunks: list[list[TrackPoint]] = []
    start = 0
    while start < len(points) - 1:
        end = min(start + OSRM_MATCH_MAX_POINTS, len(points))
        chunks.append(points[start:end])
        if end == len(points):
            break
        start = end - 1
    return chunks


def _match_chunk_with_retry(points: list[TrackPoint], options: SnapOptions, profile: str) -> list[Route]:
    try:
        return _match_chunk(points, options, profile)
    except _OSRMTooBigError as error:
        if len(points) <= 2:
            raise ValueError(f"OSRM rejected a two-coordinate Match request: {error}") from error

        midpoint = len(points) // 2
        first_routes = _match_chunk_with_retry(points[: midpoint + 1], options, profile)
        second_routes = _match_chunk_with_retry(points[midpoint:], options, profile)
        if first_routes and second_routes:
            first_end = TrackPoint(*first_routes[-1][-1])
            second_start = TrackPoint(*second_routes[0][0])
            if distance_meters(first_end, second_start) <= 50:
                first_routes[-1].extend(second_routes.pop(0)[1:])
        return first_routes + second_routes
    except ValueError as error:
        logger.error("OSRM Match failed for %d-point request; continuing: %s", len(points), error)
        return []


def snap_runs_osrm(runs: list[list[TrackPoint]], options: SnapOptions) -> list[Route]:
    profile = _profile_for_options(options)
    snapped_runs: list[Route] = []
    for run in runs:
        previous_chunk_had_routes = False
        for chunk in _chunks(run):
            try:
                chunk_routes = _match_chunk_with_retry(chunk, options, profile)
            except ValueError as error:
                logger.error("OSRM Match failed for %d-point chunk; continuing: %s", len(chunk), error)
                previous_chunk_had_routes = False
                continue
            current_chunk_had_routes = bool(chunk_routes)
            if previous_chunk_had_routes and chunk_routes:
                previous_end = TrackPoint(*snapped_runs[-1][-1])
                next_start = TrackPoint(*chunk_routes[0][0])
                if distance_meters(previous_end, next_start) <= 50:
                    snapped_runs[-1].extend(chunk_routes.pop(0)[1:])
            snapped_runs.extend(chunk_routes)
            previous_chunk_had_routes = current_chunk_had_routes
    return snapped_runs