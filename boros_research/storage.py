from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .config import DUCKDB_PATH, PARQUET_DIR


SCHEMA_VERSION = 1


_BOOK_LEVEL_TYPE = pa.struct(
    [
        pa.field("rate_apr", pa.float64()),
        pa.field("size_collateral", pa.float64()),
    ]
)


def _schema(*fields: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema([pa.field(name, data_type) for name, data_type in fields])


CANONICAL_SCHEMAS: dict[str, pa.Schema] = {
    "market_data": _schema(
        ("timestamp", pa.int64()),
        ("block_number", pa.int64()),
        ("market_id", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("mid_apr", pa.float64()),
        ("best_bid_apr", pa.float64()),
        ("best_ask_apr", pa.float64()),
        ("amm_implied_apr", pa.float64()),
        ("mark_apr", pa.float64()),
        ("notional_oi_collateral", pa.float64()),
        ("last_traded_apr", pa.float64()),
        ("latest_settlement_apr", pa.float64()),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "funding_rates": _schema(
        ("timestamp", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("annualized_funding_apr", pa.float64()),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "settlements": _schema(
        ("timestamp", pa.int64()),
        ("block_number", pa.int64()),
        ("market_id", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("settlement_apr", pa.float64()),
        ("tx_hash", pa.string()),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "ohlcv_5m": _schema(
        ("period_start_timestamp", pa.int64()),
        ("market_id", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("open_apr", pa.float64()),
        ("high_apr", pa.float64()),
        ("low_apr", pa.float64()),
        ("close_apr", pa.float64()),
        ("volume_collateral", pa.float64()),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    # Keep complete visible depth as list<struct> values instead of one row
    # per level; later execution code can choose how many levels to consume.
    "order_books_5m": _schema(
        ("grid_timestamp", pa.int64()),
        ("market_id", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("snapshot_timestamp", pa.int64()),
        ("snapshot_age_sec", pa.int64()),
        ("block_number", pa.int64()),
        ("status", pa.string()),
        ("bids", pa.list_(_BOOK_LEVEL_TYPE)),
        ("asks", pa.list_(_BOOK_LEVEL_TYPE)),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "asset_prices": _schema(
        ("asset", pa.string()),
        ("timestamp", pa.int64()),
        ("price_usd", pa.float64()),
        ("source_market_id", pa.int64()),
        ("source_path", pa.string()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "executable_opportunities": _schema(
        ("timestamp", pa.int64()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("dte_days", pa.int64()),
        ("token_id", pa.int64()),
        ("short_market_id", pa.int64()),
        ("short_venue", pa.string()),
        ("long_market_id", pa.int64()),
        ("long_venue", pa.string()),
        ("notional_usd", pa.float64()),
        ("short_bid_vwap_apr", pa.float64()),
        ("long_ask_vwap_apr", pa.float64()),
        ("executable_spread_apr", pa.float64()),
        ("short_top_bid_apr", pa.float64()),
        ("long_top_ask_apr", pa.float64()),
        ("top_of_book_spread_apr", pa.float64()),
        ("short_impact_apr", pa.float64()),
        ("long_impact_apr", pa.float64()),
        ("short_filled_usd", pa.float64()),
        ("long_filled_usd", pa.float64()),
        ("fully_executable", pa.bool_()),
        ("invalid_reason", pa.string()),
        ("short_snapshot_age_sec", pa.int64()),
        ("long_snapshot_age_sec", pa.int64()),
        ("short_price_age_sec", pa.int64()),
        ("long_price_age_sec", pa.int64()),
        ("year", pa.string()),
        ("month", pa.string()),
    ),
    "markets": _schema(
        ("market_id", pa.int64()),
        ("token_id", pa.int64()),
        ("venue", pa.string()),
        ("asset", pa.string()),
        ("maturity", pa.date32()),
        ("symbol", pa.string()),
        ("name", pa.string()),
    ),
    "assets": _schema(
        ("token_id", pa.int64()),
        ("symbol", pa.string()),
        ("asset_id", pa.string()),
        ("address", pa.string()),
        ("name", pa.string()),
        ("decimals", pa.int64()),
        ("usd_price", pa.decimal128(38, 18)),
    ),
}


PARTITION_COLUMNS: dict[str, tuple[str, ...]] = {
    "market_data": ("asset", "year", "month"),
    "funding_rates": ("venue", "asset", "year", "month"),
    "settlements": ("asset", "year", "month"),
    "ohlcv_5m": ("asset", "year", "month"),
    "order_books_5m": ("asset", "year", "month"),
    "asset_prices": ("asset", "year", "month"),
    "executable_opportunities": ("asset", "year", "month"),
    "markets": (),
    "assets": (),
}


VIEW_COLUMNS = {
    dataset: tuple(field.name for field in schema if field.name not in {"year", "month"})
    for dataset, schema in CANONICAL_SCHEMAS.items()
}


_TIMESTAMP_FIELD = {
    "market_data": "timestamp",
    "funding_rates": "timestamp",
    "settlements": "timestamp",
    "ohlcv_5m": "period_start_timestamp",
    "order_books_5m": "grid_timestamp",
    "asset_prices": "timestamp",
    "executable_opportunities": "timestamp",
}


def _partition_row(dataset: str, row: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(row)
    if dataset in _TIMESTAMP_FIELD:
        timestamp_field = _TIMESTAMP_FIELD[dataset]
        timestamp = output.get(timestamp_field)
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise ValueError(f"{dataset}.{timestamp_field} must be an integer Unix timestamp")
        moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        output["year"] = f"{moment.year:04d}"
        output["month"] = f"{moment.month:02d}"
    return output


def _table_for_rows(
    dataset: str,
    rows: Sequence[Mapping[str, Any]],
) -> pa.Table:
    schema = CANONICAL_SCHEMAS[dataset]
    normalized = [
        {field.name: row.get(field.name) for field in schema}
        for row in rows
    ]
    return pa.Table.from_pylist(normalized, schema=schema)


def _partitioning(dataset: str):
    columns = PARTITION_COLUMNS[dataset]
    if not columns:
        return None
    schema = CANONICAL_SCHEMAS[dataset]
    partition_schema = pa.schema([schema.field(column) for column in columns])
    return ds.partitioning(partition_schema, flavor="hive")


def _write_batch(
    dataset: str,
    rows: Sequence[Mapping[str, Any]],
    destination: Path,
    batch_index: int,
) -> bool:
    table = _table_for_rows(dataset, rows)
    ds.write_dataset(
        table,
        base_dir=str(destination),
        format="parquet",
        schema=CANONICAL_SCHEMAS[dataset],
        partitioning=_partitioning(dataset),
        basename_template=f"part-{batch_index:08d}-{{i}}.parquet",
        existing_data_behavior="overwrite_or_ignore",
        use_threads=False,
    )
    return table.num_rows > 0


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _replace_dataset_directory(temp_path: Path, target_path: Path) -> None:
    backup_path = target_path.with_name(f".{target_path.name}.previous")
    if backup_path.exists():
        _remove_path(backup_path)
    had_previous = target_path.exists()
    try:
        if had_previous:
            target_path.replace(backup_path)
        temp_path.replace(target_path)
    except Exception:
        if target_path.exists():
            _remove_path(target_path)
        if had_previous and backup_path.exists():
            backup_path.replace(target_path)
        raise
    else:
        if backup_path.exists():
            _remove_path(backup_path)


def write_dataset(
    rows: Iterable[Mapping[str, Any]],
    dataset: str,
    output_root: Path = PARQUET_DIR,
    *,
    batch_size: int = 10_000,
) -> Path:
    """Rebuild one explicit-schema Parquet dataset without appending duplicates."""
    if dataset not in CANONICAL_SCHEMAS:
        raise ValueError(f"unknown canonical dataset: {dataset}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    target_path = output_root / dataset
    temp_path = Path(tempfile.mkdtemp(prefix=f".{dataset}.tmp-", dir=output_root))
    batch: list[dict[str, Any]] = []
    batch_index = 0
    wrote_rows = False

    try:
        for row in rows:
            batch.append(_partition_row(dataset, row))
            if len(batch) >= batch_size:
                wrote_rows = _write_batch(dataset, batch, temp_path, batch_index) or wrote_rows
                batch = []
                batch_index += 1
        if batch:
            wrote_rows = _write_batch(dataset, batch, temp_path, batch_index) or wrote_rows
        if not wrote_rows:
            empty_table = _table_for_rows(dataset, [])
            empty_path = temp_path / "part-00000000-0.parquet"
            pq.write_table(empty_table, empty_path, compression="zstd")
        _replace_dataset_directory(temp_path, target_path)
    except Exception:
        if temp_path.exists():
            _remove_path(temp_path)
        raise
    return target_path


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb_type(data_type: pa.DataType) -> str:
    if pa.types.is_boolean(data_type):
        return "BOOLEAN"
    if pa.types.is_int64(data_type):
        return "BIGINT"
    if pa.types.is_float64(data_type):
        return "DOUBLE"
    if pa.types.is_string(data_type):
        return "VARCHAR"
    if pa.types.is_date32(data_type) or pa.types.is_date64(data_type):
        return "DATE"
    if pa.types.is_decimal(data_type):
        return f"DECIMAL({data_type.precision}, {data_type.scale})"
    if pa.types.is_struct(data_type):
        fields = ", ".join(
            f"{_quote_identifier(field.name)} {_duckdb_type(field.type)}"
            for field in data_type
        )
        return f"STRUCT({fields})"
    if pa.types.is_list(data_type):
        return f"{_duckdb_type(data_type.value_type)}[]"
    raise ValueError(f"unsupported Arrow type for DuckDB view: {data_type}")


def _empty_view_sql(dataset: str) -> str:
    schema = CANONICAL_SCHEMAS[dataset]
    expressions = [
        f"CAST(NULL AS {_duckdb_type(schema.field(column).type)}) AS {_quote_identifier(column)}"
        for column in VIEW_COLUMNS[dataset]
    ]
    return f"SELECT {', '.join(expressions)} WHERE FALSE"


def _parquet_glob(dataset_path: Path) -> str:
    return (dataset_path.resolve() / "**" / "*.parquet").as_posix().replace("'", "''")


def build_duckdb_catalog(
    parquet_root: Path = PARQUET_DIR,
    database_path: Path = DUCKDB_PATH,
    *,
    schema_version: int = SCHEMA_VERSION,
) -> Path:
    """Create idempotent DuckDB views over Parquet plus schema metadata."""
    parquet_root = Path(parquet_root)
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            "CREATE OR REPLACE TABLE schema_metadata AS "
            "SELECT CAST(? AS INTEGER) AS schema_version",
            [schema_version],
        )
        for dataset, schema in CANONICAL_SCHEMAS.items():
            view_columns = ", ".join(_quote_identifier(column) for column in VIEW_COLUMNS[dataset])
            dataset_path = parquet_root / dataset
            has_files = dataset_path.exists() and any(dataset_path.rglob("*.parquet"))
            if has_files:
                relation = (
                    f"read_parquet('{_parquet_glob(dataset_path)}', "
                    "hive_partitioning=true, union_by_name=true)"
                )
                sql = (
                    f"CREATE OR REPLACE VIEW {_quote_identifier(dataset)} AS "
                    f"SELECT {view_columns} FROM {relation}"
                )
            else:
                sql = (
                    f"CREATE OR REPLACE VIEW {_quote_identifier(dataset)} AS "
                    f"{_empty_view_sql(dataset)}"
                )
            connection.execute(sql)
    return database_path
