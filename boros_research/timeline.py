from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from .config import MAX_SNAPSHOT_AGE_SEC, SAMPLE_INTERVAL_SEC
from .orderbook import BookLevel, OrderBookSnapshot


@dataclass(frozen=True)
class AlignedBookObservation:
    grid_timestamp: int
    snapshot_timestamp: int | None
    snapshot_age_sec: int | None
    block_number: int | None
    status: str
    bids: tuple[BookLevel, ...] | None
    asks: tuple[BookLevel, ...] | None


def _require_timestamp(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a Unix timestamp in seconds")
    return value


def _grid_points(start_timestamp: int, end_timestamp: int, interval_sec: int):
    first = ((start_timestamp + interval_sec - 1) // interval_sec) * interval_sec
    last = (end_timestamp // interval_sec) * interval_sec
    if first > last:
        return range(0)
    return range(first, last + 1, interval_sec)


def align_snapshots_to_grid(
    snapshots: Iterable[OrderBookSnapshot],
    start_timestamp: int,
    end_timestamp: int,
    *,
    interval_sec: int = SAMPLE_INTERVAL_SEC,
    max_snapshot_age_sec: int = MAX_SNAPSHOT_AGE_SEC,
) -> tuple[AlignedBookObservation, ...]:
    """Align snapshots to a UTC fixed grid using prior snapshots only."""
    start_timestamp = _require_timestamp(start_timestamp, "start_timestamp")
    end_timestamp = _require_timestamp(end_timestamp, "end_timestamp")
    if start_timestamp > end_timestamp:
        raise ValueError("start_timestamp must not be after end_timestamp")
    if isinstance(interval_sec, bool) or not isinstance(interval_sec, int) or interval_sec <= 0:
        raise ValueError("interval_sec must be a positive integer")
    if (
        isinstance(max_snapshot_age_sec, bool)
        or not isinstance(max_snapshot_age_sec, int)
        or max_snapshot_age_sec < 0
    ):
        raise ValueError("max_snapshot_age_sec must be a non-negative integer")

    ordered = sorted(snapshots, key=lambda snapshot: snapshot.timestamp)
    timestamps = [snapshot.timestamp for snapshot in ordered]
    observations: list[AlignedBookObservation] = []
    for grid_timestamp in _grid_points(start_timestamp, end_timestamp, interval_sec):
        prior_index = bisect_right(timestamps, grid_timestamp)
        if prior_index == 0:
            observations.append(
                AlignedBookObservation(
                    grid_timestamp=grid_timestamp,
                    snapshot_timestamp=None,
                    snapshot_age_sec=None,
                    block_number=None,
                    status="missing",
                    bids=None,
                    asks=None,
                )
            )
            continue

        snapshot = ordered[prior_index - 1]
        snapshot_age_sec = grid_timestamp - snapshot.timestamp
        if snapshot_age_sec <= max_snapshot_age_sec:
            observations.append(
                AlignedBookObservation(
                    grid_timestamp=grid_timestamp,
                    snapshot_timestamp=snapshot.timestamp,
                    snapshot_age_sec=snapshot_age_sec,
                    block_number=snapshot.block_number,
                    status="ok",
                    bids=snapshot.bids,
                    asks=snapshot.asks,
                )
            )
        else:
            observations.append(
                AlignedBookObservation(
                    grid_timestamp=grid_timestamp,
                    snapshot_timestamp=snapshot.timestamp,
                    snapshot_age_sec=snapshot_age_sec,
                    block_number=snapshot.block_number,
                    status="stale",
                    bids=None,
                    asks=None,
                )
            )
    return tuple(observations)
