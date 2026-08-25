"""Immutable, fail-closed models for the CrossEx position-monitor routes."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias


JsonValue: TypeAlias = str | int | float | bool | None | tuple[Any, ...] | Mapping[str, Any]


def _required_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _required_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, context)


def _required_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _optional_bool(value: Any, context: str) -> bool | None:
    if value is None:
        return None
    return _required_bool(value, context)


def _required_int(value: Any, context: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} must be >= {minimum}")
    return value


def _optional_int(value: Any, context: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _required_int(value, context, minimum=minimum)


def _optional_number(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a finite number or null")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must be a finite number or null") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{context} must be a finite number or null")
    return parsed


def _string_tuple(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a string list")
    return tuple(_required_string(item, f"{context}[]") for item in value)


def _freeze(value: Any, context: str) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} must contain finite numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{context} keys must be strings")
            frozen[key] = _freeze(item, f"{context}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze(item, f"{context}[]") for item in value)
    raise ValueError(f"{context} must contain JSON values")


def _optional_frozen(value: Any, context: str) -> JsonValue | None:
    if value is None:
        return None
    return _freeze(value, context)


@dataclass(frozen=True)
class PositionLeg:
    kind: str
    venue: str
    base: str
    side: str
    notional_usd: float | None
    collateral: str | None
    notional_token: str | None
    market_id: int | None
    entry_apr: float | None
    mark_apr: float | None
    floating_apr: float | None
    entry_price: float | None
    venue_entry: float | None
    cash_flow_usd: float | None
    mtm_usd: float | None
    trade_pnl_usd: float | None
    fees_usd: float | None
    net_usd: float | None
    opened_at: int | None
    maturity: int | None
    symbol: str | None
    share: float | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class HedgeChecks:
    boros_match_ratio: float | None
    perp_match_ratio: float | None
    boros_vs_perp_ratio: float | None
    fully_hedged: bool


@dataclass(frozen=True)
class Attribution:
    source: str | None
    confidence: float | None
    pinned: bool | None
    unclaimed: bool | None


@dataclass(frozen=True)
class StrategySnapshot:
    strategy_id: str
    base: str
    maturity: int
    legs: tuple[PositionLeg, ...]
    hedge: JsonValue | None
    hedge_checks: HedgeChecks
    capital_usd: float | None
    capital_split: JsonValue | None
    realized_pnl_usd: float | None
    realized_apr: float | None
    spread: float | None
    locked_apr_on_capital: float | None
    expected_pnl_to_maturity_usd: float | None
    seconds_to_maturity: int
    notional_mismatch_usd: float | None
    attribution: Attribution
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PositionsSnapshot:
    exposure_groups: tuple[Mapping[str, JsonValue], ...]
    as_of_timestamp: int
    warnings: tuple[str, ...]


def _normalize_leg(value: Any, context: str) -> PositionLeg:
    raw = _required_mapping(value, context)
    return PositionLeg(
        kind=_required_string(raw.get("kind"), f"{context}.kind"),
        venue=_required_string(raw.get("venue"), f"{context}.venue"),
        base=_required_string(raw.get("base"), f"{context}.base"),
        side=_required_string(raw.get("side"), f"{context}.side"),
        notional_usd=_optional_number(raw.get("notionalUsd"), f"{context}.notionalUsd"),
        collateral=_optional_string(raw.get("collateral"), f"{context}.collateral"),
        notional_token=_optional_string(raw.get("notionalToken"), f"{context}.notionalToken"),
        market_id=_optional_int(raw.get("marketId"), f"{context}.marketId", minimum=1),
        entry_apr=_optional_number(raw.get("entryApr"), f"{context}.entryApr"),
        mark_apr=_optional_number(raw.get("markApr"), f"{context}.markApr"),
        floating_apr=_optional_number(raw.get("floatingApr"), f"{context}.floatingApr"),
        entry_price=_optional_number(raw.get("entryPrice"), f"{context}.entryPrice"),
        venue_entry=_optional_number(raw.get("venueEntry"), f"{context}.venueEntry"),
        cash_flow_usd=_optional_number(raw.get("cashFlowUsd"), f"{context}.cashFlowUsd"),
        mtm_usd=_optional_number(raw.get("mtmUsd"), f"{context}.mtmUsd"),
        trade_pnl_usd=_optional_number(raw.get("tradePnlUsd"), f"{context}.tradePnlUsd"),
        fees_usd=_optional_number(raw.get("feesUsd"), f"{context}.feesUsd"),
        net_usd=_optional_number(raw.get("netUsd"), f"{context}.netUsd"),
        opened_at=_optional_int(raw.get("openedAt"), f"{context}.openedAt", minimum=1),
        maturity=_optional_int(raw.get("maturity"), f"{context}.maturity", minimum=1),
        symbol=_optional_string(raw.get("symbol"), f"{context}.symbol"),
        share=_optional_number(raw.get("share"), f"{context}.share"),
        warnings=_string_tuple(raw.get("warnings"), f"{context}.warnings"),
    )


def _normalize_hedge_checks(value: Any, context: str) -> HedgeChecks:
    raw = _required_mapping(value, context)
    return HedgeChecks(
        boros_match_ratio=_optional_number(
            raw.get("borosMatchRatio"), f"{context}.borosMatchRatio"
        ),
        perp_match_ratio=_optional_number(
            raw.get("perpMatchRatio"), f"{context}.perpMatchRatio"
        ),
        boros_vs_perp_ratio=_optional_number(
            raw.get("borosVsPerpRatio"), f"{context}.borosVsPerpRatio"
        ),
        fully_hedged=_required_bool(raw.get("fullyHedged"), f"{context}.fullyHedged"),
    )


def _normalize_attribution(value: Any, context: str) -> Attribution:
    raw = _required_mapping(value, context)
    return Attribution(
        source=_optional_string(raw.get("source"), f"{context}.source"),
        confidence=_optional_number(raw.get("confidence"), f"{context}.confidence"),
        pinned=_optional_bool(raw.get("pinned"), f"{context}.pinned"),
        unclaimed=_optional_bool(raw.get("unclaimed"), f"{context}.unclaimed"),
    )


def _normalize_strategy(value: Any, context: str) -> StrategySnapshot:
    raw = _required_mapping(value, context)
    legs = raw.get("legs")
    if not isinstance(legs, list):
        raise ValueError(f"{context}.legs must be a list")
    return StrategySnapshot(
        strategy_id=_required_string(raw.get("strategyId"), f"{context}.strategyId"),
        base=_required_string(raw.get("base"), f"{context}.base"),
        maturity=_required_int(raw.get("maturity"), f"{context}.maturity", minimum=1),
        legs=tuple(_normalize_leg(item, f"{context}.legs[{index}]") for index, item in enumerate(legs)),
        hedge=_optional_frozen(raw.get("hedge"), f"{context}.hedge"),
        hedge_checks=_normalize_hedge_checks(raw.get("hedgeChecks"), f"{context}.hedgeChecks"),
        capital_usd=_optional_number(raw.get("capitalUsd"), f"{context}.capitalUsd"),
        capital_split=_optional_frozen(raw.get("capitalSplit"), f"{context}.capitalSplit"),
        realized_pnl_usd=_optional_number(
            raw.get("realizedPnlUsd"), f"{context}.realizedPnlUsd"
        ),
        realized_apr=_optional_number(raw.get("realizedApr"), f"{context}.realizedApr"),
        spread=_optional_number(raw.get("spread"), f"{context}.spread"),
        locked_apr_on_capital=_optional_number(
            raw.get("lockedAprOnCapital"), f"{context}.lockedAprOnCapital"
        ),
        expected_pnl_to_maturity_usd=_optional_number(
            raw.get("expectedPnlToMaturityUsd"), f"{context}.expectedPnlToMaturityUsd"
        ),
        seconds_to_maturity=_required_int(
            raw.get("secondsToMaturity"), f"{context}.secondsToMaturity"
        ),
        notional_mismatch_usd=_optional_number(
            raw.get("notionalMismatchUsd"), f"{context}.notionalMismatchUsd"
        ),
        attribution=_normalize_attribution(raw.get("attribution"), f"{context}.attribution"),
        warnings=_string_tuple(raw.get("warnings"), f"{context}.warnings"),
    )


def _envelope(value: Any, context: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    envelope = _required_mapping(value, context)
    if envelope.get("ok") is not True:
        raise ValueError(f"{context}.ok must be true")
    data = _required_mapping(envelope.get("data"), f"{context}.data")
    meta = _required_mapping(envelope.get("meta"), f"{context}.meta")
    return data, meta


def normalize_strategy_response(value: Any) -> tuple[StrategySnapshot, ...]:
    """Normalize a strict CrossEx strategy envelope."""
    data, _meta = _envelope(value, "CrossEx strategy response")
    raw_strategies = data.get("strategies")
    if not isinstance(raw_strategies, list):
        raise ValueError("CrossEx strategy response.data.strategies must be a list")
    result = tuple(
        _normalize_strategy(item, f"CrossEx strategy response.data.strategies[{index}]")
        for index, item in enumerate(raw_strategies)
    )
    strategy_ids = [item.strategy_id for item in result]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("duplicate strategyId in CrossEx strategy response")
    return result


def normalize_positions_response(value: Any) -> PositionsSnapshot:
    """Normalize server-computed position exposure groups."""
    data, meta = _envelope(value, "CrossEx positions response")
    groups = data.get("exposureGroups")
    if not isinstance(groups, list):
        raise ValueError("CrossEx positions response.data.exposureGroups must be a list")
    frozen_groups: list[Mapping[str, JsonValue]] = []
    for index, group in enumerate(groups):
        raw_group = _required_mapping(group, f"CrossEx positions response.data.exposureGroups[{index}]")
        frozen = _freeze(raw_group, f"CrossEx positions response.data.exposureGroups[{index}]")
        if not isinstance(frozen, Mapping):
            raise ValueError("position exposure group must be an object")
        frozen_groups.append(frozen)
    as_of_timestamp = _required_int(meta.get("asOfSec"), "CrossEx positions response.meta.asOfSec", minimum=1)
    return PositionsSnapshot(
        exposure_groups=tuple(frozen_groups),
        as_of_timestamp=as_of_timestamp,
        warnings=_string_tuple(data.get("warnings"), "CrossEx positions response.data.warnings"),
    )
