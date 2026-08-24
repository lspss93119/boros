import math

import pytest

from boros_research.crossex_client import (
    CrossExCosts,
    CrossExGroup,
    CrossExLeg,
    CrossExPair,
    CrossExResponse,
)
from boros_research.radar_economics import (
    ProxyEconomics,
    ViabilityCutoff,
    collect_asset_proxy,
    derive_viability_cutoff,
    required_dte_days,
)


def _pair(
    *,
    base="HYPE",
    total_usd=24.0,
    capital_usd=1800.0,
    reasons=(),
):
    leg = CrossExLeg(1, "venue", "venue", "HYPE-PERP", base, 0.1, 0.1)
    costs = CrossExCosts(1, 1, 1, 1, 1, 1, total_usd, 0.1)
    return CrossExPair(
        base,
        leg,
        leg,
        0.1,
        0.1,
        0.0,
        None,
        costs,
        capital_usd,
        0.1,
        0.1,
        1.0,
        10.0,
        100,
        tuple(reasons),
    )


def _response(*pairs):
    group = CrossExGroup(1, "USDC", 1.0, 100, 100, "HYPE", tuple(pairs), ())
    return CrossExResponse(10_000, 123, (group,), ())


def test_collect_asset_proxy_uses_conservative_valid_envelope():
    response = _response(
        _pair(total_usd=24, capital_usd=1800),
        _pair(total_usd=20, capital_usd=1500),
        _pair(base="BTC"),
        _pair(reasons=("warning",)),
        _pair(total_usd=None),
        _pair(total_usd=-1),
        _pair(capital_usd=None),
        _pair(capital_usd=0),
    )

    proxy = collect_asset_proxy(response, "HYPE")

    assert proxy == ProxyEconomics(24.0, 1800.0, 123, 2)


def test_collect_asset_proxy_returns_none_without_valid_pairs():
    response = _response(_pair(reasons=("invalid",)), _pair(total_usd=None))

    assert collect_asset_proxy(response, "HYPE") is None


def test_required_dte_days_uses_ceil_of_profit_and_return_cutoffs():
    proxy = ProxyEconomics(20, 2000, 1, 1)

    assert required_dte_days(
        notional_usd=10_000,
        spread_apr=0.08,
        proxy=proxy,
        min_net_profit_usd=50,
        min_holding_return=0.01,
    ) == 32


@pytest.mark.parametrize(
    ("kwargs", "exception"),
    [
        ({"notional_usd": 0}, ValueError),
        ({"notional_usd": math.inf}, ValueError),
        ({"spread_apr": math.nan}, ValueError),
        ({"proxy": ProxyEconomics(20, 0, 1, 1)}, ValueError),
    ],
)
def test_required_dte_days_rejects_invalid_finite_inputs(kwargs, exception):
    values = {
        "notional_usd": 10_000,
        "spread_apr": 0.08,
        "proxy": ProxyEconomics(20, 2000, 1, 1),
        "min_net_profit_usd": 50,
        "min_holding_return": 0.01,
    }
    values.update(kwargs)

    with pytest.raises(exception):
        required_dte_days(**values)


def test_required_dte_days_returns_none_for_non_positive_spread():
    proxy = ProxyEconomics(20, 2000, 1, 1)

    assert required_dte_days(
        notional_usd=10_000,
        spread_apr=0,
        proxy=proxy,
        min_net_profit_usd=50,
        min_holding_return=0.01,
    ) is None


def test_derive_viability_cutoff_calculates_economics_at_integer_cutoff():
    proxy = ProxyEconomics(20, 2000, 123, 2)

    cutoff = derive_viability_cutoff(
        notional_usd=10_000,
        reference_p95_spread_apr=0.08,
        proxy=proxy,
        min_net_profit_usd=50,
        min_holding_return=0.01,
    )

    assert cutoff == ViabilityCutoff(32, 0.08, 70.13698630136986, 50.13698630136986, 0.02506849315068493, proxy)


def test_derive_viability_cutoff_returns_none_for_non_positive_spread():
    proxy = ProxyEconomics(20, 2000, 1, 1)

    assert derive_viability_cutoff(
        notional_usd=10_000,
        reference_p95_spread_apr=-0.01,
        proxy=proxy,
    ) is None
