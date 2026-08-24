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
        "markets",
        "assets",
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
