#!/usr/bin/env python3
"""Route selected Google Timeline activity samples into KML for Google My Maps."""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


GOOGLE_ROADS_URL = "https://roads.googleapis.com/v1/snapToRoads"
KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
ACTIVITY_TYPES = {
    "car": {"IN_ROAD_VEHICLE", "IN_VEHICLE"},
    "bike": {"ON_BICYCLE"},
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


def activity_runs_from_data(data: Any, mode: str) -> list[list[TrackPoint]]:
    if not isinstance(data, dict) or not isinstance(data.get("rawSignals"), list):
        return []

    activities: list[tuple[datetime, bool]] = []
    positions: list[tuple[datetime, TrackPoint]] = []
    for signal in data["rawSignals"]:
        if not isinstance(signal, dict):
            continue
        activity = signal.get("activityRecord")
        if isinstance(activity, dict):
            timestamp = parse_timestamp(activity.get("timestamp"))
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
            timestamp = parse_timestamp(position.get("timestamp"))
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
    return [run for run in runs if len(run) >= 2]


def snap_chunk(points: list[TrackPoint], api_key: str) -> list[tuple[float, float]]:
    query = urllib.parse.urlencode(
        {
            "path": "|".join(f"{point.latitude:.7f},{point.longitude:.7f}" for point in points),
            "interpolate": "true",
            "key": api_key,
        }
    )
    request = urllib.request.Request(f"{GOOGLE_ROADS_URL}?{query}")
    try:
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ValueError(f"Google Roads API error ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"Could not connect to Google Roads API: {error.reason}") from error
    try:
        return [
            (float(point["location"]["latitude"]), float(point["location"]["longitude"]))
            for point in result["snappedPoints"]
        ]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Google Roads API returned no snapped points for an activity interval") from error


def snap_runs(runs: list[list[TrackPoint]], api_key: str) -> list[list[tuple[float, float]]]:
    snapped_runs: list[list[tuple[float, float]]] = []
    for run in runs:
        start = 0
        while start < len(run) - 1:
            end = min(start + 100, len(run))
            snapped_runs.append(snap_chunk(run[start:end], api_key))
            start = end - 1
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
    activity_group = parser.add_mutually_exclusive_group(required=True)
    activity_group.add_argument("-c", "--car", action="store_const", const="car", dest="mode", help="Select car/vehicle activity")
    activity_group.add_argument("-b", "--bike", action="store_const", const="bike", dest="mode", help="Select bicycle activity")
    parser.add_argument("-s", "--snap", action="store_true", help="Snap recorded points to roads using Google Roads API")
    arguments = parser.parse_args()

    try:
        files = input_json_files(arguments.input)
        if not files:
            parser.error("No JSON files found in the supplied input paths")
        runs: list[list[TrackPoint]] = []
        for path in files:
            with path.open(encoding="utf-8") as source:
                runs.extend(activity_runs_from_data(json.load(source), arguments.mode))
        if not runs:
            raise ValueError(f"No {arguments.mode} activity samples with usable GPS positions were found")
        if arguments.snap:
            api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
            if not api_key:
                raise ValueError("Set the GOOGLE_MAPS_API_KEY environment variable to use --snap")
            routes = snap_runs(runs, api_key)
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