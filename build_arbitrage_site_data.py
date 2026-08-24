#!/usr/bin/env python3
"""Export a compact, read-only DuckDB snapshot for the arbitrage explorer."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "boros.duckdb"
DEFAULT_OUTPUT = ROOT / "site" / "data" / "boros_arbitrage_site_data.json"
NOTIONALS = (10_000, 25_000, 50_000)
WINDOW_90D_SECONDS = 90 * 24 * 60 * 60
HISTOGRAM_BINS = 30


def _structure_key(
    asset: str,
    token_id: int,
    maturity: str,
    short_market_id: int,
    short_venue: str,
    long_market_id: int,
    long_venue: str,
) -> str:
    return (
        f"{asset}|{token_id}|{maturity}|"
        f"{short_market_id}:{short_venue}>{long_market_id}:{long_venue}"
    )


def _dte_bucket_sql(expression: str) -> str:
    return (
        f"CASE WHEN {expression} <= 7 THEN '0-7' "
        f"WHEN {expression} <= 21 THEN '8-21' "
        f"WHEN {expression} <= 45 THEN '22-45' "
        f"WHEN {expression} <= 90 THEN '46-90' ELSE '91+' END"
    )


def _dte_bucket(dte_days: int | None) -> str | None:
    if dte_days is None:
        return None
    if dte_days <= 7:
        return "0-7"
    if dte_days <= 21:
        return "8-21"
    if dte_days <= 45:
        return "22-45"
    if dte_days <= 90:
        return "46-90"
    return "91+"


def _date_string(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _iso_timestamp(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _row_dict(description: list[Any], row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(description, row)}


def _query_dicts(connection: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    result = connection.execute(query)
    return [_row_dict(result.description, row) for row in result.fetchall()]


def _identity_from_row(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["asset"]),
        int(row["token_id"]),
        _date_string(row["maturity"]),
        int(row["short_market_id"]),
        str(row["short_venue"]),
        int(row["long_market_id"]),
        str(row["long_venue"]),
    )


def _key_from_row(row: dict[str, Any]) -> str:
    identity = _identity_from_row(row)
    return _structure_key(*identity)


def _structure_payload(identity: tuple[Any, ...]) -> dict[str, Any]:
    asset, token_id, maturity, short_market_id, short_venue, long_market_id, long_venue = identity
    return {
        "id": _structure_key(*identity),
        "asset": asset,
        "tokenId": token_id,
        "maturity": maturity,
        "shortMarketId": short_market_id,
        "shortVenue": short_venue,
        "longMarketId": long_market_id,
        "longVenue": long_venue,
    }


def _benchmark_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "timestamp": int(row["timestamp"]),
        "benchmarkLevel": row["benchmark_level"],
        "dteBucket": row["dte_bucket"],
        "percentile30d": _finite_float(row["percentile_30d"]),
        "percentile90d": _finite_float(row["percentile_90d"]),
        "percentileLifetime": _finite_float(row["percentile_lifetime"]),
        "sampleCount30d": int(row["sample_count_30d"]),
        "sampleCount90d": int(row["sample_count_90d"]),
        "sampleCountLifetime": int(row["sample_count_lifetime"]),
    }


def _raw_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    fully = bool(row["fully_executable"])
    return {
        "timestamp": int(row["timestamp"]),
        "fullyExecutable": fully,
        "invalidReason": row["invalid_reason"],
        "executableSpreadApr": _finite_float(row["executable_spread_apr"]) if fully else None,
        "topOfBookSpreadApr": _finite_float(row["top_of_book_spread_apr"]),
        "shortBidVwapApr": _finite_float(row["short_bid_vwap_apr"]),
        "longAskVwapApr": _finite_float(row["long_ask_vwap_apr"]),
        "shortImpactApr": _finite_float(row["short_impact_apr"]),
        "longImpactApr": _finite_float(row["long_impact_apr"]),
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "count": 0,
        "p50": None,
        "p75": None,
        "p90": None,
        "p95": None,
        "p99": None,
        "min": None,
        "max": None,
    }


def _stats_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return _empty_stats()
    return {
        "count": int(row["count"]),
        "p50": _finite_float(row["p50"]),
        "p75": _finite_float(row["p75"]),
        "p90": _finite_float(row["p90"]),
        "p95": _finite_float(row["p95"]),
        "p99": _finite_float(row["p99"]),
        "min": _finite_float(row["min_spread"]),
        "max": _finite_float(row["max_spread"]),
    }


def _create_read_only_views(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW market_identity AS
        SELECT
            market_id,
            MIN(token_id) AS token_id,
            MIN(venue) AS venue,
            MIN(asset) AS asset,
            MIN(maturity) AS maturity
        FROM markets
        GROUP BY market_id
        HAVING COUNT(DISTINCT token_id) = 1
           AND COUNT(DISTINCT venue) = 1
           AND COUNT(DISTINCT asset) = 1
           AND COUNT(DISTINCT maturity) = 1
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW valid_opportunities AS
        SELECT o.*
        FROM executable_opportunities o
        JOIN market_identity sm
          ON sm.market_id = o.short_market_id
         AND sm.token_id = o.token_id
         AND sm.venue = o.short_venue
         AND sm.asset = o.asset
         AND sm.maturity = o.maturity
        JOIN market_identity lm
          ON lm.market_id = o.long_market_id
         AND lm.token_id = o.token_id
         AND lm.venue = o.long_venue
         AND lm.asset = o.asset
         AND lm.maturity = o.maturity
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW valid_structure_notionals AS
        SELECT DISTINCT
            asset, token_id, maturity,
            short_market_id, short_venue, long_market_id, long_venue,
            notional_usd
        FROM valid_opportunities
        WHERE notional_usd IN (10000, 25000, 50000)
        """
    )


def _selected_structure_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        SELECT DISTINCT
            asset, token_id, maturity,
            short_market_id, short_venue, long_market_id, long_venue
        FROM valid_opportunities
        WHERE notional_usd IN (10000, 25000, 50000)
        ORDER BY asset, maturity, token_id,
                 short_market_id, short_venue, long_market_id, long_venue
        """,
    )


def _daily_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        SELECT
            asset, token_id, maturity,
            short_market_id, short_venue, long_market_id, long_venue,
            notional_usd,
            CAST(to_timestamp(timestamp) AT TIME ZONE 'UTC' AS DATE) AS utc_date,
            COUNT(*) AS observation_count,
            COUNT(*) FILTER (WHERE fully_executable) AS fully_executable_count,
            MEDIAN(executable_spread_apr)
                FILTER (WHERE fully_executable AND executable_spread_apr IS NOT NULL)
                AS daily_median_executable_spread_apr,
            MAX(executable_spread_apr)
                FILTER (WHERE fully_executable AND executable_spread_apr IS NOT NULL)
                AS daily_max_executable_spread_apr
        FROM valid_opportunities
        WHERE notional_usd IN (10000, 25000, 50000)
        GROUP BY ALL
        ORDER BY asset, maturity, token_id,
                 short_market_id, short_venue, long_market_id, long_venue,
                 notional_usd, utc_date
        """,
    )


def _benchmark_daily_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        WITH ranked AS (
            SELECT
                h.*,
                CAST(to_timestamp(h.timestamp) AT TIME ZONE 'UTC' AS DATE) AS utc_date,
                ROW_NUMBER() OVER (
                    PARTITION BY h.asset, h.token_id, h.maturity,
                        h.short_market_id, h.short_venue,
                        h.long_market_id, h.long_venue,
                        h.notional_usd,
                        CAST(to_timestamp(h.timestamp) AT TIME ZONE 'UTC' AS DATE)
                    ORDER BY h.timestamp DESC
                ) AS row_number
            FROM historical_benchmarks h
            JOIN valid_structure_notionals v
              ON v.asset = h.asset
             AND v.token_id = h.token_id
             AND v.maturity = h.maturity
             AND v.short_market_id = h.short_market_id
             AND v.short_venue = h.short_venue
             AND v.long_market_id = h.long_market_id
             AND v.long_venue = h.long_venue
             AND v.notional_usd = h.notional_usd
            WHERE h.notional_usd IN (10000, 25000, 50000)
        )
        SELECT * EXCLUDE (row_number)
        FROM ranked
        WHERE row_number = 1
        ORDER BY asset, maturity, token_id,
                 short_market_id, short_venue, long_market_id, long_venue,
                 notional_usd, utc_date
        """,
    )


def _latest_rows(connection: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    if table == "valid_opportunities":
        columns = """
            timestamp, asset, maturity, dte_days, token_id,
            short_market_id, short_venue, long_market_id, long_venue,
            notional_usd, short_bid_vwap_apr, long_ask_vwap_apr,
            executable_spread_apr, short_top_bid_apr, long_top_ask_apr,
            top_of_book_spread_apr, short_impact_apr, long_impact_apr,
            fully_executable, invalid_reason
        """
    else:
        columns = """
            timestamp, asset, maturity, dte_days, dte_bucket, token_id,
            short_market_id, short_venue, long_market_id, long_venue,
            notional_usd, executable_spread_apr, benchmark_level,
            percentile_30d, percentile_90d, percentile_lifetime,
            sample_count_30d, sample_count_90d, sample_count_lifetime
        """
    return _query_dicts(
        connection,
        f"""
        WITH ranked AS (
            SELECT {columns},
                ROW_NUMBER() OVER (
                    PARTITION BY asset, token_id, maturity,
                        short_market_id, short_venue,
                        long_market_id, long_venue, notional_usd
                    ORDER BY timestamp DESC
                ) AS row_number
            FROM {table}
            WHERE notional_usd IN (10000, 25000, 50000)
        )
        SELECT * EXCLUDE (row_number)
        FROM ranked
        WHERE row_number = 1
        ORDER BY asset, maturity, token_id,
                 short_market_id, short_venue, long_market_id, long_venue,
                 notional_usd
        """,
    )


def _common_timestamp_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        WITH common AS (
            SELECT
                asset, token_id, maturity,
                short_market_id, short_venue, long_market_id, long_venue,
                timestamp
            FROM valid_opportunities
            WHERE notional_usd IN (10000, 25000, 50000)
            GROUP BY ALL
            HAVING COUNT(DISTINCT notional_usd) = 3
        )
        SELECT
            asset, token_id, maturity,
            short_market_id, short_venue, long_market_id, long_venue,
            MAX(timestamp) AS common_timestamp
        FROM common
        GROUP BY ALL
        ORDER BY asset, maturity, token_id,
                 short_market_id, short_venue, long_market_id, long_venue
        """,
    )


def _common_observation_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        WITH common AS (
            SELECT
                asset, token_id, maturity,
                short_market_id, short_venue, long_market_id, long_venue,
                timestamp
            FROM valid_opportunities
            WHERE notional_usd IN (10000, 25000, 50000)
            GROUP BY ALL
            HAVING COUNT(DISTINCT notional_usd) = 3
        ), latest AS (
            SELECT asset, token_id, maturity,
                short_market_id, short_venue, long_market_id, long_venue,
                MAX(timestamp) AS common_timestamp
            FROM common
            GROUP BY ALL
        )
        SELECT
            o.timestamp, o.asset, o.maturity, o.dte_days, o.token_id,
            o.short_market_id, o.short_venue, o.long_market_id, o.long_venue,
            o.notional_usd, o.fully_executable, o.invalid_reason,
            o.executable_spread_apr,
            h.percentile_90d, h.sample_count_90d
        FROM valid_opportunities o
        JOIN latest l
          ON l.asset = o.asset AND l.token_id = o.token_id
         AND l.maturity = o.maturity
         AND l.short_market_id = o.short_market_id
         AND l.short_venue = o.short_venue
         AND l.long_market_id = o.long_market_id
         AND l.long_venue = o.long_venue
         AND l.common_timestamp = o.timestamp
        LEFT JOIN historical_benchmarks h
          ON h.timestamp = o.timestamp AND h.asset = o.asset
         AND h.token_id = o.token_id AND h.maturity = o.maturity
         AND h.short_market_id = o.short_market_id
         AND h.short_venue = o.short_venue
         AND h.long_market_id = o.long_market_id
         AND h.long_venue = o.long_venue
         AND h.notional_usd = o.notional_usd
        WHERE o.notional_usd IN (10000, 25000, 50000)
        ORDER BY o.asset, o.maturity, o.token_id,
                 o.short_market_id, o.short_venue, o.long_market_id, o.long_venue,
                 o.notional_usd
        """,
    )


def _distribution_queries(connection: duckdb.DuckDBPyConnection, selected: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection.execute("DROP TABLE IF EXISTS selected_benchmarks")
    connection.execute(
        """
        CREATE TEMP TABLE selected_benchmarks (
            structure_key VARCHAR,
            asset VARCHAR,
            token_id BIGINT,
            short_venue VARCHAR,
            long_venue VARCHAR,
            notional_usd DOUBLE,
            benchmark_level VARCHAR,
            dte_bucket VARCHAR,
            asof_timestamp BIGINT
        )
        """
    )
    rows = [
        (
            item["structure_key"], item["asset"], item["token_id"],
            item["short_venue"], item["long_venue"], item["notional_usd"],
            item["benchmark_level"], item["dte_bucket"], item["asof_timestamp"],
        )
        for item in selected
        if item["benchmark_level"] in {"dte", "pair"}
    ]
    if rows:
        connection.executemany("INSERT INTO selected_benchmarks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    dte_expression = _dte_bucket_sql("o.dte_days")
    matched = f"""
        SELECT
            s.structure_key, s.notional_usd, o.timestamp,
            o.executable_spread_apr
        FROM selected_benchmarks s
        JOIN valid_opportunities o
          ON o.asset = s.asset
         AND o.token_id = s.token_id
         AND o.short_venue = s.short_venue
         AND o.long_venue = s.long_venue
         AND o.notional_usd = s.notional_usd
         AND o.timestamp <= s.asof_timestamp
         AND o.fully_executable
         AND o.executable_spread_apr IS NOT NULL
         AND (
             s.benchmark_level = 'pair'
             OR {dte_expression} = s.dte_bucket
         )
    """
    stats = _query_dicts(
        connection,
        f"""
        WITH matched AS (
            SELECT structure_key, notional_usd, executable_spread_apr, 'lifetime' AS window_name
            FROM ({matched}) lifetime_rows
            UNION ALL
            SELECT structure_key, notional_usd, executable_spread_apr, '90d' AS window_name
            FROM ({matched}) recent_rows
            JOIN selected_benchmarks s USING (structure_key, notional_usd)
            WHERE recent_rows.timestamp >= s.asof_timestamp - {WINDOW_90D_SECONDS}
        )
        SELECT
            structure_key, notional_usd, window_name,
            COUNT(*) AS count,
            QUANTILE_CONT(executable_spread_apr, 0.50) AS p50,
            QUANTILE_CONT(executable_spread_apr, 0.75) AS p75,
            QUANTILE_CONT(executable_spread_apr, 0.90) AS p90,
            QUANTILE_CONT(executable_spread_apr, 0.95) AS p95,
            QUANTILE_CONT(executable_spread_apr, 0.99) AS p99,
            MIN(executable_spread_apr) AS min_spread,
            MAX(executable_spread_apr) AS max_spread
        FROM matched
        GROUP BY structure_key, notional_usd, window_name
        ORDER BY structure_key, notional_usd, window_name
        """,
    )
    histogram = _query_dicts(
        connection,
        f"""
        WITH matched AS ({matched}),
        recent AS (
            SELECT m.structure_key, m.notional_usd, m.executable_spread_apr
            FROM matched m
            JOIN selected_benchmarks s USING (structure_key, notional_usd)
            WHERE m.timestamp >= s.asof_timestamp - {WINDOW_90D_SECONDS}
        ), extrema AS (
            SELECT structure_key, notional_usd,
                MIN(executable_spread_apr) AS min_spread,
                MAX(executable_spread_apr) AS max_spread
            FROM recent
            GROUP BY structure_key, notional_usd
        )
        SELECT
            r.structure_key, r.notional_usd,
            CASE WHEN e.max_spread = e.min_spread THEN 0
                 ELSE CAST(LEAST({HISTOGRAM_BINS - 1}, GREATEST(0, FLOOR(
                    ((r.executable_spread_apr - e.min_spread)
                    / (e.max_spread - e.min_spread)) * {HISTOGRAM_BINS}
                 ))) AS INTEGER)
            END AS bin,
            COUNT(*) AS count
        FROM recent r
        JOIN extrema e USING (structure_key, notional_usd)
        GROUP BY r.structure_key, r.notional_usd, bin
        ORDER BY r.structure_key, r.notional_usd, bin
        """,
    )
    return stats, histogram


def _distribution_payload(
    benchmark: dict[str, Any] | None,
    stats_by_key: dict[tuple[str, float, str], dict[str, Any]],
    histogram_by_key: dict[tuple[str, float], dict[int, int]],
) -> dict[str, Any] | None:
    if benchmark is None or benchmark["benchmarkLevel"] not in {"dte", "pair"}:
        return None
    key = (benchmark["structureKey"], benchmark["notionalUsd"])
    counts = [0] * HISTOGRAM_BINS
    for index, count in histogram_by_key.get(key, {}).items():
        counts[index] = count
    return {
        "asOfTimestamp": benchmark["timestamp"],
        "benchmarkLevel": benchmark["benchmarkLevel"],
        "dteBucket": benchmark["dteBucket"],
        "windows": {
            "90d": _stats_payload(stats_by_key.get((*key, "90d"))),
            "lifetime": _stats_payload(stats_by_key.get((*key, "lifetime"))),
        },
        "histogram90d": {
            "min": _stats_payload(stats_by_key.get((*key, "90d")))["min"],
            "max": _stats_payload(stats_by_key.get((*key, "90d")))["max"],
            "binCount": HISTOGRAM_BINS,
            "counts": counts,
        },
    }


def build_payload(database_path: Path = DEFAULT_DATABASE, *, generated_at: str | None = None) -> dict[str, Any]:
    """Build the browser payload using a read-only DuckDB connection."""
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        _create_read_only_views(connection)
        raw_structures = _query_dicts(
            connection,
            """
            SELECT DISTINCT asset, token_id, maturity,
                short_market_id, short_venue, long_market_id, long_venue
            FROM executable_opportunities
            WHERE notional_usd IN (10000, 25000, 50000)
            """,
        )
        structure_rows = _selected_structure_rows(connection)
        structures_by_key = {
            _key_from_row(row): _structure_payload(_identity_from_row(row))
            for row in structure_rows
        }
        for structure in structures_by_key.values():
            structure["notionals"] = {
                str(notional): {
                    "daily": [],
                    "latestRaw": None,
                    "latestBenchmark": None,
                    "distribution": None,
                }
                for notional in NOTIONALS
            }

        daily_benchmark_map: dict[tuple[str, float, str], dict[str, Any]] = {}
        for row in _benchmark_daily_rows(connection):
            key = _key_from_row(row)
            daily_benchmark_map[(key, float(row["notional_usd"]), _date_string(row["utc_date"]))] = row

        daily_point_count = 0
        for row in _daily_rows(connection):
            key = _key_from_row(row)
            if key not in structures_by_key:
                continue
            notional = float(row["notional_usd"])
            date_value = _date_string(row["utc_date"])
            benchmark_row = daily_benchmark_map.get((key, notional, date_value))
            observation_count = int(row["observation_count"])
            daily = {
                "date": date_value,
                "observationCount": observation_count,
                "fullyExecutableCount": int(row["fully_executable_count"]),
                "fullyExecutableRate": (
                    int(row["fully_executable_count"]) / observation_count
                    if observation_count
                    else None
                ),
                "dailyMedianExecutableSpreadApr": _finite_float(row["daily_median_executable_spread_apr"]),
                "dailyMaxExecutableSpreadApr": _finite_float(row["daily_max_executable_spread_apr"]),
                "benchmark": _benchmark_payload(benchmark_row),
            }
            structures_by_key[key]["notionals"][str(int(notional))]["daily"].append(daily)
            daily_point_count += 1

        latest_raw_by_key: dict[tuple[str, float], dict[str, Any]] = {}
        for row in _latest_rows(connection, "valid_opportunities"):
            key = _key_from_row(row)
            if key in structures_by_key:
                latest_raw_by_key[(key, float(row["notional_usd"]))] = row
                structures_by_key[key]["notionals"][str(int(row["notional_usd"]))]["latestRaw"] = _raw_payload(row)

        latest_benchmark_by_key: dict[tuple[str, float], dict[str, Any]] = {}
        selected_for_distribution = []
        for row in _latest_rows(connection, "historical_benchmarks"):
            key = _key_from_row(row)
            if key not in structures_by_key:
                continue
            notional = float(row["notional_usd"])
            benchmark = _benchmark_payload(row)
            assert benchmark is not None
            benchmark["structureKey"] = key
            benchmark["notionalUsd"] = int(notional)
            latest_benchmark_by_key[(key, notional)] = row
            selected_for_distribution.append(
                {
                    "structure_key": key,
                    "asset": row["asset"],
                    "token_id": int(row["token_id"]),
                    "short_venue": row["short_venue"],
                    "long_venue": row["long_venue"],
                    "notional_usd": notional,
                    "benchmark_level": row["benchmark_level"],
                    "dte_bucket": row["dte_bucket"],
                    "asof_timestamp": int(row["timestamp"]),
                }
            )
            structures_by_key[key]["notionals"][str(int(notional))]["latestBenchmark"] = _benchmark_payload(row)

        stats_rows, histogram_rows = _distribution_queries(connection, selected_for_distribution)
        stats_by_key = {
            (row["structure_key"], float(row["notional_usd"]), row["window_name"]): row
            for row in stats_rows
        }
        histogram_by_key: dict[tuple[str, float], dict[int, int]] = defaultdict(dict)
        for row in histogram_rows:
            histogram_by_key[(row["structure_key"], float(row["notional_usd"]))][int(row["bin"])] = int(row["count"])

        for key, structure in structures_by_key.items():
            for notional in NOTIONALS:
                benchmark_row = latest_benchmark_by_key.get((key, float(notional)))
                benchmark = _benchmark_payload(benchmark_row)
                if benchmark is not None:
                    benchmark["structureKey"] = key
                    benchmark["notionalUsd"] = notional
                structure["notionals"][str(notional)]["distribution"] = _distribution_payload(
                    benchmark, stats_by_key, histogram_by_key
                )

        common_rows = _common_timestamp_rows(connection)
        common_by_key = {_key_from_row(row): int(row["common_timestamp"]) for row in common_rows}
        common_observations = _common_observation_rows(connection)
        comparison_by_key: dict[str, dict[float, dict[str, Any]]] = defaultdict(dict)
        for row in common_observations:
            comparison_by_key[_key_from_row(row)][float(row["notional_usd"])] = {
                "notionalUsd": int(row["notional_usd"]),
                "available": True,
                "fullyExecutable": bool(row["fully_executable"]),
                "invalidReason": row["invalid_reason"],
                "executableSpreadApr": (
                    _finite_float(row["executable_spread_apr"])
                    if row["fully_executable"]
                    else None
                ),
                "percentile90d": _finite_float(row["percentile_90d"]),
                "sampleCount90d": _int_or_none(row["sample_count_90d"]),
            }

        leaderboard = []
        for key, structure in structures_by_key.items():
            common_timestamp = common_by_key.get(key)
            structure["commonTimestamp"] = common_timestamp
            comparison = []
            for notional in NOTIONALS:
                if common_timestamp is None:
                    comparison.append({"notionalUsd": notional, "available": False})
                else:
                    comparison.append(
                        comparison_by_key.get(key, {}).get(
                            float(notional),
                            {"notionalUsd": notional, "available": False},
                        )
                    )
                raw = latest_raw_by_key.get((key, float(notional)))
                benchmark = latest_benchmark_by_key.get((key, float(notional)))
                if raw is None:
                    continue
                latest_benchmark = _benchmark_payload(benchmark)
                leaderboard.append(
                    {
                        "structureId": key,
                        "asset": structure["asset"],
                        "tokenId": structure["tokenId"],
                        "maturity": structure["maturity"],
                        "dteDays": int(raw["dte_days"]),
                        "dteBucket": _dte_bucket(
                            None if raw["dte_days"] is None else int(raw["dte_days"])
                        ),
                        "shortVenue": structure["shortVenue"],
                        "longVenue": structure["longVenue"],
                        "notionalUsd": notional,
                        "executableSpreadApr": _raw_payload(raw)["executableSpreadApr"],
                        "percentile90d": None if latest_benchmark is None else latest_benchmark["percentile90d"],
                        "sampleCount90d": None if latest_benchmark is None else latest_benchmark["sampleCount90d"],
                        "fullyExecutable": bool(raw["fully_executable"]),
                        "executionStatus": "fully executable" if raw["fully_executable"] else "not fully executable",
                    }
                )
            structure["notionalComparison"] = comparison

        leaderboard.sort(
            key=lambda row: (
                row["percentile90d"] is None,
                -(row["percentile90d"] or 0),
                row["asset"], row["maturity"], row["shortVenue"],
                row["longVenue"], row["notionalUsd"],
            )
        )
        max_timestamp, min_timestamp = connection.execute(
            "SELECT MAX(timestamp), MIN(timestamp) FROM valid_opportunities WHERE notional_usd IN (10000, 25000, 50000)"
        ).fetchone()
        historical_max_timestamp = None if max_timestamp is None else int(max_timestamp)
        date_min = None if min_timestamp is None else _iso_timestamp(int(min_timestamp))[:10]
        date_max = None if max_timestamp is None else _iso_timestamp(int(max_timestamp))[:10]
        diagnostics = {
            "rawStructureCount": len({_key_from_row(row) for row in raw_structures}),
            "validStructureCount": len(structures_by_key),
            "excludedStructureCount": max(0, len({_key_from_row(row) for row in raw_structures}) - len(structures_by_key)),
            "dailyPointCount": daily_point_count,
            "distributionCohortCount": sum(
                1
                for structure in structures_by_key.values()
                for item in structure["notionals"].values()
                if item["distribution"] is not None
            ),
            "leaderboardRowCount": len(leaderboard),
        }
        return {
            "schemaVersion": 1,
            "generatedAt": generated_at or _iso_timestamp(historical_max_timestamp),
            "historicalMaxTimestamp": historical_max_timestamp,
            "dateMin": date_min,
            "dateMax": date_max,
            "notionals": list(NOTIONALS),
            "structureCount": len(structures_by_key),
            "structures": list(structures_by_key.values()),
            "leaderboard": leaderboard,
            "diagnostics": diagnostics,
        }
    finally:
        connection.close()


def write_site_data(database_path: Path = DEFAULT_DATABASE, output_path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    payload = build_payload(database_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False)
    output_path.write_text(encoded, encoding="utf-8")
    print(
        f"Wrote {output_path} ({len(encoded.encode('utf-8'))} bytes, "
        f"{payload['structureCount']} structures, "
        f"{payload['diagnostics']['dailyPointCount']} daily points)"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_site_data(args.database, args.output)


if __name__ == "__main__":
    main()
