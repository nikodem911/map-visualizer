from __future__ import annotations

from collections.abc import Callable

from route_geometry import TrackPoint
from snap_strategies.common import Route, SnapOptions, SnapStats
from snap_strategies.local import snap_runs_local
from snap_strategies.osrm import snap_runs_osrm


SnapStrategy = Callable[[list[list[TrackPoint]], SnapOptions, SnapStats | None], list[Route]]

SNAP_STRATEGIES: dict[str, SnapStrategy] = {
    "local": lambda runs, _options, stats: snap_runs_local(runs, stats),
    "osrm": snap_runs_osrm,
}


def snap_runs(
    runs: list[list[TrackPoint]],
    strategy: str = "local",
    options: SnapOptions | None = None,
    stats: SnapStats | None = None,
) -> list[Route]:
    try:
        snap_strategy = SNAP_STRATEGIES[strategy]
    except KeyError as error:
        available = ", ".join(sorted(SNAP_STRATEGIES))
        raise ValueError(f"Unknown snap strategy '{strategy}'. Available strategies: {available}") from error
    return snap_strategy(runs, options or SnapOptions(), stats)