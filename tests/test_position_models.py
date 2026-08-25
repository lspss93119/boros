from __future__ import annotations

import copy
import math

import pytest

from boros_research.position_models import (
    normalize_positions_response,
    normalize_strategy_response,
)


ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


def strategy_payload() -> dict:
    return {
        "ok": True,
        "data": {
            "strategies": [
                {
                    "strategyId": "strategy-1",
                    "base": "ETH",
                    "maturity": 1_790_000_000,
                    "legs": [
                        {
                            "kind": "boros",
                            "venue": "BOROS",
                            "base": "ETH",
                            "side": "long",
                            "notionalUsd": 10_000,
                            "collateral": "USDC",
                            "notionalToken": "PT-ETH",
                            "marketId": 155,
                            "entryApr": 0.19,
                            "markApr": 0.18,
                            "floatingApr": 0.17,
                            "entryPrice": 0.98,
                            "venueEntry": 0.981,
                            "cashFlowUsd": 100.0,
                            "mtmUsd": 102.0,
                            "tradePnlUsd": 3.0,
                            "feesUsd": 1.0,
                            "netUsd": 2.0,
                            "openedAt": 1_788_000_000,
                            "maturity": 1_790_000_000,
                            "symbol": "PT-ETH",
                            "share": 0.5,
                            "warnings": ["illustrative"],
                        },
                        {
                            "kind": "perp",
                            "venue": "HYPERLIQUID",
                            "base": "ETH",
                            "side": "short",
                            "notionalUsd": 10_000,
                            "marketId": 7,
                            "entryApr": 0.04,
                            "markApr": 0.03,
                            "floatingApr": 0.02,
                            "entryPrice": 2_500.0,
                            "venueEntry": 2_501.0,
                            "cashFlowUsd": -100.0,
                            "mtmUsd": -98.0,
                            "tradePnlUsd": 4.0,
                            "feesUsd": 1.5,
                            "netUsd": 2.5,
                            "openedAt": 1_788_000_000,
                            "maturity": 1_790_000_000,
                            "symbol": "ETH-PERP",
                            "share": 0.5,
                            "warnings": [],
                        },
                    ],
                    "hedge": {"venue": "HYPERLIQUID", "status": "hedged"},
                    "hedgeChecks": {
                        "borosMatchRatio": 1.0,
                        "perpMatchRatio": 0.99,
                        "borosVsPerpRatio": 1.01,
                        "fullyHedged": True,
                    },
                    "capitalUsd": 6_000.0,
                    "capitalSplit": {"boros": 3_000.0, "perp": 3_000.0},
                    "realizedPnlUsd": 12.0,
                    "realizedApr": 0.11,
                    "spread": 0.15,
                    "lockedAprOnCapital": 0.22,
                    "expectedPnlToMaturityUsd": 132.0,
                    "secondsToMaturity": 31 * 86400,
                    "notionalMismatchUsd": 100.0,
                    "attribution": {
                        "source": "crossex",
                        "confidence": 0.95,
                        "pinned": True,
                        "unclaimed": False,
                    },
                    "warnings": ["diagnostic only"],
                }
            ]
        },
        "meta": {"asOfSec": 1_788_100_000},
    }


def positions_payload() -> dict:
    return {
        "ok": True,
        "data": {
            "exposureGroups": [
                {
                    "groupId": "exposure-1",
                    "base": "ETH",
                    "netNotionalUsd": 10_000,
                    "venues": ["HYPERLIQUID"],
                    "legs": [{"side": "short", "notionalUsd": 10_000}],
                }
            ]
        },
        "meta": {"asOfSec": 1_788_100_001},
    }


def test_strategy_normalizer_preserves_strategy_and_leg_fields():
    result = normalize_strategy_response(strategy_payload())

    assert len(result) == 1
    strategy = result[0]
    assert strategy.strategy_id == "strategy-1"
    assert strategy.maturity == 1_790_000_000
    assert strategy.hedge_checks.fully_hedged is True
    assert strategy.hedge_checks.perp_match_ratio == pytest.approx(0.99)
    assert strategy.capital_split["boros"] == pytest.approx(3_000.0)
    assert strategy.attribution.pinned is True
    assert strategy.legs[0].market_id == 155
    assert strategy.legs[0].entry_apr == pytest.approx(0.19)
    assert strategy.legs[1].symbol == "ETH-PERP"
    assert strategy.legs[1].fees_usd == pytest.approx(1.5)


def test_strategy_normalizer_rejects_duplicate_strategy_ids():
    payload = strategy_payload()
    payload["data"]["strategies"].append(copy.deepcopy(payload["data"]["strategies"][0]))

    with pytest.raises(ValueError, match="duplicate strategyId"):
        normalize_strategy_response(payload)


@pytest.mark.parametrize("field", ["lockedAprOnCapital", "expectedPnlToMaturityUsd"])
def test_strategy_normalizer_rejects_non_finite_critical_numbers(field: str):
    payload = strategy_payload()
    payload["data"]["strategies"][0][field] = math.nan

    with pytest.raises(ValueError, match="finite"):
        normalize_strategy_response(payload)


def test_positions_normalizer_preserves_server_exposure_groups():
    result = normalize_positions_response(positions_payload())

    assert result.as_of_timestamp == 1_788_100_001
    assert len(result.exposure_groups) == 1
    assert result.exposure_groups[0]["groupId"] == "exposure-1"
    assert result.exposure_groups[0]["netNotionalUsd"] == 10_000


def test_malformed_strategy_envelope_fails_closed():
    payload = strategy_payload()
    del payload["data"]["strategies"]

    with pytest.raises(ValueError, match="strategies"):
        normalize_strategy_response(payload)
