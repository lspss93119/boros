from __future__ import annotations

from datetime import date

import pytest
import duckdb

from boros_research.benchmark import (
    build_historical_benchmarks,
    calculate_benchmarks,
    calculate_persistence_episodes,
    current_persistence_status,
)
from boros_research.storage import build_duckdb_catalog, write_dataset


BASE = 1_800_000_000


def opportunity(
    timestamp: int,
    spread: float,
    *,
    asset: str = "HYPE",
    short_venue: str = "HYPERLIQUID",
    long_venue: str = "BYBIT",
    token_id: int = 3,
    notional_usd: float = 2_000.0,
    dte_days: int = 32,
    fully_executable: bool = True,
    market_offset: int = 0,
) -> dict:
    return {
        "timestamp": timestamp,
        "asset": asset,
        "maturity": date(2026, 9, 25),
        "dte_days": dte_days,
        "token_id": token_id,
        "short_market_id": 100 + market_offset,
        "short_venue": short_venue,
        "long_market_id": 200 + market_offset,
        "long_venue": long_venue,
        "notional_usd": notional_usd,
        "executable_spread_apr": spread,
        "fully_executable": fully_executable,
    }


def row_for(rows: tuple[dict, ...] | list[dict], timestamp: int) -> dict:
    return next(row for row in rows if row["timestamp"] == timestamp)


def test_future_data_never_changes_current_percentile():
    base_rows = [
        opportunity(BASE, 0.01),
        opportunity(BASE + 300, 0.02),
    ]
    with_future = base_rows + [opportunity(BASE + 600, 9.0)]

    before = row_for(calculate_benchmarks(base_rows, min_samples=1), BASE + 300)
    after = row_for(calculate_benchmarks(with_future, min_samples=1), BASE + 300)

    assert after["percentile_lifetime"] == before["percentile_lifetime"]
    assert after["sample_count_lifetime"] == before["sample_count_lifetime"] == 2


def test_forward_and_reverse_venue_cohorts_are_separate():
    rows = [
        opportunity(BASE, 0.10),
        opportunity(
            BASE,
            0.20,
            short_venue="BYBIT",
            long_venue="HYPERLIQUID",
        ),
    ]

    result = calculate_benchmarks(rows, min_samples=1)

    assert {(row["short_venue"], row["long_venue"]) for row in result} == {
        ("HYPERLIQUID", "BYBIT"),
        ("BYBIT", "HYPERLIQUID"),
    }
    assert all(row["sample_count_lifetime"] == 1 for row in result)


def test_notionals_and_dte_buckets_are_independent():
    rows = [
        opportunity(BASE, 0.10, notional_usd=1_000.0, dte_days=7),
        opportunity(BASE, 0.20, notional_usd=2_000.0, dte_days=8),
        opportunity(BASE, 0.30, notional_usd=2_000.0, dte_days=22),
    ]

    result = calculate_benchmarks(rows, min_samples=1)

    assert {(row["notional_usd"], row["dte_bucket"]) for row in result} == {
        (1_000.0, "0-7"),
        (2_000.0, "8-21"),
        (2_000.0, "22-45"),
    }
    assert all(row["benchmark_level"] == "dte" for row in result)


def test_primary_dte_cohort_falls_back_to_pair_after_minimum_is_not_met():
    rows = [
        opportunity(BASE + 15_000 + index * 300, 0.01 + index / 10_000, dte_days=30)
        for index in range(10)
    ]
    rows.extend(
        opportunity(BASE + index * 300, 0.20 + index / 10_000, dte_days=60)
        for index in range(50)
    )

    result = calculate_benchmarks(rows, min_samples=50)
    current = row_for(result, BASE + 15_000)

    assert current["benchmark_level"] == "pair"
    assert current["sample_count_lifetime"] == 51
    assert current["percentile_lifetime"] is not None


def test_insufficient_primary_and_pair_cohorts_return_null_percentiles():
    rows = [opportunity(BASE + index * 300, 0.01 + index / 10_000, dte_days=30) for index in range(4)]

    current = row_for(calculate_benchmarks(rows, min_samples=5), BASE + 900)

    assert current["benchmark_level"] == "insufficient"
    assert current["sample_count_lifetime"] == 4
    assert current["percentile_30d"] is None
    assert current["percentile_90d"] is None
    assert current["percentile_lifetime"] is None


def test_rolling_windows_and_lifetime_use_only_inclusive_prior_range():
    old = BASE - 31 * 86400
    rows = [
        opportunity(old, 0.01),
        opportunity(BASE - 10 * 86400, 0.02),
        opportunity(BASE, 0.03),
    ]

    current = row_for(calculate_benchmarks(rows, min_samples=1), BASE)

    assert current["sample_count_30d"] == 2
    assert current["sample_count_90d"] == 3
    assert current["sample_count_lifetime"] == 3
    assert current["percentile_30d"] == pytest.approx(100.0)
    assert current["percentile_90d"] == pytest.approx(100.0)


def test_percentile_is_monotonic_for_monotonic_spreads():
    rows = [opportunity(BASE + index * 300, 0.01 + index / 100) for index in range(4)]

    result = calculate_benchmarks(rows, min_samples=1)
    percentiles = [row["percentile_lifetime"] for row in result]

    assert percentiles == sorted(percentiles)


def benchmark_row(
    timestamp: int,
    percentile: float | None,
    spread: float | None,
    *,
    fully_executable: bool = True,
    dte_bucket: str = "22-45",
) -> dict:
    row = opportunity(timestamp, spread or 0.0, fully_executable=fully_executable)
    row.update(
        {
            "dte_bucket": dte_bucket,
            "benchmark_level": "dte",
            "percentile_lifetime": percentile,
        }
    )
    if spread is None:
        row["executable_spread_apr"] = None
    return row


def test_p90_episode_continuity_and_p95_subset():
    rows = [
        benchmark_row(BASE, 92.0, 0.10),
        benchmark_row(BASE + 300, 96.0, 0.11),
        benchmark_row(BASE + 600, 91.0, 0.12),
    ]

    episodes = calculate_persistence_episodes(rows)

    p90 = [row for row in episodes if row["threshold_percentile"] == 90.0]
    p95 = [row for row in episodes if row["threshold_percentile"] == 95.0]
    assert len(p90) == 1
    assert p90[0]["observation_count"] == 3
    assert p90[0]["duration_minutes"] == 10
    assert p90[0]["peak_spread_apr"] == pytest.approx(0.12)
    assert len(p95) == 1
    assert p95[0]["observation_count"] == 1


@pytest.mark.parametrize(
    "rows",
    [
        [
            benchmark_row(BASE, 95.0, 0.10),
            benchmark_row(BASE + 300, None, None, fully_executable=False),
            benchmark_row(BASE + 600, 95.0, 0.12),
        ],
        [
            benchmark_row(BASE, 95.0, 0.10),
            benchmark_row(BASE + 600, 95.0, 0.12),
        ],
    ],
)
def test_persistence_episode_breaks_on_invalid_observation_or_time_gap(rows):
    episodes = calculate_persistence_episodes(rows)

    assert len(episodes) == 4
    assert all(row["observation_count"] == 1 for row in episodes)
    assert all(
        sum(row["threshold_percentile"] == threshold for row in episodes) == 2
        for threshold in (90.0, 95.0)
    )


def test_current_persistence_status_reports_duration_without_future_rows():
    rows = [
        benchmark_row(BASE, 95.0, 0.10),
        benchmark_row(BASE + 300, 95.0, 0.11),
        benchmark_row(BASE + 600, 80.0, 0.05),
        benchmark_row(BASE + 900, 99.0, 0.20),
    ]

    status = current_persistence_status(rows[:3], BASE + 300, threshold_percentile=90.0)

    assert status.above_threshold is True
    assert status.start_timestamp == BASE
    assert status.duration_minutes == 5
    assert status.observation_count == 2


def test_current_persistence_status_is_not_above_when_current_row_is_invalid():
    rows = [
        benchmark_row(BASE, 95.0, 0.10),
        benchmark_row(BASE + 300, None, None, fully_executable=False),
    ]

    status = current_persistence_status(rows, BASE + 300, threshold_percentile=90.0)

    assert status.above_threshold is False
    assert status.start_timestamp is None
    assert status.duration_minutes == 0


def test_benchmark_build_promotes_parquet_and_duckdb_views_atomically(tmp_path):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    source_rows = [
        opportunity(BASE, 0.10),
        opportunity(BASE + 300, 0.20),
        opportunity(BASE + 600, 0.20),
    ]
    write_dataset(source_rows, "executable_opportunities", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)

    result = build_historical_benchmarks(
        database_path=database_path,
        parquet_root=parquet_root,
        min_samples=2,
    )

    assert result.benchmark_row_count == 3
    assert result.episode_row_count == 2
    assert result.summary_row_count == 2
    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*), max(percentile_lifetime) FROM historical_benchmarks"
        ).fetchone() == (3, 100.0)
        assert connection.execute(
            """
            SELECT current_p90_above_threshold, current_p90_persistence_minutes
            FROM historical_benchmark_status
            WHERE timestamp = ?
            """,
            [BASE + 600],
        ).fetchone() == (True, 5)
