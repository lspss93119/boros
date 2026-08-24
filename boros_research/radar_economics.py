"""Pure proxy economics and DTE viability calculations for the market radar."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .crossex_client import CrossExResponse


@dataclass(frozen=True)
class ProxyEconomics:
    cost_usd: float
    capital_usd: float
    as_of_timestamp: int
    pair_count: int


@dataclass(frozen=True)
class ViabilityCutoff:
    cutoff_days: int
    reference_p95_spread_apr: float
    gross_profit_at_cutoff_usd: float
    estimated_net_profit_at_cutoff_usd: float
    holding_return_at_cutoff: float
    proxy: ProxyEconomics


def collect_asset_proxy(response: CrossExResponse, asset: str) -> ProxyEconomics | None:
    valid_pairs = [
        pair
        for group in response.groups
        for pair in group.pairs
        if pair.base.upper() == asset.upper()
        and not pair.reasons
        and pair.costs.total_usd is not None
        and math.isfinite(pair.costs.total_usd)
        and pair.costs.total_usd >= 0
        and pair.capital_usd is not None
        and math.isfinite(pair.capital_usd)
        and pair.capital_usd > 0
    ]
    if not valid_pairs:
        return None
    return ProxyEconomics(
        cost_usd=max(pair.costs.total_usd for pair in valid_pairs),
        capital_usd=max(pair.capital_usd for pair in valid_pairs),
        as_of_timestamp=response.as_of_timestamp,
        pair_count=len(valid_pairs),
    )


def _validate_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _validate_proxy(proxy: ProxyEconomics) -> None:
    _validate_finite(proxy.cost_usd, "proxy.cost_usd")
    _validate_finite(proxy.capital_usd, "proxy.capital_usd")
    if proxy.cost_usd < 0:
        raise ValueError("proxy.cost_usd must be >= 0")
    if proxy.capital_usd <= 0:
        raise ValueError("proxy.capital_usd must be > 0")


def required_dte_days(
    *,
    notional_usd: float,
    spread_apr: float,
    proxy: ProxyEconomics,
    min_net_profit_usd: float,
    min_holding_return: float,
) -> int | None:
    _validate_finite(notional_usd, "notional_usd")
    _validate_finite(spread_apr, "spread_apr")
    _validate_finite(min_net_profit_usd, "min_net_profit_usd")
    _validate_finite(min_holding_return, "min_holding_return")
    _validate_proxy(proxy)
    if notional_usd <= 0:
        raise ValueError("notional_usd must be > 0")
    if spread_apr <= 0:
        return None

    profit_days = (
        365 * (proxy.cost_usd + min_net_profit_usd) / (notional_usd * spread_apr)
    )
    return_days = (
        365
        * (proxy.cost_usd + min_holding_return * proxy.capital_usd)
        / (notional_usd * spread_apr)
    )
    return math.ceil(max(profit_days, return_days))


def derive_viability_cutoff(
    *,
    notional_usd: float,
    reference_p95_spread_apr: float,
    proxy: ProxyEconomics,
    min_net_profit_usd: float = 50.0,
    min_holding_return: float = 0.01,
) -> ViabilityCutoff | None:
    _validate_finite(reference_p95_spread_apr, "reference_p95_spread_apr")
    cutoff_days = required_dte_days(
        notional_usd=notional_usd,
        spread_apr=reference_p95_spread_apr,
        proxy=proxy,
        min_net_profit_usd=min_net_profit_usd,
        min_holding_return=min_holding_return,
    )
    if cutoff_days is None:
        return None
    gross_profit = notional_usd * reference_p95_spread_apr * cutoff_days / 365
    estimated_net_profit = gross_profit - proxy.cost_usd
    return ViabilityCutoff(
        cutoff_days=cutoff_days,
        reference_p95_spread_apr=reference_p95_spread_apr,
        gross_profit_at_cutoff_usd=gross_profit,
        estimated_net_profit_at_cutoff_usd=estimated_net_profit,
        holding_return_at_cutoff=estimated_net_profit / proxy.capital_usd,
        proxy=proxy,
    )
