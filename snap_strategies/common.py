from __future__ import annotations

import math

from route_geometry import (
    ACTIVITY_ENDPOINT_TOLERANCE_METERS,
    MAX_ACTIVITY_ENDPOINT_SPEED_METERS_PER_SECOND,
    TrackPoint,
    distance_meters,
)


MAX_TRACK_GAP_SECONDS = 900
ROAD_DEDUP_TOLERANCE_METERS = 35
ROAD_DEDUP_SAMPLE_SPACING_METERS = 10
ROAD_DEDUP_MIN_EDGE_LENGTH_METERS = 25
ROAD_DEDUP_MIN_COVERAGE = 0.7
ROAD_DEDUP_MIN_DIRECTION_DOT = math.cos(math.radians(35))

Route = list[tuple[float, float]]


def route_edge_samples(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float, list[tuple[float, float, float, float]]]:
    latitude1, longitude1 = start
    latitude2, longitude2 = end
    mean_latitude = math.radians((latitude1 + latitude2) / 2)
    x1 = longitude1 * 111_320 * math.cos(mean_latitude)
    y1 = latitude1 * 111_320
    x2 = longitude2 * 111_320 * math.cos(mean_latitude)
    y2 = latitude2 * 111_320
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0, 0.0, []

    direction_x = dx / length
    direction_y = dy / length
    sample_count = max(1, math.ceil(length / ROAD_DEDUP_SAMPLE_SPACING_METERS))
    samples = [
        (
            x1 + dx * (sample_index / sample_count),
            y1 + dy * (sample_index / sample_count),
            direction_x,
            direction_y,
        )
        for sample_index in range(sample_count + 1)
    ]
    return length, direction_x, samples


def road_sample_cell(x: float, y: float) -> tuple[int, int]:
    cell_size = ROAD_DEDUP_TOLERANCE_METERS
    return math.floor(x / cell_size), math.floor(y / cell_size)


def index_route_edges(
    route: Route,
    road_index: dict[tuple[int, int], list[tuple[float, float, float, float]]],
) -> None:
    for start, end in zip(route, route[1:]):
        _, _, samples = route_edge_samples(start, end)
        for sample in samples:
            road_index.setdefault(road_sample_cell(sample[0], sample[1]), []).append(sample)


def route_edge_is_duplicate(
    start: tuple[float, float],
    end: tuple[float, float],
    road_index: dict[tuple[int, int], list[tuple[float, float, float, float]]],
) -> bool:
    length, direction_x, samples = route_edge_samples(start, end)
    if length < ROAD_DEDUP_MIN_EDGE_LENGTH_METERS or not samples:
        return False

    direction_y = samples[0][3]
    nearby_cell_radius = math.ceil(ROAD_DEDUP_TOLERANCE_METERS / ROAD_DEDUP_TOLERANCE_METERS)
    matched_samples = 0
    for x, y, _, _ in samples:
        cell_x, cell_y = road_sample_cell(x, y)
        matched = False
        for offset_x in range(-nearby_cell_radius, nearby_cell_radius + 1):
            for offset_y in range(-nearby_cell_radius, nearby_cell_radius + 1):
                for previous_x, previous_y, previous_direction_x, previous_direction_y in road_index.get(
                    (cell_x + offset_x, cell_y + offset_y), ()
                ):
                    if abs(direction_x * previous_direction_x + direction_y * previous_direction_y) < ROAD_DEDUP_MIN_DIRECTION_DOT:
                        continue
                    if math.hypot(x - previous_x, y - previous_y) <= ROAD_DEDUP_TOLERANCE_METERS:
                        matched = True
                        break
                if matched:
                    break
            if matched:
                break
        matched_samples += matched
    return matched_samples / len(samples) >= ROAD_DEDUP_MIN_COVERAGE


def deduplicate_route_edges(
    route: Route,
    road_index: dict[tuple[int, int], list[tuple[float, float, float, float]]],
) -> list[Route]:
    unique_routes: list[Route] = []
    current_route: Route = []
    for start, end in zip(route, route[1:]):
        if route_edge_is_duplicate(start, end, road_index):
            if len(current_route) >= 2:
                unique_routes.append(current_route)
            current_route = []
            continue
        if current_route and current_route[-1] == start:
            current_route.append(end)
        else:
            if len(current_route) >= 2:
                unique_routes.append(current_route)
            current_route = [start, end]
    if len(current_route) >= 2:
        unique_routes.append(current_route)
    return unique_routes


def split_track_at_gaps(points: list[TrackPoint]) -> list[list[TrackPoint]]:
    if not points:
        return []
    chunks: list[list[TrackPoint]] = []
    current = [points[0]]
    for point in points[1:]:
        previous = current[-1]
        elapsed = (point.timestamp - previous.timestamp).total_seconds() if point.timestamp and previous.timestamp else 0
        maximum_distance = max(
            ACTIVITY_ENDPOINT_TOLERANCE_METERS,
            elapsed * MAX_ACTIVITY_ENDPOINT_SPEED_METERS_PER_SECOND,
        )
        if elapsed > MAX_TRACK_GAP_SECONDS or distance_meters(previous, point) > maximum_distance:
            if len(current) >= 2:
                chunks.append(current)
            current = [point]
        else:
            current.append(point)
    if len(current) >= 2:
        chunks.append(current)
    return chunks