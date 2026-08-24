from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from boros_research.live_benchmark import (
    HistoricalBenchmarkLookup,
    LiveBenchmarkCandidate,
    canonical_live_asset,
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
    notional_usd: float = 2_000,
    dte_days: int = 32,
) -> dict:
    return {
        "timestamp": timestamp,
        "asset": asset,
        "maturity": date(2026, 9, 25),
        "dte_days": dte_days,
        "token_id": token_id,
        "short_market_id": 190,
        "short_venue": short_venue,
        "long_market_id": 192,
        "long_venue": long_venue,
        "notional_usd": notional_usd,
        "executable_spread_apr": spread,
        "fully_executable": True,
    }


def candidate(
    spread: float,
    *,
    asset: str = "HYPE",
    short_venue: str = "HYPERLIQUID",
    long_venue: str = "BYBIT",
    token_id: int = 3,
    notional_usd: float = 2_000,
    maturity: date = date(2027, 2, 26),
    timestamp: int = BASE + 10 * 86400,
    dte_bucket: str = "22-45",
) -> LiveBenchmarkCandidate:
    return LiveBenchmarkCandidate(
        candidate_id=f"{asset}-{short_venue}-{long_venue}-{notional_usd}",
        asset=asset,
        short_venue=short_venue,
        long_venue=long_venue,
        token_id=token_id,
        notional_usd=notional_usd,
        maturity=maturity,
        dte_bucket=dte_bucket,
        timestamp=timestamp,
        spread_apr=spread,
    )


def make_lookup(tmp_path, rows, *, now=BASE + 100 * 86400, min_samples=50):
    parquet_root = tmp_path / "parquet"
    database_path = tmp_path / "boros.duckdb"
    write_dataset(rows, "executable_opportunities", parquet_root)
    build_duckdb_catalog(parquet_root, database_path)
    return HistoricalBenchmarkLookup(
        database_path,
        now_timestamp=now,
        min_samples=min_samples,
    )


def test_live_percentile_is_causal_and_does_not_insert_live_observation(tmp_path):
    rows = [opportunity(BASE + index * 86400, index / 100) for index in range(50)]
    lookup = make_lookup(tmp_path, rows, now=BASE + 60 * 86400, min_samples=1)

    result = lookup.lookup(
        candidate(0.49, timestamp=BASE + 50 * 86400, maturity=date(2027, 4, 10))
    )

    assert result.percentile_lifetime == pytest.approx(100.0)
    assert result.sample_count_lifetime == 50
    assert result.historical_max_timestamp == BASE + 49 * 86400


def test_windows_and_future_rows_are_separate(tmp_path):
    rows = [
        opportunity(BASE - 31 * 86400, 0.01),
        opportunity(BASE - 10 * 86400, 0.02),
        opportunity(BASE, 0.03),
        opportunity(BASE + 20 * 86400, 99.0),
    ]
    lookup = make_lookup(tmp_path, rows, now=BASE + 20 * 86400, min_samples=1)

    result = lookup.lookup(candidate(0.025, timestamp=BASE))

    assert result.sample_count_30d == 2
    assert result.sample_count_90d == 3
    assert result.sample_count_lifetime == 3
    assert result.percentile_30d == pytest.approx(50.0)
    assert result.percentile_90d == pytest.approx(66.6666666667)


def test_dte_primary_falls_back_once_to_pair_and_insufficient_is_explicit(tmp_path):
    rows = [opportunity(BASE + i * 300, 0.01 + i / 1000, dte_days=60) for i in range(50)]
    rows.extend(opportunity(BASE + 20_000 + i * 300, 0.2 + i / 1000, dte_days=32) for i in range(3))
    lookup = make_lookup(tmp_path, rows, now=BASE + 100_000, min_samples=5)

    fallback = lookup.lookup(candidate(0.3, timestamp=BASE + 30_000))
    assert fallback.benchmark_level == "pair"
    assert fallback.percentile_lifetime is not None

    insufficient_lookup = make_lookup(tmp_path / "small", rows[:3], now=BASE + 100_000, min_samples=5)
    insufficient = insufficient_lookup.lookup(candidate(0.3, timestamp=BASE + 30_000))
    assert insufficient.benchmark_level == "insufficient"
    assert insufficient.percentile_90d is None
    assert insufficient.sample_count_lifetime == 3


def test_direction_notional_token_and_dte_are_not_mixed(tmp_path):
    rows = [
        opportunity(BASE + i * 300, 0.01 + i / 1000, short_venue="HYPERLIQUID")
        for i in range(2)
    ]
    rows.extend(
        opportunity(BASE + i * 300, 0.5 + i / 1000, short_venue="BYBIT", long_venue="HYPERLIQUID")
        for i in range(2)
    )
    rows.extend(opportunity(BASE + i * 300, 0.7 + i / 1000, notional_usd=5_000) for i in range(2))
    rows.extend(opportunity(BASE + i * 300, 0.9 + i / 1000, token_id=9) for i in range(2))
    rows.extend(opportunity(BASE + i * 300, 1.1 + i / 1000, dte_days=60) for i in range(2))
    lookup = make_lookup(tmp_path, rows, now=BASE + 100_000, min_samples=1)

    result = lookup.lookup(candidate(0.02))

    assert result.percentile_lifetime == pytest.approx(100.0)
    assert result.sample_count_lifetime == 2


def test_benchmark_age_and_known_asset_aliases(tmp_path):
    lookup = make_lookup(tmp_path, [opportunity(BASE, 0.1)], now=BASE + 8 * 86400, min_samples=1)
    result = lookup.lookup(candidate(0.2, timestamp=BASE + 8 * 86400))

    assert result.benchmark_fresh is False
    assert result.benchmark_age_seconds == 8 * 86400
    assert canonical_live_asset("GOLD") == "XAU"
    assert canonical_live_asset("HYPE") == "HYPE"
