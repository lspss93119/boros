"""Efficient, read-only historical percentile lookup for live opportunities."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .benchmark import dte_bucket_for_days
from .normalize import asset_from_symbol


WINDOW_30D_SEC = 30 * 24 * 60 * 60
WINDOW_90D_SEC = 90 * 24 * 60 * 60
BENCHMARK_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class LiveBenchmarkCandidate:
    candidate_id: str
    asset: str
    short_venue: str
    long_venue: str
    token_id: int
    notional_usd: float
    maturity: date
    dte_bucket: str
    timestamp: int
    spread_apr: float

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if self.dte_bucket != dte_bucket_for_days(
            (self.maturity - datetime.fromtimestamp(self.timestamp, tz=timezone.utc).date()).days
        ):
            raise ValueError("dte_bucket does not match candidate maturity and timestamp")
        if isinstance(self.token_id, bool) or self.token_id <= 0:
            raise ValueError("token_id must be positive")
        for value, name in (
            (self.notional_usd, "notional_usd"),
            (self.spread_apr, "spread_apr"),
        ):
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.notional_usd <= 0:
            raise ValueError("notional_usd must be positive")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise ValueError("timestamp must be an integer Unix timestamp")


@dataclass(frozen=True)
class LiveBenchmarkResult:
    candidate_id: str
    benchmark_level: str
    dte_bucket: str
    percentile_30d: float | None
    percentile_90d: float | None
    percentile_lifetime: float | None
    sample_count_30d: int
    sample_count_90d: int
    sample_count_lifetime: int
    historical_max_timestamp: int | None
    benchmark_age_seconds: int | None
    benchmark_fresh: bool


def canonical_live_asset(value: str) -> str:
    """Apply only aliases already represented by the Phase 1 canonicalizer."""
    asset = asset_from_symbol(value)
    if asset == "GOLD":
        return "XAU"
    return asset


def _percentile(count: int, less_or_equal: int, minimum: int) -> float | None:
    if count < minimum:
        return None
    return 100.0 * less_or_equal / count


class HistoricalBenchmarkLookup:
    """Batch live candidates into one DuckDB query over Phase 1 history."""

    def __init__(
        self,
        database_path: Path,
        *,
        now_timestamp: int | None = None,
        min_samples: int = 50,
    ) -> None:
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        self.database_path = Path(database_path)
        self.now_timestamp = now_timestamp
        self.min_samples = min_samples

    def _now(self) -> int:
        if self.now_timestamp is not None:
            return self.now_timestamp
        import time

        return int(time.time())

    def _health_values(self, historical_max: int | None) -> tuple[int | None, bool]:
        if historical_max is None:
            return None, False
        now = self._now()
        age_seconds = max(0, now - historical_max)
        max_date = datetime.fromtimestamp(historical_max, tz=timezone.utc).date()
        now_date = datetime.fromtimestamp(now, tz=timezone.utc).date()
        return age_seconds, (now_date - max_date).days <= BENCHMARK_MAX_AGE_DAYS

    def health(self) -> tuple[int | None, int | None, bool]:
        """Return max historical timestamp, age, and the seven-day freshness gate."""
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            historical_max = self._historical_max(connection)
        age_seconds, fresh = self._health_values(historical_max)
        return historical_max, age_seconds, fresh

    def _historical_max(self, connection: duckdb.DuckDBPyConnection) -> int | None:
        value = connection.execute(
            "SELECT max(timestamp) FROM executable_opportunities "
            "WHERE fully_executable AND executable_spread_apr IS NOT NULL"
        ).fetchone()[0]
        return None if value is None else int(value)

    def validate_market_identity(
        self,
        *,
        market_id: int,
        venue: str,
        candidate: LiveBenchmarkCandidate,
    ) -> tuple[bool, str | None]:
        """Verify live market IDs against local metadata without guessing aliases."""
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as connection:
                direct = connection.execute(
                    """
                    SELECT market_id, token_id, venue, asset, maturity
                    FROM markets
                    WHERE market_id = ?
                    """,
                    [market_id],
                ).fetchall()
                if direct:
                    if len(direct) != 1:
                        return False, "ambiguous_market_id"
                    _, token_id, stored_venue, stored_asset, maturity = direct[0]
                    if (
                        int(token_id) != candidate.token_id
                        or str(stored_venue) != venue
                        or str(stored_asset) != candidate.asset
                        or maturity != candidate.maturity
                    ):
                        return False, "market_identity_mismatch"
                    return True, None
                fallback = connection.execute(
                    """
                    SELECT market_id
                    FROM markets
                    WHERE token_id = ? AND venue = ? AND asset = ? AND maturity = ?
                    ORDER BY market_id
                    """,
                    [candidate.token_id, venue, candidate.asset, candidate.maturity],
                ).fetchall()
        except Exception:
            return False, "market_catalog_unavailable"
        if len(fallback) == 1:
            return True, None
        if not fallback:
            return False, "market_metadata_missing"
        return False, "ambiguous_market_identity"

    def lookup(self, candidate: LiveBenchmarkCandidate) -> LiveBenchmarkResult:
        return self.lookup_many((candidate,))[candidate.candidate_id]

    def lookup_many(
        self,
        candidates: Iterable[LiveBenchmarkCandidate],
    ) -> dict[str, LiveBenchmarkResult]:
        candidate_list = tuple(candidates)
        if len({candidate.candidate_id for candidate in candidate_list}) != len(candidate_list):
            raise ValueError("candidate_id values must be unique")
        if not candidate_list:
            return {}

        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            historical_max = self._historical_max(connection)
            connection.execute(
                """
                CREATE TEMP TABLE live_candidates (
                    candidate_id VARCHAR,
                    asset VARCHAR,
                    short_venue VARCHAR,
                    long_venue VARCHAR,
                    token_id BIGINT,
                    notional_usd DOUBLE,
                    dte_bucket VARCHAR,
                    as_of_timestamp BIGINT,
                    spread_apr DOUBLE
                )
                """
            )
            connection.executemany(
                "INSERT INTO live_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.candidate_id,
                        item.asset,
                        item.short_venue,
                        item.long_venue,
                        item.token_id,
                        item.notional_usd,
                        item.dte_bucket,
                        item.timestamp,
                        item.spread_apr,
                    )
                    for item in candidate_list
                ],
            )
            rows = connection.execute(
                f"""
                WITH matched AS (
                    SELECT
                        c.candidate_id,
                        'dte' AS benchmark_level,
                        o.timestamp,
                        o.executable_spread_apr,
                        c.as_of_timestamp,
                        c.spread_apr
                    FROM executable_opportunities o
                    JOIN live_candidates c
                      ON o.asset = c.asset
                     AND o.short_venue = c.short_venue
                     AND o.long_venue = c.long_venue
                     AND o.token_id = c.token_id
                     AND o.notional_usd = c.notional_usd
                     AND CASE
                           WHEN o.dte_days BETWEEN 0 AND 7 THEN '0-7'
                           WHEN o.dte_days BETWEEN 8 AND 21 THEN '8-21'
                           WHEN o.dte_days BETWEEN 22 AND 45 THEN '22-45'
                           WHEN o.dte_days BETWEEN 46 AND 90 THEN '46-90'
                           WHEN o.dte_days >= 91 THEN '91+'
                         END = c.dte_bucket
                    WHERE o.fully_executable
                      AND o.executable_spread_apr IS NOT NULL
                      AND o.timestamp <= c.as_of_timestamp
                    UNION ALL
                    SELECT
                        c.candidate_id,
                        'pair' AS benchmark_level,
                        o.timestamp,
                        o.executable_spread_apr,
                        c.as_of_timestamp,
                        c.spread_apr
                    FROM executable_opportunities o
                    JOIN live_candidates c
                      ON o.asset = c.asset
                     AND o.short_venue = c.short_venue
                     AND o.long_venue = c.long_venue
                     AND o.token_id = c.token_id
                     AND o.notional_usd = c.notional_usd
                    WHERE o.fully_executable
                      AND o.executable_spread_apr IS NOT NULL
                      AND o.timestamp <= c.as_of_timestamp
                )
                SELECT
                    candidate_id,
                    benchmark_level,
                    count(*) FILTER (WHERE timestamp >= as_of_timestamp - {WINDOW_30D_SEC}) AS count_30d,
                    count(*) FILTER (
                        WHERE timestamp >= as_of_timestamp - {WINDOW_30D_SEC}
                          AND executable_spread_apr <= spread_apr
                    ) AS le_30d,
                    count(*) FILTER (WHERE timestamp >= as_of_timestamp - {WINDOW_90D_SEC}) AS count_90d,
                    count(*) FILTER (
                        WHERE timestamp >= as_of_timestamp - {WINDOW_90D_SEC}
                          AND executable_spread_apr <= spread_apr
                    ) AS le_90d,
                    count(*) AS count_lifetime,
                    count(*) FILTER (WHERE executable_spread_apr <= spread_apr) AS le_lifetime
                FROM matched
                GROUP BY candidate_id, benchmark_level
                ORDER BY candidate_id, benchmark_level
                """
            ).fetchall()

        aggregates: dict[str, dict[str, tuple[int, int, int, int, int, int]]] = {}
        for row in rows:
            (
                candidate_id,
                level,
                count_30d,
                le_30d,
                count_90d,
                le_90d,
                count_lifetime,
                le_lifetime,
            ) = row
            aggregates.setdefault(str(candidate_id), {})[str(level)] = (
                int(count_30d),
                int(le_30d),
                int(count_90d),
                int(le_90d),
                int(count_lifetime),
                int(le_lifetime),
            )

        age_seconds, fresh = self._health_values(historical_max)

        output: dict[str, LiveBenchmarkResult] = {}
        for item in candidate_list:
            by_level = aggregates.get(item.candidate_id, {})
            dte = by_level.get("dte", (0, 0, 0, 0, 0, 0))
            pair = by_level.get("pair", (0, 0, 0, 0, 0, 0))
            if dte[4] >= self.min_samples:
                level = "dte"
                selected = dte
            elif pair[4] >= self.min_samples:
                level = "pair"
                selected = pair
            else:
                level = "insufficient"
                selected = pair if pair[4] >= dte[4] else dte
            output[item.candidate_id] = LiveBenchmarkResult(
                candidate_id=item.candidate_id,
                benchmark_level=level,
                dte_bucket=item.dte_bucket,
                percentile_30d=_percentile(selected[0], selected[1], self.min_samples),
                percentile_90d=_percentile(selected[2], selected[3], self.min_samples),
                percentile_lifetime=_percentile(selected[4], selected[5], self.min_samples),
                sample_count_30d=selected[0],
                sample_count_90d=selected[2],
                sample_count_lifetime=selected[4],
                historical_max_timestamp=historical_max,
                benchmark_age_seconds=age_seconds,
                benchmark_fresh=fresh,
            )
        return output
