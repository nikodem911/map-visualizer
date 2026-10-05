#!/usr/bin/env python3
"""Route selected Google Timeline activity samples into KML for Google My Maps."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
MAX_SNAP_OFFSET_DEGREES = 1.5e-4
ACTIVITY_TYPES = {
    "car": {"IN_ROAD_VEHICLE", "IN_VEHICLE", "IN_PASSENGER_VEHICLE"},
    "bike": {"ON_BICYCLE", "CYCLING"},
}
ET.register_namespace("", KML_NAMESPACE)


@dataclass(frozen=True)
class TrackPoint:
    latitude: float
    longitude: float
    timestamp: datetime | None = None


def parse_timestamp(value: Any, milliseconds: bool = False) -> datetime | None:
    if value is None:
        return None

    try:
        if milliseconds:
            return datetime.fromtimestamp(float(value) / 1000, timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        normalized = str(value).strip().replace("Z", "+00:00")
        timestamp = datetime.fromisoformat(normalized)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def parse_timestamp_date(value: Any) -> date | None:
    if isinstance(value, str):
        try:
            normalized = value.strip().replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            pass
    timestamp = parse_timestamp(value)
    return timestamp.date() if timestamp is not None else None


def parse_coordinates(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        latitude = value.get("latitude", value.get("lat"))
        longitude = value.get("longitude", value.get("lng", value.get("lon")))
        try:
            return float(latitude), float(longitude)
        except (TypeError, ValueError):
            return None

    if not isinstance(value, str):
        return None

    coordinate_text = value.strip()
    if coordinate_text.startswith("geo:"):
        coordinate_text = coordinate_text[4:]
    numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", coordinate_text)
    if len(numbers) < 2:
        return None
    try:
        return float(numbers[0]), float(numbers[1])
    except ValueError:
        return None


def make_point(
    coordinates: tuple[float, float] | None,
    timestamp: datetime | None = None,
) -> TrackPoint | None:
    if coordinates is None:
        return None
    latitude, longitude = coordinates
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return TrackPoint(latitude, longitude, timestamp)


def input_json_files(inputs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for input_path in inputs:
        if input_path.is_dir():
            files.extend(input_path.rglob("*.json"))
        elif input_path.is_file():
            files.append(input_path)
        else:
            raise FileNotFoundError(f"Input path does not exist: {input_path}")
    return sorted(set(files))


def parse_date_filter(raw_value: str | None) -> tuple[str, str] | None:
    if raw_value is None:
        return None

    value = raw_value.strip()
    if not value:
        raise ValueError("Date filter cannot be empty")

    try:
        if ":" in value:
            start_text, end_text = value.split(":", 1)
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
            if end < start:
                raise ValueError("Date range start must be earlier than or equal to the end date")
        else:
            start = date.fromisoformat(value)
            end = start
    except ValueError as error:
        raise ValueError(
            "Date filter must be YYYY-MM-DD or YYYY-MM-DD:YYYY-MM-DD"
        ) from error

    return start.isoformat(), end.isoformat()


def semantic_activity_runs_from_data(
    data: Any, mode: str, date_filter: tuple[str, str] | None = None
) -> list[list[TrackPoint]]:
    if not isinstance(data, dict) or not isinstance(data.get("semanticSegments"), list):
        return []

    start_date = date.fromisoformat(date_filter[0]) if date_filter is not None else None
    end_date = date.fromisoformat(date_filter[1]) if date_filter is not None else None
    runs: list[list[TrackPoint]] = []
    for segment in data["semanticSegments"]:
        if not isinstance(segment, dict):
            continue
        activity = segment.get("activity")
        candidate = activity.get("topCandidate") if isinstance(activity, dict) else None
        if not isinstance(candidate, dict) or candidate.get("type") not in ACTIVITY_TYPES[mode]:
            continue

        points: list[TrackPoint] = []
        for endpoint, timestamp_key in (("start", "startTime"), ("end", "endTime")):
            raw_timestamp = segment.get(timestamp_key)
            timestamp = parse_timestamp(raw_timestamp)
            calendar_date = parse_timestamp_date(raw_timestamp)
            if timestamp is None or calendar_date is None:
                continue
            if start_date is not None and end_date is not None:
                if not (start_date <= calendar_date <= end_date):
                    continue
            location = activity.get(endpoint)
            coordinates = parse_coordinates(location.get("latLng")) if isinstance(location, dict) else None
            point = make_point(coordinates, timestamp)
            if point is not None:
                points.append(point)

        if len(points) >= 2:
            points.sort(key=lambda point: point.timestamp or datetime.min.replace(tzinfo=timezone.utc))
            runs.append(points)
    return runs


def activity_runs_from_data(data: Any, mode: str, date_filter: tuple[str, str] | None = None) -> list[list[TrackPoint]]:
    if not isinstance(data, dict):
        return []
    raw_signals = data.get("rawSignals", [])
    if not isinstance(raw_signals, list):
        raw_signals = []

    start_date = None
    end_date = None
    if date_filter is not None:
        start_date = date.fromisoformat(date_filter[0])
        end_date = date.fromisoformat(date_filter[1])

    activities: list[tuple[datetime, bool]] = []
    positions: list[tuple[datetime, TrackPoint]] = []
    for signal in raw_signals:
        if not isinstance(signal, dict):
            continue
        activity = signal.get("activityRecord")
        if isinstance(activity, dict):
            raw_timestamp = activity.get("timestamp")
            timestamp = parse_timestamp(raw_timestamp)
            calendar_date = parse_timestamp_date(raw_timestamp)
            if calendar_date is not None and start_date is not None and end_date is not None:
                if not (start_date <= calendar_date <= end_date):
                    continue
            probabilities = activity.get("probableActivities", [])
            if timestamp is not None and probabilities:
                best = max(
                    (item for item in probabilities if isinstance(item, dict)),
                    key=lambda item: float(item.get("confidence", 0) or 0),
                    default={},
                )
                activities.append((timestamp, best.get("type") in ACTIVITY_TYPES[mode]))

        position = signal.get("position")
        if isinstance(position, dict):
            raw_timestamp = position.get("timestamp")
            timestamp = parse_timestamp(raw_timestamp)
            calendar_date = parse_timestamp_date(raw_timestamp)
            if calendar_date is not None and start_date is not None and end_date is not None:
                if not (start_date <= calendar_date <= end_date):
                    continue
            point = make_point(parse_coordinates(position.get("LatLng")), timestamp)
            if timestamp is not None and point is not None:
                positions.append((timestamp, point))

    activities.sort(key=lambda item: item[0])
    positions.sort(key=lambda item: item[0])
    activity_times = [item[0] for item in activities]
    runs: list[list[TrackPoint]] = []
    current_run: list[TrackPoint] = []
    for timestamp, point in positions:
        index = bisect.bisect_right(activity_times, timestamp) - 1
        is_selected = (
            index >= 0
            and (timestamp - activities[index][0]).total_seconds() <= 120
            and activities[index][1]
        )
        if is_selected:
            current_run.append(point)
        elif current_run:
            runs.append(current_run)
            current_run = []
    if current_run:
        runs.append(current_run)
    raw_runs = [run for run in runs if len(run) >= 2]
    return raw_runs if raw_runs else semantic_activity_runs_from_data(data, mode, date_filter)


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


def snap_chunk(points: list[TrackPoint], api_key: str | None = None) -> list[tuple[float, float]]:
    if not points:
        return []
    if len(points) < 3:
        return [(point.latitude, point.longitude) for point in points]

    snapped: list[tuple[float, float]] = [(point.latitude, point.longitude) for point in points]
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


def snap_runs(runs: list[list[TrackPoint]], api_key: str | None = None) -> list[list[tuple[float, float]]]:
    snapped_runs: list[list[tuple[float, float]]] = []
    for run in runs:
        if len(run) < 2:
            continue
        snapped_runs.append(snap_chunk(run, api_key))
    return snapped_runs


def write_kml(routes: Iterable[list[tuple[float, float]]], output_path: Path, track_name: str) -> int:
    namespace = KML_NAMESPACE
    root = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(root, f"{{{namespace}}}Document")
    ET.SubElement(document, f"{{{namespace}}}name").text = track_name
    style = ET.SubElement(document, f"{{{namespace}}}Style", {"id": "route"})
    line_style = ET.SubElement(style, f"{{{namespace}}}LineStyle")
    ET.SubElement(line_style, f"{{{namespace}}}color").text = "ff2878c8"
    ET.SubElement(line_style, f"{{{namespace}}}width").text = "4"

    count = 0
    for index, route in enumerate(routes, start=1):
        if len(route) < 2:
            continue
        placemark = ET.SubElement(document, f"{{{namespace}}}Placemark")
        ET.SubElement(placemark, f"{{{namespace}}}name").text = f"Route {index}"
        ET.SubElement(placemark, f"{{{namespace}}}styleUrl").text = "#route"
        line_string = ET.SubElement(placemark, f"{{{namespace}}}LineString")
        ET.SubElement(line_string, f"{{{namespace}}}tessellate").text = "1"
        ET.SubElement(line_string, f"{{{namespace}}}coordinates").text = " ".join(
            f"{longitude:.6f},{latitude:.6f},0" for latitude, longitude in route
        )
        count += len(route)

    ET.indent(root, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export selected Google Timeline activity samples as KML, optionally snapped to roads."
    )
    parser.add_argument("input", nargs="+", type=Path, help="JSON file(s) or folder(s) containing JSON")
    parser.add_argument("output", type=Path, help="Output KML file for Google My Maps")
    parser.add_argument("--name", default="Location History", help="Name for the map layer")
    parser.add_argument(
        "-d",
        "--date",
        help="Filter records to a single day (YYYY-MM-DD) or day range (YYYY-MM-DD:YYYY-MM-DD)",
    )
    activity_group = parser.add_mutually_exclusive_group(required=True)
    activity_group.add_argument("-c", "--car", action="store_const", const="car", dest="mode", help="Select car/vehicle activity")
    activity_group.add_argument("-b", "--bike", action="store_const", const="bike", dest="mode", help="Select bicycle activity")
    parser.add_argument("-s", "--snap", action="store_true", help="Snap recorded points to the nearest OpenStreetMap road nodes")
    arguments = parser.parse_args()

    try:
        date_filter = parse_date_filter(arguments.date)
        files = input_json_files(arguments.input)
        if not files:
            parser.error("No JSON files found in the supplied input paths")
        runs: list[list[TrackPoint]] = []
        for path in files:
            with path.open(encoding="utf-8") as source:
                runs.extend(activity_runs_from_data(json.load(source), arguments.mode, date_filter=date_filter))
        if not runs:
            label = f"for {date_filter[0]} to {date_filter[1]}" if date_filter else ""
            raise ValueError(f"No {arguments.mode} activity samples with usable GPS positions were found {label}".strip())
        if arguments.snap:
            routes = snap_runs(runs)
        else:
            routes = [
                [(point.latitude, point.longitude) for point in run]
                for run in runs
            ]
        count = write_kml(routes, arguments.output, arguments.name)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {count} KML coordinates to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())