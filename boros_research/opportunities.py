from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .config import CROSSEX_VENUES, NOTIONALS_USD
from .execution import ExecutionResult, NotionalExecution
from .market_metadata import MarketInfo


_VALID_STATUSES = frozenset({"ok", "stale", "missing"})
ContractKey = tuple[str, date, int]


@dataclass(frozen=True)
class OpportunityKey:
    asset: str
    maturity: date
    token_id: int
    short_market_id: int
    short_venue: str
    long_market_id: int
    long_venue: str
    notional_usd: float


@dataclass(frozen=True)
class MarketExecutionObservation:
    market_id: int
    timestamp: int
    book_status: str
    snapshot_age_sec: int | None
    price_status: str
    price_age_sec: int | None
    collateral_price_usd: float | None
    executions: tuple[NotionalExecution, ...] | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.market_id, bool)
            or not isinstance(self.market_id, int)
            or self.market_id <= 0
        ):
            raise ValueError("market_id must be a positive integer")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise ValueError("timestamp must be a Unix timestamp in seconds")
        if self.book_status not in _VALID_STATUSES:
            raise ValueError("book_status must be ok, stale, or missing")
        if self.price_status not in _VALID_STATUSES:
            raise ValueError("price_status must be ok, stale, or missing")
        for value, name in (
            (self.snapshot_age_sec, "snapshot_age_sec"),
            (self.price_age_sec, "price_age_sec"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if self.collateral_price_usd is not None:
            if isinstance(self.collateral_price_usd, bool):
                raise ValueError("collateral_price_usd must be finite and positive")
            try:
                price = float(self.collateral_price_usd)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "collateral_price_usd must be finite and positive"
                ) from exc
            if not math.isfinite(price) or price <= 0:
                raise ValueError("collateral_price_usd must be finite and positive")
        if self.executions is not None:
            executions = tuple(self.executions)
            seen_notionals: set[float] = set()
            for execution in executions:
                if not isinstance(execution, NotionalExecution):
                    raise ValueError("executions must contain NotionalExecution values")
                notional = float(execution.notional_usd)
                if not math.isfinite(notional) or notional <= 0:
                    raise ValueError("execution notional_usd must be finite and positive")
                if notional in seen_notionals:
                    raise ValueError("executions must not repeat a notional")
                seen_notionals.add(notional)
            object.__setattr__(self, "executions", executions)


def _market_values(markets: Mapping[int, MarketInfo] | Iterable[MarketInfo]) -> list[MarketInfo]:
    values = list(markets.values()) if isinstance(markets, Mapping) else list(markets)
    seen_ids: set[int] = set()
    for market in values:
        if not isinstance(market, MarketInfo):
            raise ValueError("market catalog must contain MarketInfo values")
        if market.market_id in seen_ids:
            raise ValueError(f"duplicate market_id: {market.market_id}")
        seen_ids.add(market.market_id)
    return values


def build_market_groups(
    markets: Mapping[int, MarketInfo] | Iterable[MarketInfo],
) -> dict[ContractKey, tuple[MarketInfo, ...]]:
    """Group supported markets by exact asset, maturity, and collateral token."""
    groups: dict[ContractKey, list[MarketInfo]] = defaultdict(list)
    for market in _market_values(markets):
        if market.venue not in CROSSEX_VENUES:
            continue
        groups[(market.asset, market.maturity, market.token_id)].append(market)

    return {
        key: tuple(
            sorted(
                members,
                key=lambda item: (item.market_id, item.venue, item.symbol),
            )
        )
        for key, members in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
        )
    }


def _observation_values(
    observations: Mapping[int, Iterable[MarketExecutionObservation]]
    | Iterable[MarketExecutionObservation],
) -> list[MarketExecutionObservation]:
    if isinstance(observations, Mapping):
        values: list[MarketExecutionObservation] = []
        for market_id, market_observations in observations.items():
            if isinstance(market_observations, MarketExecutionObservation):
                market_observations = (market_observations,)
            try:
                current = list(market_observations)
            except TypeError as exc:
                raise ValueError(
                    "observations must map market IDs to observation iterables"
                ) from exc
            for observation in current:
                if not isinstance(observation, MarketExecutionObservation):
                    raise ValueError(
                        "observations must contain MarketExecutionObservation values"
                    )
                if observation.market_id != market_id:
                    raise ValueError("observation market_id does not match mapping key")
                values.append(observation)
        return values

    values = list(observations)
    if not all(isinstance(item, MarketExecutionObservation) for item in values):
        raise ValueError("observations must contain MarketExecutionObservation values")
    return values


def _observations_by_market(
    observations: Mapping[int, Iterable[MarketExecutionObservation]]
    | Iterable[MarketExecutionObservation],
) -> dict[int, dict[int, MarketExecutionObservation]]:
    grouped: dict[int, dict[int, MarketExecutionObservation]] = defaultdict(dict)
    for observation in _observation_values(observations):
        if observation.timestamp in grouped[observation.market_id]:
            raise ValueError(
                f"duplicate observation for market {observation.market_id} "
                f"at {observation.timestamp}"
            )
        grouped[observation.market_id][observation.timestamp] = observation
    return {market_id: dict(sorted(items.items())) for market_id, items in grouped.items()}


def _missing_observation(market_id: int, timestamp: int) -> MarketExecutionObservation:
    return MarketExecutionObservation(
        market_id=market_id,
        timestamp=timestamp,
        book_status="missing",
        snapshot_age_sec=None,
        price_status="missing",
        price_age_sec=None,
        collateral_price_usd=None,
        executions=None,
    )


def _execution_by_notional(
    observation: MarketExecutionObservation,
) -> dict[float, NotionalExecution]:
    if observation.executions is None:
        return {}
    return {float(execution.notional_usd): execution for execution in observation.executions}


def _execution_for_notional(
    observation: MarketExecutionObservation,
    notional_usd: float,
) -> NotionalExecution | None:
    if observation.book_status != "ok" or observation.price_status != "ok":
        return None
    return _execution_by_notional(observation).get(notional_usd)


def _dte_days(timestamp: int, maturity: date) -> int:
    timestamp_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
    return (maturity - timestamp_date).days


def _directional_row(
    short_market: MarketInfo,
    long_market: MarketInfo,
    short_observation: MarketExecutionObservation,
    long_observation: MarketExecutionObservation,
    notional_usd: float,
) -> dict[str, Any]:
    short_execution = _execution_for_notional(short_observation, notional_usd)
    long_execution = _execution_for_notional(long_observation, notional_usd)
    reasons: list[str] = []

    if short_observation.book_status != "ok":
        reasons.append(f"short_book_{short_observation.book_status}")
    if long_observation.book_status != "ok":
        reasons.append(f"long_book_{long_observation.book_status}")
    if short_observation.price_status != "ok":
        reasons.append(f"short_price_{short_observation.price_status}")
    if long_observation.price_status != "ok":
        reasons.append(f"long_price_{long_observation.price_status}")

    if short_observation.book_status == "ok" and short_observation.price_status == "ok":
        if short_execution is None:
            reasons.append("short_execution_missing")
        elif not short_execution.bid.depth_sufficient:
            reasons.append("short_depth_insufficient")
        elif short_execution.bid.vwap_apr is None:
            reasons.append("short_execution_missing")
    if long_observation.book_status == "ok" and long_observation.price_status == "ok":
        if long_execution is None:
            reasons.append("long_execution_missing")
        elif not long_execution.ask.depth_sufficient:
            reasons.append("long_depth_insufficient")
        elif long_execution.ask.vwap_apr is None:
            reasons.append("long_execution_missing")

    dte_days = _dte_days(short_observation.timestamp, short_market.maturity)
    if dte_days < 0:
        reasons.append("expired")

    short_bid = short_execution.bid if short_execution is not None else None
    long_ask = long_execution.ask if long_execution is not None else None
    short_bid_vwap = short_bid.vwap_apr if short_bid is not None else None
    long_ask_vwap = long_ask.vwap_apr if long_ask is not None else None
    top_spread = None
    if (
        short_bid is not None
        and long_ask is not None
        and short_bid.top_apr is not None
        and long_ask.top_apr is not None
    ):
        top_spread = short_bid.top_apr - long_ask.top_apr

    fully_executable = not reasons
    executable_spread = None
    if fully_executable:
        if short_bid_vwap is None or long_ask_vwap is None:
            raise ValueError("fully executable legs must have VWAP APR values")
        executable_spread = short_bid_vwap - long_ask_vwap

    timestamp = short_observation.timestamp
    timestamp_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return {
        "timestamp": timestamp,
        "asset": short_market.asset,
        "maturity": short_market.maturity,
        "dte_days": dte_days,
        "token_id": short_market.token_id,
        "short_market_id": short_market.market_id,
        "short_venue": short_market.venue,
        "long_market_id": long_market.market_id,
        "long_venue": long_market.venue,
        "notional_usd": notional_usd,
        "short_bid_vwap_apr": short_bid_vwap,
        "long_ask_vwap_apr": long_ask_vwap,
        "executable_spread_apr": executable_spread,
        "short_top_bid_apr": short_bid.top_apr if short_bid is not None else None,
        "long_top_ask_apr": long_ask.top_apr if long_ask is not None else None,
        "top_of_book_spread_apr": top_spread,
        "short_impact_apr": short_bid.impact_apr if short_bid is not None else None,
        "long_impact_apr": long_ask.impact_apr if long_ask is not None else None,
        "short_filled_usd": short_bid.filled_usd if short_bid is not None else None,
        "long_filled_usd": long_ask.filled_usd if long_ask is not None else None,
        "fully_executable": fully_executable,
        "invalid_reason": ";".join(reasons) if reasons else None,
        "short_snapshot_age_sec": short_observation.snapshot_age_sec,
        "long_snapshot_age_sec": long_observation.snapshot_age_sec,
        "short_price_age_sec": short_observation.price_age_sec,
        "long_price_age_sec": long_observation.price_age_sec,
        "year": f"{timestamp_date.year:04d}",
        "month": f"{timestamp_date.month:02d}",
    }


def _notionals(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result:
        raise ValueError("notionals_usd must not be empty")
    if any(not math.isfinite(value) or value <= 0 for value in result):
        raise ValueError("notionals_usd must contain finite positive values")
    if len(set(result)) != len(result):
        raise ValueError("notionals_usd must not contain duplicates")
    return result


def build_executable_opportunities(
    market_catalog: Mapping[int, MarketInfo] | Iterable[MarketInfo],
    observations: Mapping[int, Iterable[MarketExecutionObservation]]
    | Iterable[MarketExecutionObservation],
    notionals_usd: Iterable[float] = NOTIONALS_USD,
) -> tuple[dict[str, Any], ...]:
    """Build deterministic diagnostic rows for every directed venue pair."""
    markets = _market_values(market_catalog)
    market_by_id = {market.market_id: market for market in markets}
    observations_by_market = _observations_by_market(observations)
    unknown_market_ids = set(observations_by_market) - set(market_by_id)
    if unknown_market_ids:
        raise ValueError(f"observations refer to unknown markets: {sorted(unknown_market_ids)}")
    notionals = _notionals(notionals_usd)
    groups = build_market_groups(markets)
    rows: list[dict[str, Any]] = []

    for members in groups.values():
        for index, left_market in enumerate(members):
            for right_market in members[index + 1 :]:
                if left_market.venue == right_market.venue:
                    continue
                left_observations = observations_by_market.get(left_market.market_id, {})
                right_observations = observations_by_market.get(right_market.market_id, {})
                timestamps = sorted(set(left_observations) | set(right_observations))
                for timestamp in timestamps:
                    left_observation = left_observations.get(timestamp)
                    if left_observation is None:
                        left_observation = _missing_observation(left_market.market_id, timestamp)
                    right_observation = right_observations.get(timestamp)
                    if right_observation is None:
                        right_observation = _missing_observation(right_market.market_id, timestamp)
                    for notional_usd in notionals:
                        rows.append(
                            _directional_row(
                                left_market,
                                right_market,
                                left_observation,
                                right_observation,
                                notional_usd,
                            )
                        )
                        rows.append(
                            _directional_row(
                                right_market,
                                left_market,
                                right_observation,
                                left_observation,
                                notional_usd,
                            )
                        )

    rows.sort(
        key=lambda row: (
            row["timestamp"],
            row["asset"],
            row["maturity"],
            row["token_id"],
            row["short_market_id"],
            row["long_market_id"],
            row["notional_usd"],
        )
    )
    return tuple(rows)
