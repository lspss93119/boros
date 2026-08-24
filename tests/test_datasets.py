import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from boros_research.datasets import (
    iter_ndjson_zip,
    normalize_assets,
    normalize_funding_rates,
    normalize_market_data,
    normalize_markets,
    normalize_ohlcv,
    normalize_settlements,
)
from boros_research.market_metadata import CollateralAsset, MarketInfo


def write_ndjson_zip(root, relative_path, members):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return path


def test_iter_ndjson_zip_reads_members_in_deterministic_order(tmp_path):
    path = write_ndjson_zip(
        tmp_path,
        "market-data/sample/2026-08.ndjson.zip",
        [
            ("z.ndjson", '{"member": "z"}\n\n'),
            ("folder/", ""),
            ("a.ndjson", '\n{"member": "a"}\n'),
        ],
    )

    assert list(iter_ndjson_zip(path)) == [{"member": "a"}, {"member": "z"}]


def test_market_data_keeps_apr_fraction_epoch_identity_and_nullable_fields(tmp_path):
    relative = "market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip"
    record = {
        "timestamp": 1700000000,
        "blockNumber": 7,
        "midApr": 0.05,
        "bestBid": None,
        "bestAsk": 0.06,
        "ammImpliedApr": None,
        "markApr": 0.04,
        "notionalOI": None,
        "lastTradedApr": 0.055,
        "latestSettlementApr": None,
    }
    path = write_ndjson_zip(tmp_path, relative, [("rows.ndjson", json.dumps(record))])

    row = list(normalize_market_data(path, raw_root=tmp_path))[0]

    assert row["timestamp"] == 1700000000
    assert row["mid_apr"] == 0.05
    assert row["market_id"] == 155
    assert row["venue"] == "HYPERLIQUID"
    assert row["asset"] == "HYPE"
    assert row["maturity"] == date(2026, 9, 25)
    assert row["best_bid_apr"] is None
    assert row["notional_oi_collateral"] is None
    assert row["source_path"] == relative


def test_current_and_legacy_funding_paths_share_canonical_schema(tmp_path):
    record = {"timestamp": 1700000000, "annualizedFundingRate": 0.05}
    current_relative = "funding-rate/Hyperliquid-HYPE.ndjson.zip"
    legacy_relative = "underlying-apr/Hyperliquid-HYPE.ndjson.zip"
    current_path = write_ndjson_zip(
        tmp_path, current_relative, [("rows.ndjson", json.dumps(record))]
    )
    legacy_path = write_ndjson_zip(
        tmp_path, legacy_relative, [("rows.ndjson", json.dumps(record))]
    )

    current = list(normalize_funding_rates(current_path, raw_root=tmp_path))[0]
    legacy = list(normalize_funding_rates(legacy_path, raw_root=tmp_path))[0]

    assert set(current) == set(legacy)
    assert current["annualized_funding_apr"] == 0.05
    assert legacy["annualized_funding_apr"] == 0.05
    assert current["venue"] == legacy["venue"] == "HYPERLIQUID"
    assert current["asset"] == legacy["asset"] == "HYPE"


def test_task3_catalogs_keep_market_and_collateral_identity():
    market = MarketInfo(
        market_id=155,
        token_id=3,
        venue="HYPERLIQUID",
        asset="HYPE",
        maturity=date(2026, 9, 25),
        symbol="HYPERLIQUID-HYPEUSDT-25SEP2026",
        name="Hyperliquid HYPE 25 Sep 2026",
    )
    collateral = CollateralAsset(
        token_id=3,
        symbol="USDT",
        asset_id="USDT",
        address="0x3",
        name="USD₮0",
        decimals=6,
        usd_price=Decimal("1.0"),
    )

    assert normalize_markets({155: market})[0]["token_id"] == 3
    assert normalize_markets({155: market})[0]["market_id"] == 155
    assert normalize_assets({3: collateral})[0]["token_id"] == 3
    assert normalize_assets({3: collateral})[0]["usd_price"] == Decimal("1.0")


def test_settlement_and_ohlcv_use_explicit_canonical_field_names(tmp_path):
    settlement_relative = (
        "settlement/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip"
    )
    settlement = write_ndjson_zip(
        tmp_path,
        settlement_relative,
        [("rows.ndjson", json.dumps({"timestamp": 1700000000, "settlementApr": 0.03}))],
    )
    ohlcv_relative = "ohlcv/5m/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip"
    ohlcv = write_ndjson_zip(
        tmp_path,
        ohlcv_relative,
        [
            (
                "rows.ndjson",
                json.dumps(
                    {
                        "periodStartTimestamp": 1700000000,
                        "open": 0.01,
                        "high": 0.02,
                        "low": 0.005,
                        "close": 0.015,
                        "volume": 12.5,
                    }
                ),
            )
        ],
    )

    settlement_row = list(normalize_settlements(settlement, raw_root=tmp_path))[0]
    ohlcv_row = list(normalize_ohlcv(ohlcv, raw_root=tmp_path))[0]

    assert settlement_row["settlement_apr"] == 0.03
    assert settlement_row["tx_hash"] is None
    assert ohlcv_row["period_start_timestamp"] == 1700000000
    assert ohlcv_row["open_apr"] == 0.01
    assert ohlcv_row["volume_collateral"] == 12.5


@pytest.mark.parametrize(
    "content, expected",
    [
        ('{"ok": 1}\n{bad}\n', "line 2"),
        ('{"ok": 1}\n[]\n', "line 2"),
    ],
)
def test_iter_ndjson_zip_rejects_malformed_or_non_object_rows(tmp_path, content, expected):
    path = write_ndjson_zip(tmp_path, "market-data/bad/2026-08.ndjson.zip", [("bad.ndjson", content)])

    with pytest.raises(ValueError) as error:
        list(iter_ndjson_zip(path))

    message = str(error.value)
    assert str(path) in message
    assert "bad.ndjson" in message
    assert expected in message
