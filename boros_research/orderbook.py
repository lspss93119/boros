from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BookLevel:
    rate_apr: float
    size_collateral: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    timestamp: int
    block_number: int | None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]


def _timestamp(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timestamp must be a Unix timestamp in seconds")
    return value


def _block_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("blockNumber must be an integer or null")
    return value


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_side(value: Any, side: str, *, descending: bool) -> tuple[BookLevel, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{side} side must be an array")

    levels: list[BookLevel] = []
    for index, raw_level in enumerate(value):
        if not isinstance(raw_level, Mapping):
            raise ValueError(f"{side}[{index}] level must be an object")

        rate_apr = _finite_float(raw_level.get("rate"))
        size_collateral = _finite_float(raw_level.get("size"))
        if rate_apr is None or size_collateral is None or size_collateral <= 0:
            continue
        levels.append(
            BookLevel(rate_apr=rate_apr, size_collateral=size_collateral)
        )

    return tuple(sorted(levels, key=lambda level: level.rate_apr, reverse=descending))


def parse_combined_snapshot(raw: Mapping[str, Any]) -> OrderBookSnapshot:
    """Normalize one official Boros combined-book snapshot.

    The wire ``long`` side is the normalized bid side and ``short`` is the
    normalized ask side.  Invalid numeric levels are discarded without
    changing the remaining visible depth.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("combined snapshot must be an object")

    return OrderBookSnapshot(
        timestamp=_timestamp(raw.get("timestamp")),
        block_number=_block_number(raw.get("blockNumber")),
        bids=_parse_side(raw.get("long"), "long", descending=True),
        asks=_parse_side(raw.get("short"), "short", descending=False),
    )
