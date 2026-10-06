from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


MAX_ACTIVITY_ENDPOINT_SPEED_METERS_PER_SECOND = 70
ACTIVITY_ENDPOINT_TOLERANCE_METERS = 1000


@dataclass(frozen=True)
class TrackPoint:
    latitude: float
    longitude: float
    timestamp: datetime | None = None


def distance_meters(first: TrackPoint, second: TrackPoint) -> float:
    latitude1 = math.radians(first.latitude)
    latitude2 = math.radians(second.latitude)
    latitude_delta = latitude2 - latitude1
    longitude_delta = math.radians(second.longitude - first.longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude1) * math.cos(latitude2) * math.sin(longitude_delta / 2) ** 2
    )
    return 12_742_000 * math.asin(math.sqrt(haversine))