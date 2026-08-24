from datetime import date

import duckdb

from boros_research.storage import build_duckdb_catalog, write_dataset


def market_rows():
    common = {
        "block_number": 7,
        "market_id": 155,
        "venue": "HYPERLIQUID",
        "asset": "HYPE",
        "maturity": date(2026, 9, 25),
        "best_bid_apr": None,
        "best_ask_apr": 0.06,
        "amm_implied_apr": None,
        "mark_apr": 0.04,
        "notional_oi_collateral": None,
        "last_traded_apr": 0.055,
        "latest_settlement_apr": None,
    }
    return [
        {
            **common,
            "timestamp": 1700000000,
            "mid_apr": 0.05,
            "source_path": "market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        },
        {
            **common,
            "timestamp": 1700000300,
            "mid_apr": 0.07,
            "source_path": "market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip",
        },
    ]


def order_book_rows():
    return [
        {
            "grid_timestamp": 1787443500,
            "market_id": 155,
            "venue": "HYPERLIQUID",
            "asset": "HYPE",
            "maturity": date(2026, 9, 25),
            "snapshot_timestamp": 1787443320,
            "snapshot_age_sec": 180,
            "block_number": 1,
            "status": "ok",
            "bids": [
                {"rate_apr": 0.10, "size_collateral": 1000.0},
                {"rate_apr": 0.09, "size_collateral": 2000.0},
            ],
            "asks": [
                {"rate_apr": 0.11, "size_collateral": 1000.0},
                {"rate_apr": 0.12, "size_collateral": 3000.0},
            ],
            "source_path": "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        },
        {
            "grid_timestamp": 1787443800,
            "market_id": 155,
            "venue": "HYPERLIQUID",
            "asset": "HYPE",
            "maturity": date(2026, 9, 25),
            "snapshot_timestamp": 1787442899,
            "snapshot_age_sec": 901,
            "block_number": 0,
            "status": "stale",
            "bids": None,
            "asks": None,
            "source_path": "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip",
        },
    ]


def asset_price_rows():
    return [
        {
            "asset": "HYPE",
            "timestamp": 1787443200,
            "price_usd": 2422.76754675,
            "source_market_id": 155,
            "source_path": "raw_indicators/asset-price/HYPE/market-155/export-ap-5m-1787443200-1787443200.csv",
        },
        {
            "asset": "USDT",
            "timestamp": 1787443200,
            "price_usd": 1.0,
            "source_market_id": None,
            "source_path": "synthetic/stable/USDT",
        },
    ]


def opportunity_rows():
    common = {
        "asset": "HYPE",
        "maturity": date(2026, 9, 25),
        "dte_days": 32,
        "token_id": 3,
        "short_market_id": 155,
        "short_venue": "HYPERLIQUID",
        "long_market_id": 201,
        "long_venue": "BYBIT",
        "notional_usd": 2_000.0,
        "short_bid_vwap_apr": 0.109,
        "long_ask_vwap_apr": 0.062,
        "short_top_bid_apr": 0.109,
        "long_top_ask_apr": 0.062,
        "top_of_book_spread_apr": 0.047,
        "short_impact_apr": 0.0,
        "long_impact_apr": 0.0,
        "short_filled_usd": 2_000.0,
        "long_filled_usd": 2_000.0,
        "short_snapshot_age_sec": 120,
        "long_snapshot_age_sec": 180,
        "short_price_age_sec": 60,
        "long_price_age_sec": 240,
    }
    return [
        {
            **common,
            "timestamp": 1_787_443_200,
            "executable_spread_apr": 0.047,
            "fully_executable": True,
            "invalid_reason": None,
        },
        {
            **common,
            "timestamp": 1_787_443_500,
            "executable_spread_apr": None,
            "fully_executable": False,
            "invalid_reason": "long_depth_insufficient",
            "long_filled_usd": 1_500.0,
        },
    ]


def test_parquet_roundtrip_and_duckdb_view(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    rows = market_rows()

    dataset_path = write_dataset(rows, "market_data", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    assert dataset_path == parquet_root / "market_data"
    assert list(dataset_path.glob("asset=HYPE/year=*/month=*/*.parquet"))

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*), max(mid_apr) FROM market_data"
        ).fetchone() == (2, 0.07)
        assert connection.execute(
            "SELECT venue, asset FROM market_data ORDER BY timestamp LIMIT 1"
        ).fetchone() == ("HYPERLIQUID", "HYPE")
        assert connection.execute(
            "SELECT best_bid_apr FROM market_data ORDER BY timestamp LIMIT 1"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone() == (1,)


def test_catalog_exposes_all_views_without_importing_parquet_as_tables(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    write_dataset(market_rows(), "market_data", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    expected_views = {
        "market_data",
        "funding_rates",
        "settlements",
        "ohlcv_5m",
        "order_books_5m",
        "asset_prices",
        "markets",
        "assets",
        "executable_opportunities",
    }
    with duckdb.connect(str(database_path), read_only=True) as connection:
        actual_views = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert expected_views <= actual_views
        assert connection.execute("SELECT count(*) FROM funding_rates").fetchone() == (0,)


def test_executable_opportunities_round_trip_and_rebuild_is_idempotent(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    rows = opportunity_rows()

    dataset_path = write_dataset(rows, "executable_opportunities", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    write_dataset(rows, "executable_opportunities", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    assert list(dataset_path.glob("asset=HYPE/year=*/month=*/*.parquet"))

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            """
            SELECT
                timestamp,
                short_venue,
                long_venue,
                notional_usd,
                executable_spread_apr,
                fully_executable,
                invalid_reason
            FROM executable_opportunities
            ORDER BY timestamp
            """
        ).fetchall() == [
            (1_787_443_200, "HYPERLIQUID", "BYBIT", 2_000.0, 0.047, True, None),
            (
                1_787_443_500,
                "HYPERLIQUID",
                "BYBIT",
                2_000.0,
                None,
                False,
                "long_depth_insufficient",
            ),
        ]
        assert connection.execute(
            "SELECT count(*) FROM executable_opportunities"
        ).fetchone() == (2,)


def test_asset_prices_round_trip_partition_and_catalog_rebuild(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    rows = asset_price_rows()

    dataset_path = write_dataset(rows, "asset_prices", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    write_dataset(rows, "asset_prices", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    assert list(dataset_path.glob("asset=HYPE/year=*/month=*/*.parquet"))
    assert list(dataset_path.glob("asset=USDT/year=*/month=*/*.parquet"))

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT asset, timestamp, price_usd, source_market_id "
            "FROM asset_prices ORDER BY asset"
        ).fetchall() == [
            ("HYPE", 1787443200, 2422.76754675, 155),
            ("USDT", 1787443200, 1.0, None),
        ]
        assert connection.execute("SELECT count(*) FROM asset_prices").fetchone() == (2,)


def test_order_books_nested_levels_round_trip_and_rebuild_is_idempotent(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    rows = order_book_rows()

    write_dataset(rows, "order_books_5m", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    write_dataset(rows, "order_books_5m", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            """
            SELECT
                count(*),
                bids[1].rate_apr,
                bids[2].size_collateral,
                asks[1].rate_apr,
                asks[2].size_collateral
            FROM order_books_5m
            WHERE status = 'ok'
            GROUP BY ALL
            """
        ).fetchone() == (1, 0.10, 2000.0, 0.11, 3000.0)
        assert connection.execute(
            """
            SELECT status, snapshot_age_sec, bids IS NULL, asks IS NULL
            FROM order_books_5m
            WHERE status = 'stale'
            """
        ).fetchone() == ("stale", 901, True, True)
        assert connection.execute("SELECT count(*) FROM order_books_5m").fetchone() == (2,)


def test_rebuilding_dataset_and_catalog_does_not_duplicate_rows(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    rows = market_rows()

    write_dataset(rows, "market_data", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    write_dataset(rows, "market_data", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM market_data").fetchone() == (2,)


def phase2_rows():
    benchmark = {
        "timestamp": 1_787_443_200,
        "asset": "HYPE",
        "maturity": date(2026, 9, 25),
        "dte_days": 32,
        "dte_bucket": "22-45",
        "token_id": 3,
        "short_market_id": 155,
        "short_venue": "HYPERLIQUID",
        "long_market_id": 201,
        "long_venue": "BYBIT",
        "notional_usd": 2_000.0,
        "executable_spread_apr": 0.047,
        "benchmark_level": "dte",
        "percentile_30d": 95.0,
        "percentile_90d": 92.0,
        "percentile_lifetime": 96.0,
        "sample_count_30d": 60,
        "sample_count_90d": 80,
        "sample_count_lifetime": 100,
    }
    episode = {
        "asset": "HYPE",
        "short_venue": "HYPERLIQUID",
        "long_venue": "BYBIT",
        "token_id": 3,
        "notional_usd": 2_000.0,
        "dte_bucket": "22-45",
        "threshold_percentile": 95.0,
        "start_timestamp": 1_787_443_200,
        "end_timestamp": 1_787_443_500,
        "duration_minutes": 5,
        "observation_count": 2,
        "peak_spread_apr": 0.05,
        "mean_spread_apr": 0.048,
    }
    summary = {
        "asset": "HYPE",
        "short_venue": "HYPERLIQUID",
        "long_venue": "BYBIT",
        "token_id": 3,
        "notional_usd": 2_000.0,
        "dte_bucket": "22-45",
        "threshold_percentile": 95.0,
        "episode_count": 1,
        "median_duration_minutes": 5.0,
        "p75_duration_minutes": 5.0,
    }
    return [benchmark], [episode], [summary]


def test_phase2_benchmark_storage_and_current_persistence_view(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    benchmark, episodes, summaries = phase2_rows()

    write_dataset(benchmark, "historical_benchmarks", parquet_root)
    write_dataset(episodes, "spread_episodes", parquet_root)
    write_dataset(summaries, "persistence_summary", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    build_duckdb_catalog(parquet_root, database_path)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*), max(percentile_lifetime) FROM historical_benchmarks"
        ).fetchone() == (1, 96.0)
        assert connection.execute(
            "SELECT count(*), max(duration_minutes) FROM spread_episodes"
        ).fetchone() == (1, 5)
        assert connection.execute(
            "SELECT episode_count, median_duration_minutes FROM persistence_summary"
        ).fetchone() == (1, 5.0)
        assert connection.execute(
            """
            SELECT
                current_p90_above_threshold,
                current_p95_above_threshold,
                current_p95_persistence_minutes
            FROM historical_benchmark_status
            """
        ).fetchone() == (False, True, 0)
