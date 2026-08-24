from __future__ import annotations

import importlib
import importlib.util
from datetime import date, datetime, timezone

import duckdb
import pytest

from boros_research.radar_economics import ProxyEconomics


ANCHOR = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())
FUTURE_MATURITY = date(2026, 9, 25)
NEAR_MATURITY = date(2026, 8, 30)
PAST_MATURITY = date(2026, 8, 20)


def _radar_module():
    if importlib.util.find_spec("boros_research.radar") is None:
        pytest.skip("radar aggregation is not implemented yet")
    return importlib.import_module("boros_research.radar")


def test_radar_payload_builder_is_a_public_module():
    """Catches removal of the Task 3 public aggregation boundary."""
    assert importlib.util.find_spec("boros_research.radar") is not None


@pytest.fixture
def connection():
    database = duckdb.connect(":memory:")
    database.execute(
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
    database.execute(
        """
        CREATE TABLE executable_opportunities (
            timestamp BIGINT,
            asset VARCHAR,
            maturity DATE,
            dte_days INTEGER,
            token_id BIGINT,
            short_market_id BIGINT,
            short_venue VARCHAR,
            long_market_id BIGINT,
            long_venue VARCHAR,
            notional_usd DOUBLE,
            short_bid_vwap_apr DOUBLE,
            long_ask_vwap_apr DOUBLE,
            executable_spread_apr DOUBLE,
            fully_executable BOOLEAN,
            invalid_reason VARCHAR
        )
        """
    )
    yield database
    database.close()


def _add_market(
    connection,
    market_id: int,
    venue: str,
    *,
    asset: str = "HYPE",
    token_id: int = 3,
    maturity: date = FUTURE_MATURITY,
) -> None:
    connection.execute(
        "INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?, ?)",
        [market_id, token_id, venue, asset, maturity, f"{venue}-{market_id}", venue],
    )


def _add_opportunity(
    connection,
    *,
    timestamp: int,
    short_market_id: int,
    short_venue: str,
    long_market_id: int,
    long_venue: str,
    spread: float,
    notional: float = 10_000,
    short_bid: float | None = None,
    long_ask: float | None = None,
    asset: str = "HYPE",
    token_id: int = 3,
    maturity: date = FUTURE_MATURITY,
    dte_days: int = 30,
    fully_executable: bool = True,
    invalid_reason: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO executable_opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
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
            spread if short_bid is None else short_bid,
            0.01 if long_ask is None else long_ask,
            spread,
            fully_executable,
            invalid_reason,
        ],
    )


def _rows_by_key(payload):
    return {(row["asset"], row["notionalUsd"]): row for row in payload["rows"]}


def test_reference_p95_is_anchored_windowed_deduplicated_and_drives_each_notional_cutoff(
    connection,
):
    """Catches calculating P95 after DTE filtering or per direction instead of best spread."""
    _add_market(connection, 1, "HYPERLIQUID")
    _add_market(connection, 2, "BYBIT")
    _add_market(connection, 3, "HYPERLIQUID", asset="ETH", token_id=4)
    _add_market(connection, 4, "BYBIT", asset="ETH", token_id=4)

    _add_opportunity(
        connection,
        timestamp=ANCHOR,
        short_market_id=1,
        short_venue="HYPERLIQUID",
        long_market_id=2,
        long_venue="BYBIT",
        spread=0.10,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR,
        short_market_id=2,
        short_venue="BYBIT",
        long_market_id=1,
        long_venue="HYPERLIQUID",
        spread=0.20,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR,
        short_market_id=1,
        short_venue="HYPERLIQUID",
        long_market_id=2,
        long_venue="BYBIT",
        spread=0.05,
        notional=50_000,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR,
        short_market_id=2,
        short_venue="BYBIT",
        long_market_id=1,
        long_venue="HYPERLIQUID",
        spread=0.06,
        notional=50_000,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR - 91 * 24 * 60 * 60,
        short_market_id=1,
        short_venue="HYPERLIQUID",
        long_market_id=2,
        long_venue="BYBIT",
        spread=0.99,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR,
        short_market_id=3,
        short_venue="HYPERLIQUID",
        long_market_id=4,
        long_venue="BYBIT",
        spread=0.10,
        asset="ETH",
        token_id=4,
    )

    payload = _radar_module().build_radar_payload(
        connection,
        {
            10_000: {"HYPE": ProxyEconomics(20, 1800, 1, 1)},
            25_000: {},
            50_000: {"HYPE": ProxyEconomics(20, 1800, 2, 2)},
        },
        generated_at="2026-08-24T00:00:00Z",
    )
    rows = _rows_by_key(payload)

    assert rows[("HYPE", 10_000)]["referenceP95SpreadApr"] == pytest.approx(0.20)
    assert rows[("HYPE", 10_000)]["dteCutoffDays"] == 13
    assert rows[("HYPE", 50_000)]["referenceP95SpreadApr"] == pytest.approx(0.06)
    assert rows[("HYPE", 50_000)]["dteCutoffDays"] == 9
    assert rows[("ETH", 10_000)] == {
        "asset": "ETH",
        "notionalUsd": 10_000,
        "available": False,
        "unavailableReason": "proxy-unavailable",
        "dteCutoffDays": None,
        "referenceP95SpreadApr": None,
        "proxy": None,
        "normal": None,
        "burst": None,
        "highVenue": None,
        "lowVenue": None,
        "highVenueComparisonCount": None,
        "lowVenueComparisonCount": None,
    }


def test_radar_excludes_short_dte_from_statistics_without_mutating_rows_and_keeps_spikes(
    connection,
):
    """Catches using a global DTE rule, clipping high observations, or writing production data."""
    _add_market(connection, 1, "HYPERLIQUID")
    _add_market(connection, 2, "BYBIT")
    for index in range(60):
        _add_opportunity(
            connection,
            timestamp=ANCHOR - index,
            short_market_id=1,
            short_venue="HYPERLIQUID",
            long_market_id=2,
            long_venue="BYBIT",
            spread=0.02 + index * 0.001,
        )
    _add_opportunity(
        connection,
        timestamp=ANCHOR - 60,
        short_market_id=1,
        short_venue="HYPERLIQUID",
        long_market_id=2,
        long_venue="BYBIT",
        spread=0.15,
    )
    _add_opportunity(
        connection,
        timestamp=ANCHOR + 1,
        short_market_id=1,
        short_venue="HYPERLIQUID",
        long_market_id=2,
        long_venue="BYBIT",
        spread=0.50,
        dte_days=2,
    )
    original_count = connection.execute(
        "SELECT COUNT(*) FROM executable_opportunities"
    ).fetchone()[0]

    payload = _radar_module().build_radar_payload(
        connection,
        {10_000: {"HYPE": ProxyEconomics(0, 100, 1, 1)}},
        generated_at="2026-08-24T00:00:00Z",
    )
    row = _rows_by_key(payload)[("HYPE", 10_000)]

    assert row["dteCutoffDays"] == 24
    assert row["referenceP95SpreadApr"] == pytest.approx(0.07795)
    assert row["normal"] == {
        "shortVenue": "HYPERLIQUID",
        "longVenue": "BYBIT",
        "medianSpreadApr": pytest.approx(0.05),
        "sampleCount": 61,
        "drilldownMaturity": "2026-09-25",
    }
    assert row["burst"] == {
        "shortVenue": "HYPERLIQUID",
        "longVenue": "BYBIT",
        "p95SpreadApr": pytest.approx(0.077),
        "sampleCount": 61,
        "drilldownMaturity": "2026-09-25",
    }
    assert (
        connection.execute("SELECT COUNT(*) FROM executable_opportunities").fetchone()[
            0
        ]
        == original_count
    )


def test_radar_orders_tied_directions_lexically_and_selects_nearest_future_drilldown(
    connection,
):
    """Catches arbitrary equal-metric ranking and maturity selection unrelated to the anchor."""
    _add_market(connection, 1, "HYPERLIQUID", maturity=NEAR_MATURITY)
    _add_market(connection, 2, "BYBIT", maturity=NEAR_MATURITY)
    _add_market(connection, 3, "HYPERLIQUID", maturity=FUTURE_MATURITY)
    _add_market(connection, 4, "BINANCE", maturity=FUTURE_MATURITY)
    for index in range(50):
        timestamp = ANCHOR - index
        _add_opportunity(
            connection,
            timestamp=timestamp,
            short_market_id=1,
            short_venue="HYPERLIQUID",
            long_market_id=2,
            long_venue="BYBIT",
            spread=0.10,
            maturity=NEAR_MATURITY,
        )
        _add_opportunity(
            connection,
            timestamp=timestamp,
            short_market_id=3,
            short_venue="HYPERLIQUID",
            long_market_id=4,
            long_venue="BINANCE",
            spread=0.10,
            maturity=FUTURE_MATURITY,
        )

    payload = _radar_module().build_radar_payload(
        connection,
        {10_000: {"HYPE": ProxyEconomics(0, 100, 1, 1)}},
        generated_at="2026-08-24T00:00:00Z",
    )
    row = _rows_by_key(payload)[("HYPE", 10_000)]

    assert row["normal"] == {
        "shortVenue": "HYPERLIQUID",
        "longVenue": "BINANCE",
        "medianSpreadApr": pytest.approx(0.10),
        "sampleCount": 50,
        "drilldownMaturity": "2026-09-25",
    }
    assert row["burst"] == {
        "shortVenue": "HYPERLIQUID",
        "longVenue": "BINANCE",
        "p95SpreadApr": pytest.approx(0.10),
        "sampleCount": 50,
        "drilldownMaturity": "2026-09-25",
    }


def test_radar_counts_fair_venue_winners_once_per_group_and_keeps_ties_unknown(
    connection,
):
    """Catches pair-row-weighted venue claims or arbitrary exact-rate/win-count tie breaking."""
    for market_id, venue in ((1, "HYPERLIQUID"), (2, "BYBIT"), (3, "BINANCE")):
        _add_market(connection, market_id, venue)
    for index in range(50):
        timestamp = ANCHOR - index
        for short_id, short_venue, short_bid in (
            (1, "HYPERLIQUID", 0.10),
            (2, "BYBIT", 0.06),
            (3, "BINANCE", 0.08),
        ):
            for long_id, long_venue, long_ask in (
                (1, "HYPERLIQUID", 0.04),
                (2, "BYBIT", 0.01),
                (3, "BINANCE", 0.03),
            ):
                if short_id == long_id:
                    continue
                _add_opportunity(
                    connection,
                    timestamp=timestamp,
                    short_market_id=short_id,
                    short_venue=short_venue,
                    long_market_id=long_id,
                    long_venue=long_venue,
                    short_bid=short_bid,
                    long_ask=long_ask,
                    spread=short_bid - long_ask,
                )

    payload = _radar_module().build_radar_payload(
        connection,
        {10_000: {"HYPE": ProxyEconomics(0, 100, 1, 1)}},
        generated_at="2026-08-24T00:00:00Z",
    )
    row = _rows_by_key(payload)[("HYPE", 10_000)]

    assert row["highVenue"] == "HYPERLIQUID"
    assert row["lowVenue"] == "BYBIT"
    assert row["highVenueComparisonCount"] == 50
    assert row["lowVenueComparisonCount"] == 50

    connection.execute(
        "UPDATE executable_opportunities SET short_bid_vwap_apr = 0.10 "
        "WHERE short_venue = 'BYBIT'"
    )
    tied = _radar_module().build_radar_payload(
        connection,
        {10_000: {"HYPE": ProxyEconomics(0, 100, 1, 1)}},
        generated_at="2026-08-24T00:00:00Z",
    )

    assert _rows_by_key(tied)[("HYPE", 10_000)]["highVenue"] is None
