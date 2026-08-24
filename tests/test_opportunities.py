from datetime import date

import pytest

from boros_research.execution import ExecutionResult, NotionalExecution
from boros_research.market_metadata import MarketInfo
from boros_research.opportunities import (
    MarketExecutionObservation,
    OpportunityKey,
    build_executable_opportunities,
    build_market_groups,
)


MATURITY = date(2026, 9, 25)
TIMESTAMP = 1_787_529_600
NOTIONAL = 2_000.0


def market(
    market_id: int,
    venue: str,
    *,
    asset: str = "HYPE",
    token_id: int = 3,
    maturity: date = MATURITY,
) -> MarketInfo:
    return MarketInfo(
        market_id=market_id,
        token_id=token_id,
        venue=venue,
        asset=asset,
        maturity=maturity,
        symbol=f"{venue}-{asset}USDT",
        name=f"{venue} {asset}",
    )


def execution(
    notional_usd: float,
    bid_vwap: float,
    ask_vwap: float,
    *,
    bid_sufficient: bool = True,
    ask_sufficient: bool = True,
    bid_top: float | None = None,
    ask_top: float | None = None,
) -> NotionalExecution:
    bid_top = bid_vwap if bid_top is None else bid_top
    ask_top = ask_vwap if ask_top is None else ask_top
    bid = ExecutionResult(
        target_usd=notional_usd,
        filled_usd=notional_usd if bid_sufficient else 1_500.0,
        depth_sufficient=bid_sufficient,
        vwap_apr=bid_vwap if bid_sufficient else None,
        top_apr=bid_top,
        impact_apr=0.0 if bid_sufficient else None,
        levels_used=1,
    )
    ask = ExecutionResult(
        target_usd=notional_usd,
        filled_usd=notional_usd if ask_sufficient else 1_500.0,
        depth_sufficient=ask_sufficient,
        vwap_apr=ask_vwap if ask_sufficient else None,
        top_apr=ask_top,
        impact_apr=0.0 if ask_sufficient else None,
        levels_used=1,
    )
    return NotionalExecution(notional_usd, bid, ask)


def observation(
    market_id: int,
    *,
    timestamp: int = TIMESTAMP,
    book_status: str = "ok",
    price_status: str = "ok",
    executions: tuple[NotionalExecution, ...] | None = None,
    snapshot_age_sec: int | None = 120,
    price_age_sec: int | None = 60,
    top_bid_apr: float | None = None,
    top_ask_apr: float | None = None,
) -> MarketExecutionObservation:
    return MarketExecutionObservation(
        market_id=market_id,
        timestamp=timestamp,
        book_status=book_status,
        snapshot_age_sec=snapshot_age_sec,
        price_status=price_status,
        price_age_sec=price_age_sec,
        collateral_price_usd=1.0 if price_status == "ok" else None,
        executions=executions,
        top_bid_apr=top_bid_apr,
        top_ask_apr=top_ask_apr,
    )


def executable_observation(
    market_id: int,
    bid_vwap: float,
    ask_vwap: float,
    *,
    timestamp: int = TIMESTAMP,
    bid_sufficient: bool = True,
    ask_sufficient: bool = True,
    bid_top: float | None = None,
    ask_top: float | None = None,
) -> MarketExecutionObservation:
    bid_top = bid_vwap if bid_top is None else bid_top
    ask_top = ask_vwap if ask_top is None else ask_top
    return observation(
        market_id,
        timestamp=timestamp,
        top_bid_apr=bid_top,
        top_ask_apr=ask_top,
        executions=(
            execution(
                NOTIONAL,
                bid_vwap,
                ask_vwap,
                bid_sufficient=bid_sufficient,
                ask_sufficient=ask_sufficient,
                bid_top=bid_top,
                ask_top=ask_top,
            ),
        ),
    )


def rows_for_pair(rows, short_market_id: int, long_market_id: int):
    return [
        row
        for row in rows
        if row["short_market_id"] == short_market_id
        and row["long_market_id"] == long_market_id
    ]


def test_opportunity_key_is_immutable_contract_identity():
    key = OpportunityKey(
        asset="HYPE",
        maturity=MATURITY,
        token_id=3,
        short_market_id=155,
        short_venue="HYPERLIQUID",
        long_market_id=201,
        long_venue="BYBIT",
        notional_usd=NOTIONAL,
    )

    assert key.asset == "HYPE"
    with pytest.raises(AttributeError):
        key.asset = "ETH"


def test_market_groups_require_asset_maturity_token_and_supported_venue():
    markets = [
        market(155, "HYPERLIQUID"),
        market(201, "BYBIT"),
        market(202, "BYBIT", token_id=9),
        market(203, "BYBIT", asset="ETH"),
        market(204, "BYBIT", maturity=date(2026, 10, 2)),
        market(205, "LIGHTER"),
    ]

    groups = build_market_groups(markets)

    assert [item.market_id for item in groups[("HYPE", MATURITY, 3)]] == [155, 201]
    assert [item.market_id for item in groups[("HYPE", MATURITY, 9)]] == [202]
    assert ("HYPE", MATURITY, 3) in groups
    assert all(item.venue != "LIGHTER" for group in groups.values() for item in group)


def test_three_venue_group_generates_six_directed_pairs():
    markets = [
        market(155, "HYPERLIQUID"),
        market(201, "BYBIT"),
        market(301, "BINANCE"),
    ]
    observations = {
        item.market_id: [executable_observation(item.market_id, 0.10, 0.11)]
        for item in markets
    }

    rows = build_executable_opportunities(
        markets, observations, notionals_usd=(NOTIONAL,)
    )
    pairs = {
        (row["short_market_id"], row["long_market_id"])
        for row in rows
    }

    assert pairs == {
        (155, 201),
        (201, 155),
        (155, 301),
        (301, 155),
        (201, 301),
        (301, 201),
    }
    assert all(short != long for short, long in pairs)


def test_same_venue_never_pairs_with_itself():
    markets = [market(155, "BYBIT"), market(156, "BYBIT")]
    observations = {
        item.market_id: [executable_observation(item.market_id, 0.10, 0.11)]
        for item in markets
    }

    assert build_executable_opportunities(
        markets, observations, notionals_usd=(NOTIONAL,)
    ) == ()


def test_directed_spreads_use_each_direction_bid_and_ask_independently():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [executable_observation(155, 0.109, 0.115)],
        201: [executable_observation(201, 0.058, 0.062)],
    }

    rows = build_executable_opportunities(
        markets, observations, notionals_usd=(NOTIONAL,)
    )
    forward = rows_for_pair(rows, 155, 201)[0]
    reverse = rows_for_pair(rows, 201, 155)[0]

    assert forward["executable_spread_apr"] == pytest.approx(0.047)
    assert reverse["executable_spread_apr"] == pytest.approx(-0.057)
    assert reverse["executable_spread_apr"] != pytest.approx(-0.047)


def test_only_exact_grid_timestamps_are_joined():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [executable_observation(155, 0.109, 0.115, timestamp=TIMESTAMP)],
        201: [
            executable_observation(
                201, 0.058, 0.062, timestamp=TIMESTAMP + 300
            )
        ],
    }

    rows = build_executable_opportunities(
        markets, observations, notionals_usd=(NOTIONAL,)
    )

    assert {row["timestamp"] for row in rows} == {TIMESTAMP, TIMESTAMP + 300}
    at_first = rows_for_pair(rows, 155, 201)[0]
    at_second = rows_for_pair(rows, 155, 201)[1]
    assert at_first["invalid_reason"] == "long_book_missing;long_price_missing"
    assert at_second["invalid_reason"] == "short_book_missing;short_price_missing"


def test_invalid_leg_rows_preserve_deterministic_reason_codes():
    cases = [
        (
            "short stale book",
            observation(155, book_status="stale", snapshot_age_sec=901),
            executable_observation(201, 0.058, 0.062),
            "short_book_stale",
        ),
        (
            "long missing book",
            executable_observation(155, 0.109, 0.115),
            observation(201, book_status="missing", snapshot_age_sec=None),
            "long_book_missing",
        ),
        (
            "short stale price",
            observation(155, price_status="stale", price_age_sec=901),
            executable_observation(201, 0.058, 0.062),
            "short_price_stale",
        ),
        (
            "long missing price",
            executable_observation(155, 0.109, 0.115),
            observation(201, price_status="missing", price_age_sec=None),
            "long_price_missing",
        ),
        (
            "short insufficient depth",
            executable_observation(
                155, 0.109, 0.115, bid_sufficient=False, bid_top=0.109
            ),
            executable_observation(201, 0.058, 0.062),
            "short_depth_insufficient",
        ),
        (
            "long insufficient depth",
            executable_observation(155, 0.109, 0.115),
            executable_observation(
                201, 0.058, 0.062, ask_sufficient=False, ask_top=0.062
            ),
            "long_depth_insufficient",
        ),
    ]

    for _, short_observation, long_observation, reason in cases:
        rows = build_executable_opportunities(
            [market(155, "HYPERLIQUID"), market(201, "BYBIT")],
            {155: [short_observation], 201: [long_observation]},
            notionals_usd=(NOTIONAL,),
        )
        row = rows_for_pair(rows, 155, 201)[0]
        assert row["fully_executable"] is False
        assert row["executable_spread_apr"] is None
        assert reason in row["invalid_reason"]


def test_top_of_book_spread_survives_insufficient_execution_depth():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [
            executable_observation(
                155, 0.109, 0.115, bid_top=0.109, ask_top=0.115
            )
        ],
        201: [
            executable_observation(
                201,
                0.058,
                0.062,
                ask_sufficient=False,
                bid_top=0.058,
                ask_top=0.062,
            )
        ],
    }

    row = rows_for_pair(
        build_executable_opportunities(
            markets, observations, notionals_usd=(NOTIONAL,)
        ),
        155,
        201,
    )[0]

    assert row["top_of_book_spread_apr"] == pytest.approx(0.047)
    assert row["executable_spread_apr"] is None
    assert row["fully_executable"] is False


def test_top_of_book_spread_survives_missing_collateral_price():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [
            executable_observation(
                155, 0.109, 0.115, bid_top=0.109, ask_top=0.115
            )
        ],
        201: [
            observation(
                201,
                price_status="missing",
                executions=None,
                top_bid_apr=0.058,
                top_ask_apr=0.062,
            )
        ],
    }

    row = rows_for_pair(
        build_executable_opportunities(
            markets, observations, notionals_usd=(NOTIONAL,)
        ),
        155,
        201,
    )[0]

    assert row["top_of_book_spread_apr"] == pytest.approx(0.047)
    assert row["executable_spread_apr"] is None
    assert row["fully_executable"] is False
    assert "long_price_missing" in row["invalid_reason"]


def test_top_of_book_spread_survives_stale_collateral_price():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [
            executable_observation(
                155, 0.109, 0.115, bid_top=0.109, ask_top=0.115
            )
        ],
        201: [
            observation(
                201,
                price_status="stale",
                price_age_sec=901,
                executions=None,
                top_bid_apr=0.058,
                top_ask_apr=0.062,
            )
        ],
    }

    row = rows_for_pair(
        build_executable_opportunities(
            markets, observations, notionals_usd=(NOTIONAL,)
        ),
        155,
        201,
    )[0]

    assert row["top_of_book_spread_apr"] == pytest.approx(0.047)
    assert row["executable_spread_apr"] is None
    assert row["fully_executable"] is False
    assert "long_price_stale" in row["invalid_reason"]


def test_observation_provenance_and_dte_are_preserved():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [
            executable_observation(
                155, 0.109, 0.115, bid_top=0.109, ask_top=0.115
            )
        ],
        201: [
            observation(
                201,
                snapshot_age_sec=180,
                price_age_sec=240,
                executions=(execution(NOTIONAL, 0.058, 0.062),),
            )
        ],
    }

    row = rows_for_pair(
        build_executable_opportunities(
            markets, observations, notionals_usd=(NOTIONAL,)
        ),
        155,
        201,
    )[0]

    assert row["dte_days"] == 32
    assert row["short_snapshot_age_sec"] == 120
    assert row["long_snapshot_age_sec"] == 180
    assert row["short_price_age_sec"] == 60
    assert row["long_price_age_sec"] == 240


def test_missing_requested_notional_is_diagnostic_not_nearest_match():
    markets = [market(155, "HYPERLIQUID"), market(201, "BYBIT")]
    observations = {
        155: [
            observation(
                155,
                executions=(execution(1_000.0, 0.109, 0.115),),
            )
        ],
        201: [executable_observation(201, 0.058, 0.062)],
    }

    row = rows_for_pair(
        build_executable_opportunities(
            markets, observations, notionals_usd=(NOTIONAL,)
        ),
        155,
        201,
    )[0]

    assert row["invalid_reason"] == "short_execution_missing"
    assert row["fully_executable"] is False
    assert row["executable_spread_apr"] is None
