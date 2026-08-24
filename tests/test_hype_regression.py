import json
from datetime import date
from pathlib import Path

import pytest

from boros_research.execution import simulate_notional_ladder
from boros_research.market_metadata import MarketInfo
from boros_research.opportunities import (
    MarketExecutionObservation,
    build_executable_opportunities,
)
from boros_research.orderbook import parse_combined_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "hype_hl_bybit_market_data.json"
TIMESTAMP = 1787450400
MATURITY = date(2026, 9, 25)


def _markets():
    return (
        MarketInfo(
            market_id=155,
            token_id=3,
            venue="HYPERLIQUID",
            asset="HYPE",
            maturity=MATURITY,
            symbol="HYPERLIQUID-HYPEUSDT-25SEP2026",
            name="Hyperliquid HYPE 25 Sep 2026",
        ),
        MarketInfo(
            market_id=201,
            token_id=3,
            venue="BYBIT",
            asset="HYPE",
            maturity=MATURITY,
            symbol="BYBIT-HYPEUSDT-25SEP2026",
            name="Bybit HYPE 25 Sep 2026",
        ),
    )


def _observation(market_id, *, top_bid, top_ask, executions=None, price_status="missing"):
    return MarketExecutionObservation(
        market_id=market_id,
        timestamp=TIMESTAMP,
        book_status="ok",
        snapshot_age_sec=0,
        price_status=price_status,
        price_age_sec=None if price_status != "ok" else 0,
        collateral_price_usd=None if price_status != "ok" else 1.0,
        executions=executions,
        top_bid_apr=top_bid,
        top_ask_apr=top_ask,
    )


def _row(rows, short_venue, long_venue, notional):
    return next(
        row
        for row in rows
        if row["short_venue"] == short_venue
        and row["long_venue"] == long_venue
        and row["notional_usd"] == notional
    )


def test_hype_hl_bybit_direction_regression():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    hyperliquid = fixture["hyperliquid"]
    bybit = fixture["bybit"]
    markets = _markets()
    rows = build_executable_opportunities(
        markets,
        [
            _observation(
                155,
                top_bid=hyperliquid["bestBid"],
                top_ask=hyperliquid["bestAsk"],
            ),
            _observation(
                201,
                top_bid=bybit["bestBid"],
                top_ask=bybit["bestAsk"],
            ),
        ],
        notionals_usd=(1_000,),
    )

    forward = _row(rows, "HYPERLIQUID", "BYBIT", 1_000)
    reverse = _row(rows, "BYBIT", "HYPERLIQUID", 1_000)

    assert forward["short_market_id"] == 155
    assert forward["long_market_id"] == 201
    assert forward["top_of_book_spread_apr"] == pytest.approx(
        0.2111480263303385
    )
    assert reverse["top_of_book_spread_apr"] == pytest.approx(
        -0.21801886711611435
    )
    assert reverse["top_of_book_spread_apr"] != pytest.approx(
        -forward["top_of_book_spread_apr"]
    )


def test_hype_combined_book_to_directed_vwap_regression():
    hyperliquid_book = parse_combined_snapshot(
        {
            "timestamp": TIMESTAMP,
            "blockNumber": 1,
            "long": [{"rate": 0.109, "size": 2_000}],
            "short": [{"rate": 0.115, "size": 2_000}],
        }
    )
    bybit_book = parse_combined_snapshot(
        {
            "timestamp": TIMESTAMP,
            "blockNumber": 2,
            "long": [{"rate": 0.058, "size": 2_000}],
            "short": [{"rate": 0.062, "size": 2_000}],
        }
    )
    hyperliquid_execution = simulate_notional_ladder(
        hyperliquid_book, 1.0, notionals_usd=(2_000,)
    )
    bybit_execution = simulate_notional_ladder(
        bybit_book, 1.0, notionals_usd=(2_000,)
    )
    rows = build_executable_opportunities(
        _markets(),
        [
            _observation(
                155,
                top_bid=hyperliquid_book.bids[0].rate_apr,
                top_ask=hyperliquid_book.asks[0].rate_apr,
                executions=hyperliquid_execution,
                price_status="ok",
            ),
            _observation(
                201,
                top_bid=bybit_book.bids[0].rate_apr,
                top_ask=bybit_book.asks[0].rate_apr,
                executions=bybit_execution,
                price_status="ok",
            ),
        ],
        notionals_usd=(2_000,),
    )

    forward = _row(rows, "HYPERLIQUID", "BYBIT", 2_000)
    reverse = _row(rows, "BYBIT", "HYPERLIQUID", 2_000)

    assert forward["short_bid_vwap_apr"] == pytest.approx(0.109)
    assert forward["long_ask_vwap_apr"] == pytest.approx(0.062)
    assert forward["executable_spread_apr"] == pytest.approx(0.047)
    assert forward["fully_executable"] is True
    assert reverse["short_bid_vwap_apr"] == pytest.approx(0.058)
    assert reverse["long_ask_vwap_apr"] == pytest.approx(0.115)
    assert reverse["executable_spread_apr"] == pytest.approx(-0.057)
    assert reverse["executable_spread_apr"] != pytest.approx(
        -forward["executable_spread_apr"]
    )
