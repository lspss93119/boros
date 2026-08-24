from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from build_arbitrage_site_data import build_payload


NOTIONALS = (10_000.0, 25_000.0, 50_000.0)
BASE_DATE = date(2026, 8, 1)


def ts(day: int, hour: int = 0) -> int:
    current = datetime.combine(BASE_DATE + timedelta(days=day - 1), datetime.min.time())
    return int(current.replace(hour=hour, tzinfo=timezone.utc).timestamp())


def create_fixture_database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE markets (
            market_id BIGINT,
            token_id BIGINT,
            venue VARCHAR,
            asset VARCHAR,
            maturity DATE,
            symbol VARCHAR,
            name VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE executable_opportunities (
            timestamp BIGINT,
            asset VARCHAR,
            maturity DATE,
            dte_days BIGINT,
            token_id BIGINT,
            short_market_id BIGINT,
            short_venue VARCHAR,
            long_market_id BIGINT,
            long_venue VARCHAR,
            notional_usd DOUBLE,
            short_bid_vwap_apr DOUBLE,
            long_ask_vwap_apr DOUBLE,
            executable_spread_apr DOUBLE,
            short_top_bid_apr DOUBLE,
            long_top_ask_apr DOUBLE,
            top_of_book_spread_apr DOUBLE,
            short_impact_apr DOUBLE,
            long_impact_apr DOUBLE,
            short_filled_usd DOUBLE,
            long_filled_usd DOUBLE,
            fully_executable BOOLEAN,
            invalid_reason VARCHAR,
            short_snapshot_age_sec BIGINT,
            long_snapshot_age_sec BIGINT,
            short_price_age_sec BIGINT,
            long_price_age_sec BIGINT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE historical_benchmarks (
            timestamp BIGINT,
            asset VARCHAR,
            maturity DATE,
            dte_days BIGINT,
            dte_bucket VARCHAR,
            token_id BIGINT,
            short_market_id BIGINT,
            short_venue VARCHAR,
            long_market_id BIGINT,
            long_venue VARCHAR,
            notional_usd DOUBLE,
            executable_spread_apr DOUBLE,
            benchmark_level VARCHAR,
            percentile_30d DOUBLE,
            percentile_90d DOUBLE,
            percentile_lifetime DOUBLE,
            sample_count_30d BIGINT,
            sample_count_90d BIGINT,
            sample_count_lifetime BIGINT
        )
        """
    )

    markets = [
        (101, 3, "HYPERLIQUID", "HYPE", date(2026, 9, 25), "HYPEUSDT", "HYPE HL"),
        (102, 3, "BYBIT", "HYPE", date(2026, 9, 25), "HYPEUSDT", "HYPE Bybit"),
        (103, 3, "BYBIT", "HYPE", date(2026, 9, 25), "HYPEUSDT", "HYPE Bybit short"),
        (104, 3, "HYPERLIQUID", "HYPE", date(2026, 9, 25), "HYPEUSDT", "HYPE HL long"),
        (105, 3, "HYPERLIQUID", "HYPE", date(2026, 8, 28), "HYPEUSDT", "HYPE Aug HL"),
        (106, 3, "BYBIT", "HYPE", date(2026, 8, 28), "HYPEUSDT", "HYPE Aug Bybit"),
    ]
    connection.executemany("INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?, ?)", markets)

    opportunities = []

    def add_opportunity(
        timestamp: int,
        maturity: date,
        short_market_id: int,
        short_venue: str,
        long_market_id: int,
        long_venue: str,
        notional: float,
        spread: float,
        *,
        fully: bool = True,
        invalid_reason: str | None = None,
        asset: str = "HYPE",
        token_id: int = 3,
    ) -> None:
        dte_days = (maturity - datetime.fromtimestamp(timestamp, timezone.utc).date()).days
        opportunities.append(
            (
                timestamp,
                asset,
                maturity,
                dte_days,
                token_id,
                short_market_id,
                short_venue,
                long_market_id,
                long_venue,
                notional,
                spread + 0.01,
                0.01,
                spread,
                spread + 0.02,
                0.02,
                spread + 0.01,
                0.001,
                0.002,
                notional if fully else 100.0,
                notional if fully else 100.0,
                fully,
                invalid_reason,
                60,
                60,
                60,
                60,
            )
        )

    # Primary HL -> Bybit structure. Day 2 has one invalid row so the daily
    # denominator is larger than the executable-spread aggregation set.
    for day, spreads in ((1, (0.10, 0.08, 0.07)), (2, (0.20, 0.18, 0.16)), (3, (0.30, 0.28, 0.27))):
        for notional, spread in zip(NOTIONALS, spreads):
            add_opportunity(
                ts(day), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", notional, spread,
                fully=not (day == 3 and notional == 50_000),
                invalid_reason=("short_depth_insufficient" if day == 3 and notional == 50_000 else None),
            )
    add_opportunity(
        ts(2, 1), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", 10_000, 0.99,
        fully=False, invalid_reason="short_depth_insufficient",
    )
    add_opportunity(
        ts(4), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", 10_000, 0.99,
    )
    add_opportunity(
        ts(-90), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", 10_000, 0.01,
    )
    # This selected-notional row must not leak into the output.
    add_opportunity(
        ts(1), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", 2_000, 0.50,
    )

    # Reverse direction and a different maturity must remain distinct.
    add_opportunity(ts(1), date(2026, 9, 25), 103, "BYBIT", 104, "HYPERLIQUID", 10_000, 0.04)
    add_opportunity(ts(-90), date(2026, 9, 25), 103, "BYBIT", 104, "HYPERLIQUID", 10_000, 0.02)
    add_opportunity(ts(2), date(2026, 9, 25), 103, "BYBIT", 104, "HYPERLIQUID", 25_000, 0.05)
    add_opportunity(ts(1), date(2026, 8, 28), 105, "HYPERLIQUID", 106, "BYBIT", 10_000, 0.06)

    connection.executemany(
        "INSERT INTO executable_opportunities VALUES (" + ",".join("?" for _ in range(26)) + ")",
        opportunities,
    )

    benchmarks = []

    def add_benchmark(
        timestamp: int,
        maturity: date,
        short_market_id: int,
        short_venue: str,
        long_market_id: int,
        long_venue: str,
        notional: float,
        percentile: float | None,
        *,
        level: str = "dte",
        dte_bucket: str | None = "46-90",
    ) -> None:
        dte_days = (maturity - datetime.fromtimestamp(timestamp, timezone.utc).date()).days
        benchmarks.append(
            (
                timestamp,
                "HYPE",
                maturity,
                dte_days,
                dte_bucket,
                3,
                short_market_id,
                short_venue,
                long_market_id,
                long_venue,
                notional,
                None,
                level,
                percentile,
                percentile,
                percentile,
                10,
                20,
                30,
            )
        )

    for day in (1, 2, 3):
        for notional in NOTIONALS:
            add_benchmark(
                ts(day), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", notional,
                80.0 + day + notional / 100_000,
            )
    # Later same-day benchmark wins for daily attachment.
    add_benchmark(ts(2, 2), date(2026, 9, 25), 101, "HYPERLIQUID", 102, "BYBIT", 10_000, 92.0)
    add_benchmark(ts(1), date(2026, 9, 25), 103, "BYBIT", 104, "HYPERLIQUID", 10_000, 88.0, level="pair", dte_bucket=None)
    add_benchmark(ts(2), date(2026, 9, 25), 103, "BYBIT", 104, "HYPERLIQUID", 25_000, 87.0, level="pair", dte_bucket=None)
    add_benchmark(ts(1), date(2026, 8, 28), 105, "HYPERLIQUID", 106, "BYBIT", 10_000, 91.0, dte_bucket="22-45")

    connection.executemany(
        "INSERT INTO historical_benchmarks VALUES (" + ",".join("?" for _ in range(19)) + ")",
        benchmarks,
    )
    connection.close()


def find_structure(payload, *, short_venue, long_venue, maturity):
    return next(
        structure
        for structure in payload["structures"]
        if structure["shortVenue"] == short_venue
        and structure["longVenue"] == long_venue
        and structure["maturity"] == maturity
    )


def test_exporter_preserves_identity_daily_stats_and_benchmark_semantics(tmp_path, monkeypatch):
    database_path = tmp_path / "boros.duckdb"
    create_fixture_database(database_path)

    import build_arbitrage_site_data as exporter

    original_connect = exporter.duckdb.connect
    read_only_flags = []

    def connect(path, **kwargs):
        read_only_flags.append(kwargs.get("read_only"))
        return original_connect(path, **kwargs)

    monkeypatch.setattr(exporter.duckdb, "connect", connect)
    payload = build_payload(database_path, generated_at="2026-08-04T00:00:00+00:00")

    assert read_only_flags == [True]
    assert payload["notionals"] == [10_000, 25_000, 50_000]
    assert all(item["notionalUsd"] in payload["notionals"] for item in payload["leaderboard"])

    forward = find_structure(
        payload,
        short_venue="HYPERLIQUID",
        long_venue="BYBIT",
        maturity="2026-09-25",
    )
    reverse = find_structure(
        payload,
        short_venue="BYBIT",
        long_venue="HYPERLIQUID",
        maturity="2026-09-25",
    )
    different_maturity = find_structure(
        payload,
        short_venue="HYPERLIQUID",
        long_venue="BYBIT",
        maturity="2026-08-28",
    )
    assert forward["shortMarketId"] == 101
    assert forward["longMarketId"] == 102
    assert next(
        row for row in payload["leaderboard"]
        if row["structureId"] == forward["id"] and row["notionalUsd"] == 10_000
    )["dteBucket"] == "46-90"
    assert reverse["shortMarketId"] == 103
    assert reverse["longMarketId"] == 104
    assert different_maturity["maturity"] != forward["maturity"]

    primary = forward["notionals"]["10000"]
    day_two = next(row for row in primary["daily"] if row["date"] == "2026-08-02")
    assert day_two["observationCount"] == 2
    assert day_two["fullyExecutableCount"] == 1
    assert day_two["fullyExecutableRate"] == pytest.approx(0.5)
    assert day_two["dailyMedianExecutableSpreadApr"] == pytest.approx(0.20)
    assert day_two["dailyMaxExecutableSpreadApr"] == pytest.approx(0.20)
    assert day_two["benchmark"]["percentile90d"] == pytest.approx(92.0)

    assert primary["latestRaw"]["timestamp"] == ts(4)
    assert primary["latestRaw"]["executableSpreadApr"] == pytest.approx(0.99)
    invalid_50k = forward["notionals"]["50000"]["latestRaw"]
    assert invalid_50k["fullyExecutable"] is False
    assert invalid_50k["executableSpreadApr"] is None
    assert invalid_50k["invalidReason"] == "short_depth_insufficient"

    distribution = primary["distribution"]
    assert distribution["benchmarkLevel"] == "dte"
    assert distribution["dteBucket"] == "46-90"
    assert distribution["windows"]["90d"]["count"] == 3
    assert distribution["windows"]["lifetime"]["count"] == 3
    assert len(distribution["histogram90d"]["counts"]) == 30
    assert sum(distribution["histogram90d"]["counts"]) == 3


def test_exporter_common_timestamp_and_deterministic_finite_json(tmp_path):
    database_path = tmp_path / "boros.duckdb"
    create_fixture_database(database_path)

    first = build_payload(database_path, generated_at="2026-08-04T00:00:00+00:00")
    second = build_payload(database_path, generated_at="2026-08-04T00:00:00+00:00")

    assert first == second
    encoded = json.dumps(first, allow_nan=False, separators=(",", ":"))
    assert "NaN" not in encoded
    assert "Infinity" not in encoded

    forward = find_structure(
        first,
        short_venue="HYPERLIQUID",
        long_venue="BYBIT",
        maturity="2026-09-25",
    )
    comparison = forward["notionalComparison"]
    assert forward["commonTimestamp"] == ts(3)
    assert [row["notionalUsd"] for row in comparison] == [10_000, 25_000, 50_000]
    assert comparison[0]["executableSpreadApr"] == pytest.approx(0.30)
    assert comparison[2]["available"] is True
    assert comparison[2]["fullyExecutable"] is False

    reverse = find_structure(
        first,
        short_venue="BYBIT",
        long_venue="HYPERLIQUID",
        maturity="2026-09-25",
    )
    assert reverse["commonTimestamp"] is None
    assert all(row["available"] is False for row in reverse["notionalComparison"])
    reverse_distribution = reverse["notionals"]["10000"]["distribution"]
    assert reverse_distribution["benchmarkLevel"] == "pair"
    assert reverse_distribution["windows"]["90d"]["count"] == 1
    assert reverse_distribution["windows"]["lifetime"]["count"] == 2


def test_benchmark_daily_attachment_is_not_multiplied_by_opportunity_rows(tmp_path):
    database_path = tmp_path / "boros.duckdb"
    create_fixture_database(database_path)

    payload = build_payload(database_path, generated_at="2026-08-04T00:00:00+00:00")
    forward = find_structure(
        payload,
        short_venue="HYPERLIQUID",
        long_venue="BYBIT",
        maturity="2026-09-25",
    )

    daily = forward["notionals"]["10000"]["daily"]
    assert [row["date"] for row in daily] == [
        "2026-05-02",
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
        "2026-08-04",
    ]
