from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .config import NOTIONALS_USD
from .orderbook import BookLevel, OrderBookSnapshot


@dataclass(frozen=True)
class ExecutionResult:
    target_usd: float
    filled_usd: float
    depth_sufficient: bool
    vwap_apr: float | None
    top_apr: float | None
    impact_apr: float | None
    levels_used: int


@dataclass(frozen=True)
class NotionalExecution:
    notional_usd: float
    bid: ExecutionResult
    ask: ExecutionResult


def _positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and greater than zero")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and greater than zero") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return parsed


def _finite_level_field(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"BookLevel {name} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"BookLevel {name} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"BookLevel {name} must be finite")
    return parsed


def _validated_levels(
    levels: Iterable[BookLevel], side: str
) -> tuple[tuple[float, float], ...]:
    try:
        raw_levels = tuple(levels)
    except TypeError as exc:
        raise ValueError("levels must be an iterable of BookLevel") from exc

    validated: list[tuple[float, float]] = []
    for level in raw_levels:
        if not isinstance(level, BookLevel):
            raise ValueError("levels must contain BookLevel instances")
        rate_apr = _finite_level_field(level.rate_apr, "rate_apr")
        size_collateral = _finite_level_field(
            level.size_collateral, "size_collateral"
        )
        if size_collateral <= 0:
            raise ValueError("BookLevel size_collateral must be greater than zero")
        validated.append((rate_apr, size_collateral))

    for previous, current in zip(validated, validated[1:]):
        if side == "bid" and previous[0] < current[0]:
            raise ValueError("bid levels must be in descending APR order")
        if side == "ask" and previous[0] > current[0]:
            raise ValueError("ask levels must be in ascending APR order")

    return tuple(validated)


def walk_book(
    levels: Iterable[BookLevel],
    collateral_price_usd: float,
    target_usd: float,
    *,
    side: str,
) -> ExecutionResult:
    """Walk canonical bid or ask levels for one USD notional."""
    if side not in {"bid", "ask"}:
        raise ValueError("side must be 'bid' or 'ask'")

    price = _positive_finite(collateral_price_usd, "collateral_price_usd")
    target = _positive_finite(target_usd, "target_usd")
    validated = _validated_levels(levels, side)

    if not validated:
        return ExecutionResult(
            target_usd=target,
            filled_usd=0.0,
            depth_sufficient=False,
            vwap_apr=None,
            top_apr=None,
            impact_apr=None,
            levels_used=0,
        )

    top_apr = validated[0][0]
    remaining_usd = target
    filled_usd = 0.0
    weighted_apr = 0.0
    levels_used = 0

    for rate_apr, size_collateral in validated:
        level_usd = size_collateral * price
        if not math.isfinite(level_usd) or level_usd <= 0:
            raise ValueError("BookLevel USD depth must be finite and greater than zero")

        if level_usd >= remaining_usd:
            consumed_usd = remaining_usd
            weighted_apr += consumed_usd * rate_apr
            filled_usd = target
            remaining_usd = 0.0
            levels_used += 1
            break

        consumed_usd = level_usd
        filled_usd += consumed_usd
        weighted_apr += consumed_usd * rate_apr
        remaining_usd -= consumed_usd
        levels_used += 1

    if remaining_usd > 0.0:
        return ExecutionResult(
            target_usd=target,
            filled_usd=filled_usd,
            depth_sufficient=False,
            vwap_apr=None,
            top_apr=top_apr,
            impact_apr=None,
            levels_used=levels_used,
        )

    vwap_apr = weighted_apr / target
    if side == "bid":
        impact_apr = top_apr - vwap_apr
    else:
        impact_apr = vwap_apr - top_apr

    return ExecutionResult(
        target_usd=target,
        filled_usd=filled_usd,
        depth_sufficient=True,
        vwap_apr=vwap_apr,
        top_apr=top_apr,
        impact_apr=impact_apr,
        levels_used=levels_used,
    )


def simulate_notional_ladder(
    book: OrderBookSnapshot,
    collateral_price_usd: float,
    notionals_usd: Iterable[float] = NOTIONALS_USD,
) -> tuple[NotionalExecution, ...]:
    """Calculate bid and ask execution results for each configured notional."""
    if not hasattr(book, "bids") or not hasattr(book, "asks"):
        raise ValueError("book must provide canonical bids and asks")

    results: list[NotionalExecution] = []
    for notional in notionals_usd:
        target = _positive_finite(notional, "notional_usd")
        results.append(
            NotionalExecution(
                notional_usd=target,
                bid=walk_book(
                    book.bids,
                    collateral_price_usd,
                    target,
                    side="bid",
                ),
                ask=walk_book(
                    book.asks,
                    collateral_price_usd,
                    target,
                    side="ask",
                ),
            )
        )
    return tuple(results)
