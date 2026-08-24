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
