import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

import boros_research.build as build_module
from boros_research.build import BuildPaths, _validate_book_market, run_build
from boros_research.cli import main as cli_main
from boros_research.market_metadata import MarketInfo
from boros_research.normalize import parse_market_slug


BASE_TIMESTAMP = 1_787_529_600
STALE_TIMESTAMP = BASE_TIMESTAMP + 1_200
MATURITY_TIMESTAMP = int(
    datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp()
)


def zip_payload(records):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        body = "".join(json.dumps(record) + "\n" for record in records)
        archive.writestr("rows.ndjson", body)
    return buffer.getvalue()


def combined_payload(timestamp, bid_rate, ask_rate, size):
    return zip_payload(
        [
            {
                "timestamp": timestamp,
                "blockNumber": 1,
                "long": [{"rate": bid_rate, "size": size}],
                "short": [{"rate": ask_rate, "size": size}],
            }
        ]
    )


def market_raw(market_id, venue, asset, token_id):
    return {
        "marketId": market_id,
        "tokenId": token_id,
        "underlyingSymbol": asset,
        "metadata": {},
        "imData": {
            "symbol": f"{venue}-{asset}USDT-25SEP2026",
            "name": f"{venue} {asset} 25 Sep 2026",
            "maturity": MATURITY_TIMESTAMP,
        },
        "state": "Normal",
    }


def assets_payload():
    return {
        "results": [
            {
                "id": "USDT",
                "address": "0x3",
                "tokenId": 3,
                "name": "USD₮0",
                "symbol": "USD₮0",
                "decimals": 6,
                "usdPrice": "1.0",
                "isCollateral": True,
                "metadata": {"proSymbol": "USDT"},
            },
            {
                "id": "HYPE",
                "address": "0x5",
                "tokenId": 5,
                "name": "HYPE",
                "symbol": "HYPE",
                "decimals": 18,
                "usdPrice": None,
                "isCollateral": True,
                "metadata": {},
            },
        ]
    }


def make_fixture_archive():
    payloads = {}

    def add(path, payload):
        payloads[path] = payload

    add(
        "market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        zip_payload(
            [
                {
                    "timestamp": BASE_TIMESTAMP,
                    "blockNumber": 1,
                    "midApr": 0.10,
                    "bestBid": 0.109,
                    "bestAsk": 0.115,
                }
            ]
        ),
    )
    add(
        "market-data/201-BYBIT-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        zip_payload(
            [
                {
                    "timestamp": BASE_TIMESTAMP,
                    "blockNumber": 1,
                    "midApr": 0.06,
                    "bestBid": 0.058,
                    "bestAsk": 0.062,
                }
            ]
        ),
    )
    add(
        "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        combined_payload(BASE_TIMESTAMP, 0.109, 0.115, 2_000),
    )
    add(
        "order-book/201-BYBIT-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        combined_payload(BASE_TIMESTAMP, 0.058, 0.062, 2_000),
    )
    add(
        "order-book/301-HYPERLIQUID-ETHUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        combined_payload(STALE_TIMESTAMP, 0.20, 0.21, 100),
    )
    add(
        "order-book/302-BYBIT-ETHUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        combined_payload(STALE_TIMESTAMP, 0.18, 0.19, 100),
    )
    add(
        "funding-rate/Hyperliquid-HYPE.ndjson.zip",
        zip_payload(
            [{"timestamp": BASE_TIMESTAMP, "annualizedFundingRate": 0.01}]
        ),
    )
    add(
        "underlying-apr/Hyperliquid-HYPE.ndjson.zip",
        zip_payload(
            [{"timestamp": BASE_TIMESTAMP, "annualizedFundingRate": 0.01}]
        ),
    )
    add(
        "settlement/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        zip_payload([{"timestamp": BASE_TIMESTAMP, "settlementApr": 0.02}]),
    )
    add(
        "ohlcv/5m/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        zip_payload(
            [
                {
                    "periodStartTimestamp": BASE_TIMESTAMP,
                    "open": 0.10,
                    "high": 0.11,
                    "low": 0.09,
                    "close": 0.105,
                    "volume": 10,
                }
            ]
        ),
    )
    add(
        "ohlcv/1d/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        zip_payload(
            [
                {
                    "periodStartTimestamp": BASE_TIMESTAMP,
                    "open": 0.10,
                    "high": 0.11,
                    "low": 0.09,
                    "close": 0.105,
                    "volume": 100,
                }
            ]
        ),
    )
    return payloads


def make_manifest(payloads):
    return [
        {"path": path, "size": len(payload)}
        for path, payload in sorted(payloads.items())
    ]


def make_metadata_requester(calls):
    markets = [
        market_raw(155, "HYPERLIQUID", "HYPE", 3),
        market_raw(201, "BYBIT", "HYPE", 3),
        market_raw(301, "HYPERLIQUID", "ETH", 5),
        market_raw(302, "BYBIT", "ETH", 5),
    ]
    page = {"results": markets, "cursor": {"next": None, "hasMore": False}}

    def requester(url, params):
        calls.append((url, dict(params)))
        if url.endswith("/markets"):
            return page
        if url.endswith("/assets"):
            return assets_payload()
        raise AssertionError(url)

    return requester


def build_paths(tmp_path):
    data_root = tmp_path / "data"
    return BuildPaths(
        raw_boros_dir=tmp_path / "raw_boros",
        raw_api_dir=tmp_path / "raw_api",
        raw_indicators_dir=tmp_path / "raw_indicators",
        parquet_dir=data_root / "parquet",
        duckdb_path=data_root / "boros.duckdb",
        report_path=data_root / "build_report.json",
    )


def run_fixture_build(tmp_path, payloads, manifest, counters):
    paths = build_paths(tmp_path)

    def manifest_fetcher(_url):
        counters["manifest"] += 1
        return io.BytesIO(json.dumps(manifest).encode())

    def archive_fetcher(url):
        counters["archive"] += 1
        relative = url.split("historical-data.boros.finance/", 1)[1]
        return io.BytesIO(payloads[relative])

    metadata_requester = make_metadata_requester(counters["metadata_calls"])

    def indicator_exporter(url, params):
        counters["indicator"] += 1
        return f"timestamp,asset_price_usd\n{BASE_TIMESTAMP},50.0\n"

    result = run_build(
        paths,
        workers=2,
        refresh_manifest=True,
        refresh_metadata=True,
        manifest_fetcher=manifest_fetcher,
        archive_fetcher=archive_fetcher,
        metadata_requester=metadata_requester,
        indicator_exporter=indicator_exporter,
        indicator_request_delay_sec=0,
    )
    return paths, result


def query_one(database_path, sql):
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(sql).fetchone()


@pytest.mark.parametrize(
    ("slug", "asset"),
    [
        ("73-HYPERLIQUID-xyzCL-27MAR2026", "CLOIL"),
        ("77-HYPERLIQUID-xyzSILVER-27MAR2026", "XAG"),
        ("105-BINANCE-BZUSDT-21MAY2026", "BRENTOIL"),
        ("176-HYPERLIQUID-xyzSKHX-5AUG2026", "SKHYNIX"),
    ],
)
def test_official_symbol_aliases_match_canonical_market_assets(slug, asset):
    parsed = parse_market_slug(slug)
    market = MarketInfo(
        market_id=parsed.market_id,
        token_id=3,
        venue=parsed.venue,
        asset=asset,
        maturity=parsed.maturity,
        symbol=f"{parsed.venue}-{parsed.symbol}-{slug.rsplit('-', 1)[1]}",
        name=f"{asset} market",
    )

    assert _validate_book_market(slug, parsed, {market.market_id: market}, set()) == market


def test_order_book_ingestion_aligns_each_market_before_reading_next_market(monkeypatch):
    first_path = (
        "order-book/1-BYBIT-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip"
    )
    second_path = (
        "order-book/2-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip"
    )
    selected = [
        {"path": first_path, "size": 1},
        {"path": second_path, "size": 1},
    ]
    markets = {
        1: MarketInfo(
            market_id=1,
            token_id=3,
            venue="BYBIT",
            asset="HYPE",
            maturity=datetime.fromtimestamp(
                MATURITY_TIMESTAMP, tz=timezone.utc
            ).date(),
            symbol="BYBIT-HYPEUSDT-25SEP2026",
            name="Bybit HYPE",
        ),
        2: MarketInfo(
            market_id=2,
            token_id=3,
            venue="HYPERLIQUID",
            asset="HYPE",
            maturity=datetime.fromtimestamp(
                MATURITY_TIMESTAMP, tz=timezone.utc
            ).date(),
            symbol="HYPERLIQUID-HYPEUSDT-25SEP2026",
            name="Hyperliquid HYPE",
        ),
    }
    events = []

    def fake_iter(path):
        market_id = 1 if "1-BYBIT" in str(path) else 2
        events.append(("parse", market_id))
        yield {
            "timestamp": BASE_TIMESTAMP,
            "blockNumber": market_id,
            "long": [{"rate": 0.1, "size": 1}],
            "short": [{"rate": 0.2, "size": 1}],
        }

    original_align = build_module.align_snapshots_to_grid

    def tracked_align(snapshots, grid_start, grid_end):
        events.append(("align", len([event for event in events if event[0] == "align"]) + 1))
        return original_align(snapshots, grid_start, grid_end)

    monkeypatch.setattr(build_module, "iter_ndjson_zip", fake_iter)
    monkeypatch.setattr(build_module, "align_snapshots_to_grid", tracked_align)

    build_module._book_rows(selected, Path("unused"), markets, set())

    assert events.index(("align", 1)) < events.index(("parse", 2))


def test_run_build_reconstructs_spreads_and_quality_report_offline(tmp_path):
    payloads = make_fixture_archive()
    manifest = make_manifest(payloads)
    counters = {"manifest": 0, "archive": 0, "indicator": 0, "metadata_calls": []}
    paths, result = run_fixture_build(tmp_path, payloads, manifest, counters)

    assert query_one(
        paths.duckdb_path,
        """
        SELECT executable_spread_apr
        FROM executable_opportunities
        WHERE short_venue = 'HYPERLIQUID'
          AND long_venue = 'BYBIT'
          AND asset = 'HYPE'
          AND notional_usd = 2000
        """,
    ) == (0.047,)
    assert query_one(
        paths.duckdb_path,
        """
        SELECT executable_spread_apr
        FROM executable_opportunities
        WHERE short_venue = 'BYBIT'
          AND long_venue = 'HYPERLIQUID'
          AND asset = 'HYPE'
          AND notional_usd = 2000
        """,
    ) == (-0.057,)
    assert query_one(
        paths.duckdb_path,
        "SELECT count(*) FROM funding_rates",
    ) == (1,)
    assert query_one(
        paths.duckdb_path,
        "SELECT source_path FROM funding_rates",
    )[0].startswith("funding-rate/")
    assert query_one(paths.duckdb_path, "SELECT count(*) FROM ohlcv_5m") == (1,)
    assert query_one(
        paths.duckdb_path,
        "SELECT count(*) FROM executable_opportunities",
    ) == (24,)

    assert query_one(
        paths.duckdb_path,
        """
        SELECT fully_executable, invalid_reason
        FROM executable_opportunities
        WHERE asset = 'HYPE'
          AND short_venue = 'HYPERLIQUID'
          AND long_venue = 'BYBIT'
          AND notional_usd = 50000
        """,
    ) == (False, "short_depth_insufficient;long_depth_insufficient")

    top_without_price = query_one(
        paths.duckdb_path,
        """
        SELECT top_of_book_spread_apr, executable_spread_apr,
               fully_executable, invalid_reason
        FROM executable_opportunities
        WHERE asset = 'ETH'
          AND short_venue = 'HYPERLIQUID'
          AND long_venue = 'BYBIT'
          AND notional_usd = 2000
        """,
    )
    assert top_without_price[0] == pytest.approx(0.01)
    assert top_without_price[1:] == (
        None,
        False,
        "short_price_stale;long_price_stale",
    )

    report = json.loads(paths.report_path.read_text())
    assert report["build_status"] == "ok"
    assert report["source_files_expected"] == len(manifest)
    assert report["source_files_present"] == len(manifest)
    assert report["parse_failures"] == 0
    assert report["unpriceable_observations"] == 2
    assert report["market_group_count"] == 2
    assert report["opportunity_row_count"] == 24
    assert result.report.opportunity_row_count == 24


def test_build_keeps_final_off_grid_orderbook_snapshot(tmp_path):
    payloads = make_fixture_archive()
    off_grid_timestamp = BASE_TIMESTAMP + 120
    payloads[
        "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip"
    ] = combined_payload(off_grid_timestamp, 0.109, 0.115, 2_000)
    payloads[
        "order-book/201-BYBIT-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip"
    ] = combined_payload(off_grid_timestamp, 0.058, 0.062, 2_000)
    manifest = make_manifest(payloads)
    counters = {"manifest": 0, "archive": 0, "indicator": 0, "metadata_calls": []}

    paths, _ = run_fixture_build(tmp_path, payloads, manifest, counters)

    assert query_one(
        paths.duckdb_path,
        """
        SELECT grid_timestamp, snapshot_timestamp, snapshot_age_sec, status
        FROM order_books_5m
        WHERE market_id = 155
        """,
    ) == (BASE_TIMESTAMP + 300, off_grid_timestamp, 180, "ok")
    assert query_one(
        paths.duckdb_path,
        """
        SELECT executable_spread_apr
        FROM executable_opportunities
        WHERE short_venue = 'HYPERLIQUID'
          AND long_venue = 'BYBIT'
          AND asset = 'HYPE'
          AND notional_usd = 2000
        """,
    ) == (0.047,)


def test_second_build_is_idempotent_and_uses_raw_caches(tmp_path):
    payloads = make_fixture_archive()
    manifest = make_manifest(payloads)
    counters = {"manifest": 0, "archive": 0, "indicator": 0, "metadata_calls": []}
    paths, first = run_fixture_build(tmp_path, payloads, manifest, counters)
    first_counts = dict(
        manifest=counters["manifest"],
        archive=counters["archive"],
        indicator=counters["indicator"],
        metadata=len(counters["metadata_calls"]),
    )

    def never_manifest(_url):
        raise AssertionError("cached manifest should be used")

    def never_archive(_url):
        raise AssertionError("complete archive should be skipped")

    def never_metadata(_url, _params):
        raise AssertionError("cached metadata should be used")

    def never_indicator(_url, _params):
        raise AssertionError("cached indicator should be used")

    second = run_build(
        paths,
        workers=2,
        manifest_fetcher=never_manifest,
        archive_fetcher=never_archive,
        metadata_requester=never_metadata,
        indicator_exporter=never_indicator,
        indicator_request_delay_sec=0,
    )

    assert first.report.dataset_row_counts == second.report.dataset_row_counts
    assert first.report.opportunity_row_count == second.report.opportunity_row_count
    assert query_one(paths.duckdb_path, "SELECT count(*) FROM executable_opportunities") == (
        first.report.opportunity_row_count,
    )
    assert first_counts == {
        "manifest": 1,
        "archive": len(manifest),
        "indicator": 1,
        "metadata": 2,
    }


def test_failed_second_build_preserves_previous_derived_outputs(tmp_path):
    payloads = make_fixture_archive()
    manifest = make_manifest(payloads)
    counters = {"manifest": 0, "archive": 0, "indicator": 0, "metadata_calls": []}
    paths, first = run_fixture_build(tmp_path, payloads, manifest, counters)
    previous_count = query_one(
        paths.duckdb_path, "SELECT count(*) FROM executable_opportunities"
    )

    bad_path = "market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/bad.ndjson.zip"
    bad_payload = b"not a zip archive"
    broken_payloads = dict(payloads)
    broken_payloads[bad_path] = bad_payload
    broken_manifest = make_manifest(broken_payloads)

    def manifest_fetcher(_url):
        return io.BytesIO(json.dumps(broken_manifest).encode())

    def archive_fetcher(url):
        relative = url.split("historical-data.boros.finance/", 1)[1]
        return io.BytesIO(broken_payloads[relative])

    with pytest.raises(ValueError, match="invalid ZIP"):
        run_build(
            paths,
            workers=2,
            refresh_manifest=True,
            manifest_fetcher=manifest_fetcher,
            archive_fetcher=archive_fetcher,
            indicator_request_delay_sec=0,
        )

    failed_report = json.loads(paths.report_path.read_text())
    assert failed_report["build_status"] == "failed"
    assert failed_report["parse_failures"] >= 1
    assert query_one(
        paths.duckdb_path, "SELECT count(*) FROM executable_opportunities"
    ) == previous_count
    assert first.report.opportunity_row_count == previous_count[0]


def test_build_cli_help_is_available(capsys):
    assert cli_main(["build", "--help"]) == 0
    assert "build" in capsys.readouterr().out.lower()
