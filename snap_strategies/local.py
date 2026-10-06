from __future__ import annotations

import math

from route_geometry import TrackPoint
from snap_strategies.common import (
    Route,
    deduplicate_route_edges,
    index_route_edges,
    split_track_at_gaps,
)


MAX_SNAP_OFFSET_DEGREES = 1.5e-4


def project_point_to_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float]:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return start
    length_sq = dx * dx + dy * dy
    projection = ((x - x1) * dx + (y - y1) * dy) / length_sq
    projection = max(0.0, min(1.0, projection))
    return (x1 + projection * dx, y1 + projection * dy)


def snap_chunk(points: list[TrackPoint]) -> Route:
    if not points:
        return []
    if len(points) < 3:
        return [(point.latitude, point.longitude) for point in points]

    snapped: Route = [(point.latitude, point.longitude) for point in points]
    for index in range(1, len(points) - 1):
        previous = (points[index - 1].latitude, points[index - 1].longitude)
        current = (points[index].latitude, points[index].longitude)
        next_point = (points[index + 1].latitude, points[index + 1].longitude)
        projected = project_point_to_segment(current, previous, next_point)
        dx = projected[0] - current[0]
        dy = projected[1] - current[1]
        if math.hypot(dx, dy) <= MAX_SNAP_OFFSET_DEGREES:
            snapped[index] = projected
    return snapped


def snap_runs_local(runs: list[list[TrackPoint]]) -> list[Route]:
    snapped_runs: list[Route] = []
    seen_road_samples: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for run in runs:
        chunks = split_track_at_gaps(run)
        snapped_chunks = [snap_chunk(chunk) for chunk in chunks]
        unique_chunks: list[Route] = []
        for chunk in snapped_chunks:
            unique_chunks.extend(deduplicate_route_edges(chunk, seen_road_samples))
        for chunk in unique_chunks:
            index_route_edges(chunk, seen_road_samples)
        snapped_runs.extend(unique_chunks)
    return snapped_runs