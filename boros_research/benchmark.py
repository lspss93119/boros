from __future__ import annotations

import bisect
import os
import shutil
import tempfile
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from .config import DUCKDB_PATH, PARQUET_DIR, SAMPLE_INTERVAL_SEC
from .storage import build_duckdb_catalog, write_dataset


MIN_SAMPLE_COUNT = 50
WINDOW_30D_SEC = 30 * 24 * 60 * 60
WINDOW_90D_SEC = 90 * 24 * 60 * 60
BENCHMARK_DATASET = "historical_benchmarks"
EPISODE_DATASET = "spread_episodes"
SUMMARY_DATASET = "persistence_summary"

_BENCHMARK_SOURCE_COLUMNS = (
    "timestamp",
    "asset",
    "maturity",
    "dte_days",
    "token_id",
    "short_market_id",
    "short_venue",
    "long_market_id",
    "long_venue",
    "notional_usd",
    "executable_spread_apr",
)

_BASE_KEY_COLUMNS = (
    "asset",
    "short_venue",
    "long_venue",
    "token_id",
    "notional_usd",
)


@dataclass(frozen=True)
class PersistenceStatus:
    threshold_percentile: float
    above_threshold: bool
    start_timestamp: int | None
    duration_minutes: int
    observation_count: int


@dataclass(frozen=True)
class BenchmarkBuildResult:
    benchmark_row_count: int
    episode_row_count: int
    summary_row_count: int
    dte_benchmark_row_count: int
    pair_fallback_row_count: int
    insufficient_benchmark_row_count: int
    elapsed_seconds: float

    @property
    def fallback_rate(self) -> float | None:
        if self.benchmark_row_count == 0:
            return None
        return self.pair_fallback_row_count / self.benchmark_row_count


def dte_bucket_for_days(dte_days: int) -> str:
    if isinstance(dte_days, bool) or not isinstance(dte_days, int):
        raise ValueError("dte_days must be an integer")
    if 0 <= dte_days <= 7:
        return "0-7"
    if 8 <= dte_days <= 21:
        return "8-21"
    if 22 <= dte_days <= 45:
        return "22-45"
    if 46 <= dte_days <= 90:
        return "46-90"
    if dte_days >= 91:
        return "91+"
    raise ValueError(f"negative dte_days cannot be benchmarked: {dte_days}")


def _require_timestamp(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("benchmark timestamp must be an integer Unix timestamp")
    return value


def _normalize_source_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not row.get("fully_executable", True):
        return None
    spread = row.get("executable_spread_apr")
    if spread is None:
        return None
    try:
        finite = bool(spread == spread and abs(float(spread)) != float("inf"))
    except (TypeError, ValueError) as exc:
        raise ValueError("executable_spread_apr must be a finite number") from exc
    if not finite:
        raise ValueError("executable_spread_apr must be a finite number")

    timestamp = _require_timestamp(row.get("timestamp"))
    dte_days = row.get("dte_days")
    dte_bucket = dte_bucket_for_days(dte_days)
    normalized = {column: row.get(column) for column in _BENCHMARK_SOURCE_COLUMNS}
    normalized["timestamp"] = timestamp
    normalized["dte_days"] = dte_days
    normalized["executable_spread_apr"] = float(spread)
    normalized["dte_bucket"] = dte_bucket
    return normalized


def _base_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in _BASE_KEY_COLUMNS)


class _Fenwick:
    def __init__(self, size: int):
        self._tree = [0] * (size + 1)

    def add(self, index: int, delta: int) -> None:
        cursor = index + 1
        while cursor < len(self._tree):
            self._tree[cursor] += delta
            cursor += cursor & -cursor

    def prefix(self, index: int) -> int:
        if index < 0:
            return 0
        cursor = min(index + 1, len(self._tree) - 1)
        total = 0
        while cursor:
            total += self._tree[cursor]
            cursor -= cursor & -cursor
        return total


class _RollingStats:
    """Causal rank/count state for one pair or DTE cohort."""

    def __init__(self, coordinates: Sequence[float]):
        self._coordinates = tuple(coordinates)
        self._trees = [_Fenwick(len(self._coordinates)) for _ in range(3)]
        self._window_queues = [deque(), deque()]
        self._counts = [0, 0, 0]

    def _index(self, value: float) -> int:
        index = bisect.bisect_left(self._coordinates, value)
        if index == len(self._coordinates) or self._coordinates[index] != value:
            raise ValueError(f"spread value is missing from cohort coordinates: {value}")
        return index

    def add(self, timestamp: int, value: float) -> None:
        index = self._index(value)
        for slot, tree in enumerate(self._trees):
            tree.add(index, 1)
            self._counts[slot] += 1
        self._window_queues[0].append((timestamp, index))
        self._window_queues[1].append((timestamp, index))

    def expire(self, timestamp: int) -> None:
        for slot, window in enumerate((WINDOW_30D_SEC, WINDOW_90D_SEC)):
            cutoff = timestamp - window
            queue = self._window_queues[slot]
            while queue and queue[0][0] < cutoff:
                _, index = queue.popleft()
                self._trees[slot].add(index, -1)
                self._counts[slot] -= 1

    def count(self, slot: int) -> int:
        return self._counts[slot]

    def percentile(self, value: float, slot: int, minimum: int) -> float | None:
        count = self._counts[slot]
        if count < minimum:
            return None
        right = bisect.bisect_right(self._coordinates, value) - 1
        less_or_equal = self._trees[slot].prefix(right)
        return 100.0 * less_or_equal / count


def _benchmark_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_samples: int,
) -> Iterator[dict[str, Any]]:
    if not rows:
        return
    coordinates = sorted({float(row["executable_spread_apr"]) for row in rows})
    pair_stats = _RollingStats(coordinates)
    dte_stats = {
        bucket: _RollingStats(coordinates)
        for bucket in sorted({str(row["dte_bucket"]) for row in rows})
    }
    ordered = sorted(
        rows,
        key=lambda row: (
            row["timestamp"],
            row.get("maturity") or date.min,
            row.get("dte_days", 0),
            row.get("short_market_id", 0),
            row.get("long_market_id", 0),
            row["executable_spread_apr"],
        ),
    )

    index = 0
    while index < len(ordered):
        timestamp = ordered[index]["timestamp"]
        end = index + 1
        while end < len(ordered) and ordered[end]["timestamp"] == timestamp:
            end += 1
        timestamp_rows = ordered[index:end]

        pair_stats.expire(timestamp)
        for stats in dte_stats.values():
            stats.expire(timestamp)
        for row in timestamp_rows:
            spread = float(row["executable_spread_apr"])
            pair_stats.add(timestamp, spread)
            dte_stats[row["dte_bucket"]].add(timestamp, spread)

        for row in timestamp_rows:
            dte = dte_stats[row["dte_bucket"]]
            if dte.count(2) >= min_samples:
                selected = dte
                level = "dte"
            elif pair_stats.count(2) >= min_samples:
                selected = pair_stats
                level = "pair"
            else:
                selected = None
                level = "insufficient"

            output = {
                column: row[column]
                for column in _BENCHMARK_SOURCE_COLUMNS
            }
            output["dte_bucket"] = row["dte_bucket"]
            output["benchmark_level"] = level
            if selected is None:
                output["percentile_30d"] = None
                output["percentile_90d"] = None
                output["percentile_lifetime"] = None
                output["sample_count_30d"] = pair_stats.count(0)
                output["sample_count_90d"] = pair_stats.count(1)
                output["sample_count_lifetime"] = pair_stats.count(2)
            else:
                output["percentile_30d"] = selected.percentile(
                    row["executable_spread_apr"], 0, min_samples
                )
                output["percentile_90d"] = selected.percentile(
                    row["executable_spread_apr"], 1, min_samples
                )
                output["percentile_lifetime"] = selected.percentile(
                    row["executable_spread_apr"], 2, min_samples
                )
                output["sample_count_30d"] = selected.count(0)
                output["sample_count_90d"] = selected.count(1)
                output["sample_count_lifetime"] = selected.count(2)
            yield output
        index = end


def calculate_benchmarks(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_samples: int = MIN_SAMPLE_COUNT,
) -> tuple[dict[str, Any], ...]:
    """Calculate causal benchmark rows for an iterable of opportunity rows.

    The implementation keeps all rows for one pair/asset/token/notional group
    at a time. Production builds obtain those groups from an ordered DuckDB
    cursor rather than loading the complete opportunity dataset into Python.
    """
    if min_samples <= 0:
        raise ValueError("min_samples must be positive")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = _normalize_source_row(row)
        if normalized is not None:
            groups[_base_key(normalized)].append(normalized)

    result: list[dict[str, Any]] = []
    for key in sorted(groups):
        result.extend(_benchmark_group(groups[key], min_samples=min_samples))
    return tuple(result)


def _source_query() -> str:
    columns = ", ".join(_BENCHMARK_SOURCE_COLUMNS)
    return (
        "SELECT "
        + columns
        + " FROM executable_opportunities "
        "WHERE fully_executable AND executable_spread_apr IS NOT NULL "
        "ORDER BY asset, short_venue, long_venue, token_id, notional_usd, "
        "timestamp, maturity, dte_days, short_market_id, long_market_id, "
        "executable_spread_apr"
    )


def _iter_source_groups(connection: duckdb.DuckDBPyConnection) -> Iterator[list[dict[str, Any]]]:
    cursor = connection.execute(_source_query())
    columns = list(_BENCHMARK_SOURCE_COLUMNS)
    current_key: tuple[Any, ...] | None = None
    current: list[dict[str, Any]] = []
    while True:
        batch = cursor.fetchmany(100_000)
        if not batch:
            break
        for values in batch:
            row = dict(zip(columns, values, strict=True))
            key = _base_key(row)
            if current_key is not None and key != current_key:
                yield current
                current = []
            current_key = key
            normalized = _normalize_source_row(row)
            if normalized is not None:
                current.append(normalized)
    if current:
        yield current


def _parquet_glob(path: Path) -> str:
    return (path.resolve() / "**" / "*.parquet").as_posix().replace("'", "''")


def _attach_source(connection: duckdb.DuckDBPyConnection, database_path: Path) -> None:
    escaped = database_path.resolve().as_posix().replace("'", "''")
    connection.execute(f"ATTACH '{escaped}' AS phase1 (READ_ONLY)")


def _query_rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> Iterator[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [description[0] for description in cursor.description]
    while True:
        batch = cursor.fetchmany(100_000)
        if not batch:
            return
        for values in batch:
            yield dict(zip(columns, values, strict=True))


def _episode_query(benchmark_path: Path) -> str:
    benchmark_relation = f"read_parquet('{_parquet_glob(benchmark_path)}', hive_partitioning=true, union_by_name=true)"
    return f"""
        WITH raw AS (
            SELECT
                timestamp,
                asset,
                dte_days,
                token_id,
                short_market_id,
                short_venue,
                long_market_id,
                long_venue,
                notional_usd,
                executable_spread_apr,
                fully_executable,
                CASE
                    WHEN dte_days BETWEEN 0 AND 7 THEN '0-7'
                    WHEN dte_days BETWEEN 8 AND 21 THEN '8-21'
                    WHEN dte_days BETWEEN 22 AND 45 THEN '22-45'
                    WHEN dte_days BETWEEN 46 AND 90 THEN '46-90'
                    WHEN dte_days >= 91 THEN '91+'
                END AS dte_bucket
            FROM phase1.executable_opportunities
            WHERE dte_days >= 0
        ),
        thresholds AS (
            SELECT CAST(90.0 AS DOUBLE) AS threshold_percentile
            UNION ALL
            SELECT CAST(95.0 AS DOUBLE) AS threshold_percentile
        ),
        expanded AS (
            SELECT
                r.*,
                t.threshold_percentile,
                CASE
                    WHEN r.fully_executable
                        AND b.percentile_lifetime >= t.threshold_percentile
                    THEN TRUE
                    ELSE FALSE
                END AS qualifies
            FROM raw r
            CROSS JOIN thresholds t
            LEFT JOIN {benchmark_relation} b
                ON b.timestamp = r.timestamp
                AND b.asset = r.asset
                AND b.dte_days = r.dte_days
                AND b.token_id = r.token_id
                AND b.short_market_id = r.short_market_id
                AND b.short_venue = r.short_venue
                AND b.long_market_id = r.long_market_id
                AND b.long_venue = r.long_venue
                AND b.notional_usd = r.notional_usd
                AND b.executable_spread_apr = r.executable_spread_apr
        ),
        cohort_time AS (
            SELECT
                asset,
                short_venue,
                long_venue,
                token_id,
                notional_usd,
                dte_bucket,
                threshold_percentile,
                timestamp,
                bool_or(qualifies) AS qualifies,
                max(executable_spread_apr) FILTER (WHERE qualifies) AS spread_apr
            FROM expanded
            GROUP BY
                asset, short_venue, long_venue, token_id, notional_usd,
                dte_bucket, threshold_percentile, timestamp
        ),
        ordered AS (
            SELECT
                *,
                lag(timestamp) OVER (
                    PARTITION BY asset, short_venue, long_venue, token_id,
                        notional_usd, dte_bucket, threshold_percentile
                    ORDER BY timestamp
                ) AS previous_timestamp,
                lag(qualifies) OVER (
                    PARTITION BY asset, short_venue, long_venue, token_id,
                        notional_usd, dte_bucket, threshold_percentile
                    ORDER BY timestamp
                ) AS previous_qualifies
            FROM cohort_time
        ),
        marked AS (
            SELECT
                *,
                CASE
                    WHEN qualifies
                        AND coalesce(previous_qualifies, FALSE)
                        AND timestamp = previous_timestamp + {SAMPLE_INTERVAL_SEC}
                    THEN 0
                    WHEN qualifies THEN 1
                    ELSE 0
                END AS new_episode
            FROM ordered
        ),
        numbered AS (
            SELECT
                *,
                sum(new_episode) OVER (
                    PARTITION BY asset, short_venue, long_venue, token_id,
                        notional_usd, dte_bucket, threshold_percentile
                    ORDER BY timestamp
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS episode_number
            FROM marked
        )
        SELECT
            asset,
            short_venue,
            long_venue,
            token_id,
            notional_usd,
            dte_bucket,
            threshold_percentile,
            min(timestamp) AS start_timestamp,
            max(timestamp) AS end_timestamp,
            CAST((max(timestamp) - min(timestamp)) / 60 AS BIGINT) AS duration_minutes,
            count(*) AS observation_count,
            max(spread_apr) AS peak_spread_apr,
            avg(spread_apr) AS mean_spread_apr
        FROM numbered
        WHERE qualifies
        GROUP BY
            asset, short_venue, long_venue, token_id, notional_usd,
            dte_bucket, threshold_percentile, episode_number
        ORDER BY
            asset, short_venue, long_venue, token_id, notional_usd,
            dte_bucket, threshold_percentile, start_timestamp
    """


def _summary_query(benchmark_path: Path, episode_path: Path) -> str:
    benchmark_relation = f"read_parquet('{_parquet_glob(benchmark_path)}', hive_partitioning=true, union_by_name=true)"
    episode_relation = f"read_parquet('{_parquet_glob(episode_path)}', hive_partitioning=true, union_by_name=true)"
    return f"""
        WITH cohorts AS (
            SELECT DISTINCT
                asset, short_venue, long_venue, token_id, notional_usd, dte_bucket
            FROM {benchmark_relation}
        ), thresholds AS (
            SELECT CAST(90.0 AS DOUBLE) AS threshold_percentile
            UNION ALL
            SELECT CAST(95.0 AS DOUBLE) AS threshold_percentile
        )
        SELECT
            c.asset,
            c.short_venue,
            c.long_venue,
            c.token_id,
            c.notional_usd,
            c.dte_bucket,
            t.threshold_percentile,
            count(e.start_timestamp) AS episode_count,
            quantile_cont(e.duration_minutes, 0.5) AS median_duration_minutes,
            quantile_cont(e.duration_minutes, 0.75) AS p75_duration_minutes
        FROM cohorts c
        CROSS JOIN thresholds t
        LEFT JOIN {episode_relation} e
            ON e.asset = c.asset
            AND e.short_venue = c.short_venue
            AND e.long_venue = c.long_venue
            AND e.token_id = c.token_id
            AND e.notional_usd = c.notional_usd
            AND e.dte_bucket = c.dte_bucket
            AND e.threshold_percentile = t.threshold_percentile
        GROUP BY
            c.asset, c.short_venue, c.long_venue, c.token_id,
            c.notional_usd, c.dte_bucket, t.threshold_percentile
        ORDER BY
            c.asset, c.short_venue, c.long_venue, c.token_id,
            c.notional_usd, c.dte_bucket, t.threshold_percentile
    """


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _promote_dataset_directories(
    stage_root: Path,
    parquet_root: Path,
    datasets: Sequence[str],
) -> tuple[list[Path], list[tuple[Path, Path]]]:
    parquet_root.mkdir(parents=True, exist_ok=True)
    backups: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    try:
        for dataset in datasets:
            staged = stage_root / dataset
            target = parquet_root / dataset
            backup = parquet_root / f".{dataset}.phase2-previous"
            if backup.exists():
                _remove_path(backup)
            if target.exists():
                target.replace(backup)
                backups.append((target, backup))
            staged.replace(target)
            promoted.append(target)
    except Exception:
        for target in promoted:
            if target.exists():
                _remove_path(target)
        for target, backup in reversed(backups):
            if backup.exists():
                backup.replace(target)
        raise
    else:
        return promoted, backups


def _rollback_dataset_promotion(
    promoted: Sequence[Path],
    backups: Sequence[tuple[Path, Path]],
) -> None:
    for target in promoted:
        if target.exists():
            _remove_path(target)
    for target, backup in reversed(backups):
        if backup.exists():
            backup.replace(target)


def _finalize_dataset_promotion(backups: Sequence[tuple[Path, Path]]) -> None:
    for _, backup in backups:
        if backup.exists():
            _remove_path(backup)


def _replace_catalog(parquet_root: Path, database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.stem}.phase2-",
        suffix=database_path.suffix,
        dir=database_path.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    backup = database_path.with_name(f".{database_path.name}.phase2-previous")
    try:
        build_duckdb_catalog(parquet_root, temporary)
        if backup.exists():
            _remove_path(backup)
        if database_path.exists():
            database_path.replace(backup)
        temporary.replace(database_path)
    except Exception:
        if temporary.exists():
            _remove_path(temporary)
        if database_path.exists() and backup.exists():
            _remove_path(database_path)
        if backup.exists() and not database_path.exists():
            backup.replace(database_path)
        raise
    else:
        if backup.exists():
            _remove_path(backup)


def _count_dataset_rows(database_path: Path, dataset: str) -> int:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return int(connection.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0])


def build_historical_benchmarks(
    database_path: Path = DUCKDB_PATH,
    parquet_root: Path = PARQUET_DIR,
    *,
    min_samples: int = MIN_SAMPLE_COUNT,
) -> BenchmarkBuildResult:
    """Build Phase 2 benchmark and persistence datasets from Phase 1 outputs."""
    if min_samples <= 0:
        raise ValueError("min_samples must be positive")
    database_path = Path(database_path)
    parquet_root = Path(parquet_root)
    if not database_path.exists():
        raise FileNotFoundError(f"Phase 1 DuckDB catalog does not exist: {database_path}")

    started = time.monotonic()
    parquet_root.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(
        tempfile.mkdtemp(prefix=".phase2-benchmark-", dir=parquet_root.parent)
    )
    datasets = (BENCHMARK_DATASET, EPISODE_DATASET, SUMMARY_DATASET)
    try:
        with duckdb.connect(str(database_path), read_only=True) as source:
            source_groups = _iter_source_groups(source)

            def benchmark_rows() -> Iterator[dict[str, Any]]:
                for group in source_groups:
                    yield from _benchmark_group(group, min_samples=min_samples)

            benchmark_stage = write_dataset(
                benchmark_rows(),
                BENCHMARK_DATASET,
                stage_root,
                batch_size=100_000,
            )

        with duckdb.connect(":memory:") as connection:
            _attach_source(connection, database_path)
            episode_stage = write_dataset(
                _query_rows(connection, _episode_query(benchmark_stage)),
                EPISODE_DATASET,
                stage_root,
                batch_size=100_000,
            )
            write_dataset(
                _query_rows(connection, _summary_query(benchmark_stage, episode_stage)),
                SUMMARY_DATASET,
                stage_root,
                batch_size=100_000,
            )

        promoted, backups = _promote_dataset_directories(
            stage_root, parquet_root, datasets
        )
        try:
            _replace_catalog(parquet_root, database_path)
        except Exception:
            _rollback_dataset_promotion(promoted, backups)
            raise
        else:
            _finalize_dataset_promotion(backups)

        benchmark_row_count = _count_dataset_rows(database_path, BENCHMARK_DATASET)
        episode_row_count = _count_dataset_rows(database_path, EPISODE_DATASET)
        summary_row_count = _count_dataset_rows(database_path, SUMMARY_DATASET)
        with duckdb.connect(str(database_path), read_only=True) as connection:
            dte_count, pair_count, insufficient_count = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE benchmark_level = 'dte'),
                    count(*) FILTER (WHERE benchmark_level = 'pair'),
                    count(*) FILTER (WHERE benchmark_level = 'insufficient')
                FROM historical_benchmarks
                """
            ).fetchone()
        return BenchmarkBuildResult(
            benchmark_row_count=benchmark_row_count,
            episode_row_count=episode_row_count,
            summary_row_count=summary_row_count,
            dte_benchmark_row_count=int(dte_count),
            pair_fallback_row_count=int(pair_count),
            insufficient_benchmark_row_count=int(insufficient_count),
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception:
        if stage_root.exists():
            _remove_path(stage_root)
        raise
    finally:
        if stage_root.exists():
            _remove_path(stage_root)


def _canonical_cohort_points(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_timestamp: int | None = None,
) -> dict[tuple[Any, ...], dict[int, dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], dict[int, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        timestamp = _require_timestamp(row.get("timestamp"))
        if target_timestamp is not None and timestamp > target_timestamp:
            continue
        dte_bucket = row.get("dte_bucket")
        if dte_bucket is None:
            dte_bucket = dte_bucket_for_days(row["dte_days"])
        key = (
            row.get("asset"),
            row.get("short_venue"),
            row.get("long_venue"),
            row.get("token_id"),
            row.get("notional_usd"),
            dte_bucket,
        )
        grouped[key][timestamp].append(row)

    output: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = {}
    for key, timestamp_rows in grouped.items():
        points: dict[int, dict[str, Any]] = {}
        for timestamp, candidates in timestamp_rows.items():
            qualifying = [
                row
                for row in candidates
                if row.get("fully_executable", True)
                and row.get("percentile_lifetime") is not None
                and row.get("executable_spread_apr") is not None
            ]
            spreads = [float(row["executable_spread_apr"]) for row in qualifying]
            points[timestamp] = {
                "timestamp": timestamp,
                "qualifying": qualifying,
                "spread_apr": max(spreads) if spreads else None,
            }
        output[key] = points
    return output


def calculate_persistence_episodes(
    rows: Iterable[Mapping[str, Any]],
    *,
    thresholds: Sequence[float] = (90.0, 95.0),
    sample_interval_sec: int = SAMPLE_INTERVAL_SEC,
) -> tuple[dict[str, Any], ...]:
    """Build deterministic p90/p95 episodes from benchmark observations.

    Multiple source observations in one cohort at one grid timestamp collapse
    to one canonical point. A point qualifies when any fully executable source
    observation has a lifetime percentile at or above the threshold; its
    spread is the maximum qualifying spread at that timestamp.
    """
    if sample_interval_sec <= 0:
        raise ValueError("sample_interval_sec must be positive")
    points_by_group = _canonical_cohort_points(rows)
    output: list[dict[str, Any]] = []
    for key in sorted(points_by_group):
        asset, short_venue, long_venue, token_id, notional_usd, dte_bucket = key
        points = points_by_group[key]
        ordered_points = [points[timestamp] for timestamp in sorted(points)]
        for threshold in thresholds:
            active: list[dict[str, Any]] = []
            previous_timestamp: int | None = None

            def flush() -> None:
                if not active:
                    return
                start = active[0]["timestamp"]
                end = active[-1]["timestamp"]
                spreads = [point["spread_apr"] for point in active]
                output.append(
                    {
                        "asset": asset,
                        "short_venue": short_venue,
                        "long_venue": long_venue,
                        "token_id": token_id,
                        "notional_usd": notional_usd,
                        "dte_bucket": dte_bucket,
                        "threshold_percentile": float(threshold),
                        "start_timestamp": start,
                        "end_timestamp": end,
                        "duration_minutes": (end - start) // 60,
                        "observation_count": len(active),
                        "peak_spread_apr": max(spreads),
                        "mean_spread_apr": sum(spreads) / len(spreads),
                    }
                )

            for point in ordered_points:
                timestamp = point["timestamp"]
                qualifies = any(
                    float(row["percentile_lifetime"]) >= threshold
                    for row in point["qualifying"]
                )
                if qualifies and previous_timestamp is not None and (
                    timestamp == previous_timestamp + sample_interval_sec
                ):
                    active.append(
                        {"timestamp": timestamp, "spread_apr": point["spread_apr"]}
                    )
                elif qualifies:
                    flush()
                    active = [{"timestamp": timestamp, "spread_apr": point["spread_apr"]}]
                else:
                    flush()
                    active = []
                previous_timestamp = timestamp
            flush()
    return tuple(
        sorted(
            output,
            key=lambda row: (
                row["asset"],
                row["short_venue"],
                row["long_venue"],
                row["token_id"],
                row["notional_usd"],
                row["dte_bucket"],
                row["threshold_percentile"],
                row["start_timestamp"],
            ),
        )
    )


def current_persistence_status(
    rows: Iterable[Mapping[str, Any]],
    target_timestamp: int,
    *,
    threshold_percentile: float,
    sample_interval_sec: int = SAMPLE_INTERVAL_SEC,
) -> PersistenceStatus:
    """Return the current p90/p95 episode duration without future data."""
    points_by_group = _canonical_cohort_points(rows, target_timestamp=target_timestamp)
    matching = [
        points
        for key, points in points_by_group.items()
        if any(point["timestamp"] == target_timestamp for point in points.values())
    ]
    if len(matching) != 1:
        return PersistenceStatus(
            threshold_percentile=float(threshold_percentile),
            above_threshold=False,
            start_timestamp=None,
            duration_minutes=0,
            observation_count=0,
        )
    points = matching[0]
    ordered_timestamps = sorted(points)
    current = points[target_timestamp]
    if not any(
        float(row["percentile_lifetime"]) >= threshold_percentile
        for row in current["qualifying"]
    ):
        return PersistenceStatus(
            threshold_percentile=float(threshold_percentile),
            above_threshold=False,
            start_timestamp=None,
            duration_minutes=0,
            observation_count=0,
        )

    position = ordered_timestamps.index(target_timestamp)
    start_position = position
    while start_position > 0:
        previous = points[ordered_timestamps[start_position - 1]]
        current_timestamp = ordered_timestamps[start_position]
        previous_timestamp = ordered_timestamps[start_position - 1]
        previous_qualifies = any(
            float(row["percentile_lifetime"]) >= threshold_percentile
            for row in previous["qualifying"]
        )
        if (
            not previous_qualifies
            or current_timestamp - previous_timestamp != sample_interval_sec
        ):
            break
        start_position -= 1

    start_timestamp = ordered_timestamps[start_position]
    return PersistenceStatus(
        threshold_percentile=float(threshold_percentile),
        above_threshold=True,
        start_timestamp=start_timestamp,
        duration_minutes=(target_timestamp - start_timestamp) // 60,
        observation_count=position - start_position + 1,
    )
