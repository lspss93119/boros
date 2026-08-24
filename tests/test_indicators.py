from datetime import date
from decimal import Decimal

import pytest

from boros_research.market_metadata import CollateralAsset, MarketInfo
from boros_research.indicators import (
    AssetPrice,
    PriceLookupResult,
    materialize_asset_prices,
    price_at_or_before,
    select_reference_market,
)


START = 1787443200


def collateral(token_id, symbol):
    return CollateralAsset(
        token_id=token_id,
        symbol=symbol,
        asset_id=symbol,
        address=f"0x{token_id}",
        name=symbol,
        decimals=18,
        usd_price=Decimal("1") if symbol == "USDT" else None,
    )


def market(market_id, asset):
    return MarketInfo(
        market_id=market_id,
        token_id=3,
        venue="HYPERLIQUID",
        asset=asset,
        maturity=date(2026, 9, 25),
        symbol=f"HYPERLIQUID-{asset}USDT-25SEP2026",
        name=f"{asset} market",
    )


def csv_response(rows):
    lines = ["timestamp,open,high,low,close,volume,asset_price_usd"]
    lines.extend(
        f"{timestamp},0,0,0,0,0,{price}"
        for timestamp, price in rows
    )
    return "\n".join(lines) + "\n"


def test_usdt_collateral_is_one_dollar(tmp_path):
    exporter_calls = []

    def exporter(url, params):
        exporter_calls.append((url, dict(params)))
        raise AssertionError("stable collateral must not fetch indicators")

    rows = materialize_asset_prices(
        {3: collateral(3, "USDT")},
        {},
        START,
        START + 600,
        raw_root=tmp_path,
        exporter=exporter,
    )

    assert [(row.asset, row.timestamp, row.price_usd) for row in rows] == [
        ("USDT", START, 1.0),
        ("USDT", START + 300, 1.0),
        ("USDT", START + 600, 1.0),
    ]
    assert all(row.source_market_id is None for row in rows)
    assert {row.source_path for row in rows} == {"synthetic/stable/USDT"}
    assert exporter_calls == []


def test_token_collateral_uses_reference_asset_price(tmp_path):
    calls = []

    def exporter(url, params):
        calls.append((url, dict(params)))
        return csv_response([(START, 2422.7), (START + 300, 2423.4)])

    rows = materialize_asset_prices(
        {5: collateral(5, "HYPE")},
        {200: market(200, "HYPE"), 155: market(155, "HYPE")},
        START,
        START + 300,
        raw_root=tmp_path,
        exporter=exporter,
        request_delay_sec=0,
    )

    assert [(row.timestamp, row.price_usd, row.source_market_id) for row in rows] == [
        (START, 2422.7, 155),
        (START + 300, 2423.4, 155),
    ]
    assert calls == [
        (
            "https://api-boros.pendle.finance/apis/v1/indicators/export",
            {
                "marketId": 155,
                "timeFrame": "5m",
                "select": "ap",
                "startTimestamp": START,
                "endTimestamp": START + 300,
            },
        )
    ]


def test_reference_market_selection_is_deterministic():
    selected = select_reference_market(
        collateral(5, "HYPE"),
        {200: market(200, "HYPE"), 155: market(155, "HYPE"), 99: market(99, "BTC")},
    )

    assert selected.market_id == 155


def test_missing_price_marks_observation_unpriceable():
    result = price_at_or_before(
        [AssetPrice("HYPE", START + 300, 2423.0, 155, "source.csv")],
        target_timestamp=START,
    )

    assert result == PriceLookupResult(START, None, None, None, "missing")


def test_stale_price_marks_observation_unpriceable():
    result = price_at_or_before(
        [AssetPrice("HYPE", START, 2423.0, 155, "source.csv")],
        target_timestamp=START + 901,
    )

    assert result == PriceLookupResult(
        START + 901,
        START,
        901,
        None,
        "stale",
    )


def test_future_price_is_never_used():
    result = price_at_or_before(
        [AssetPrice("HYPE", START + 1, 2423.0, 155, "source.csv")],
        target_timestamp=START,
    )

    assert result.status == "missing"
    assert result.price_usd is None


def test_price_age_900_is_valid_and_901_is_stale():
    prices = [AssetPrice("HYPE", START, 2423.0, 155, "source.csv")]

    valid = price_at_or_before(prices, target_timestamp=START + 900)
    stale = price_at_or_before(prices, target_timestamp=START + 901)

    assert valid.status == "ok"
    assert valid.price_usd == 2423.0
    assert valid.price_age_sec == 900
    assert stale.status == "stale"
    assert stale.price_usd is None
    assert stale.price_age_sec == 901


def test_export_chunks_do_not_duplicate_boundary_rows(tmp_path):
    calls = []
    end = START + 5 * 300

    def exporter(url, params):
        calls.append(dict(params))
        first = params["startTimestamp"]
        last = params["endTimestamp"]
        return csv_response(
            [(timestamp, 100.0 + timestamp / 1000) for timestamp in range(first, last + 1, 300)]
        )

    rows = materialize_asset_prices(
        {5: collateral(5, "HYPE")},
        {155: market(155, "HYPE")},
        START,
        end,
        raw_root=tmp_path,
        exporter=exporter,
        max_rows_per_request=3,
        request_delay_sec=0,
    )

    assert [row.timestamp for row in rows] == [START + offset for offset in range(0, 1800, 300)]
    assert calls == [
        {
            "marketId": 155,
            "timeFrame": "5m",
            "select": "ap",
            "startTimestamp": START,
            "endTimestamp": START + 600,
        },
        {
            "marketId": 155,
            "timeFrame": "5m",
            "select": "ap",
            "startTimestamp": START + 900,
            "endTimestamp": end,
        },
    ]


def test_cached_indicator_response_is_not_refetched(tmp_path):
    calls = []

    def exporter(url, params):
        calls.append(dict(params))
        return csv_response([(START, 2422.7)])

    kwargs = {
        "raw_root": tmp_path,
        "exporter": exporter,
        "request_delay_sec": 0,
    }
    first = materialize_asset_prices(
        {5: collateral(5, "HYPE")}, {155: market(155, "HYPE")}, START, START, **kwargs
    )
    second = materialize_asset_prices(
        {5: collateral(5, "HYPE")}, {155: market(155, "HYPE")}, START, START, **kwargs
    )

    assert first == second
    assert len(calls) == 1


def test_invalid_export_price_raises_instead_of_becoming_zero(tmp_path):
    def exporter(url, params):
        return csv_response([(START, 0)])

    with pytest.raises(ValueError, match="price"):
        materialize_asset_prices(
            {5: collateral(5, "HYPE")},
            {155: market(155, "HYPE")},
            START,
            START,
            raw_root=tmp_path,
            exporter=exporter,
            request_delay_sec=0,
        )
