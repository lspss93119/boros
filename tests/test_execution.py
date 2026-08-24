import pytest

from boros_research.config import NOTIONALS_USD
from boros_research.orderbook import BookLevel, OrderBookSnapshot
from boros_research.execution import (
    ExecutionResult,
    NotionalExecution,
    simulate_notional_ladder,
    walk_book,
)


def _bids() -> tuple[BookLevel, ...]:
    return (
        BookLevel(rate_apr=0.10, size_collateral=1000.0),
        BookLevel(rate_apr=0.09, size_collateral=2000.0),
    )


def _asks() -> tuple[BookLevel, ...]:
    return (
        BookLevel(rate_apr=0.11, size_collateral=1000.0),
        BookLevel(rate_apr=0.12, size_collateral=3000.0),
    )


def test_bid_vwap_uses_partial_final_level():
    result = walk_book(_bids(), 1.0, 1500.0, side="bid")

    assert result == ExecutionResult(
        target_usd=1500.0,
        filled_usd=1500.0,
        depth_sufficient=True,
        vwap_apr=pytest.approx(0.09666666666666666),
        top_apr=0.10,
        impact_apr=pytest.approx(0.003333333333333341),
        levels_used=2,
    )


def test_ask_vwap_uses_partial_final_level():
    result = walk_book(_asks(), 1.0, 1500.0, side="ask")

    assert result.vwap_apr == pytest.approx(0.11333333333333334)
    assert result.target_usd == 1500.0
    assert result.filled_usd == 1500.0
    assert result.depth_sufficient is True
    assert result.top_apr == 0.11
    assert result.impact_apr == pytest.approx(0.003333333333333341)
    assert result.levels_used == 2


def test_insufficient_depth_never_extrapolates():
    result = walk_book(_bids(), 1.0, 5000.0, side="bid")

    assert result.filled_usd == 3000.0
    assert result.depth_sufficient is False
    assert result.vwap_apr is None
    assert result.impact_apr is None
    assert result.top_apr == 0.10
    assert result.levels_used == 2


def test_token_collateral_size_is_converted_to_usd():
    result = walk_book(
        (BookLevel(rate_apr=0.10, size_collateral=2.0),),
        50.0,
        100.0,
        side="bid",
    )

    assert result.filled_usd == 100.0
    assert result.depth_sufficient is True
    assert result.vwap_apr == 0.10


def test_bid_impact_is_top_minus_vwap():
    result = walk_book(_bids(), 1.0, 1500.0, side="bid")

    assert result.impact_apr == pytest.approx(0.10 - 0.09666666666666666)


def test_ask_impact_is_vwap_minus_top():
    result = walk_book(_asks(), 1.0, 1500.0, side="ask")

    assert result.impact_apr == pytest.approx(0.11333333333333334 - 0.11)


def test_empty_book_is_insufficient_not_error():
    result = walk_book((), 1.0, 1000.0, side="bid")

    assert result == ExecutionResult(
        target_usd=1000.0,
        filled_usd=0.0,
        depth_sufficient=False,
        vwap_apr=None,
        top_apr=None,
        impact_apr=None,
        levels_used=0,
    )


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_collateral_price_raises(price):
    with pytest.raises(ValueError, match="collateral_price_usd"):
        walk_book(_bids(), price, 1000.0, side="bid")


@pytest.mark.parametrize("target", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_target_usd_raises(target):
    with pytest.raises(ValueError, match="target_usd"):
        walk_book(_bids(), 1.0, target, side="bid")


@pytest.mark.parametrize(
    "level",
    [
        BookLevel(rate_apr=float("nan"), size_collateral=1.0),
        BookLevel(rate_apr=float("inf"), size_collateral=1.0),
        BookLevel(rate_apr=0.1, size_collateral=float("nan")),
        BookLevel(rate_apr=0.1, size_collateral=float("inf")),
        BookLevel(rate_apr=0.1, size_collateral=0.0),
        BookLevel(rate_apr=0.1, size_collateral=-1.0),
    ],
)
def test_invalid_canonical_level_raises(level):
    with pytest.raises(ValueError, match="BookLevel"):
        walk_book((level,), 1.0, 100.0, side="bid")


def test_wrong_bid_order_raises():
    levels = (
        BookLevel(rate_apr=0.09, size_collateral=1000.0),
        BookLevel(rate_apr=0.10, size_collateral=1000.0),
    )

    with pytest.raises(ValueError, match="descending"):
        walk_book(levels, 1.0, 100.0, side="bid")


def test_wrong_ask_order_raises():
    levels = (
        BookLevel(rate_apr=0.12, size_collateral=1000.0),
        BookLevel(rate_apr=0.11, size_collateral=1000.0),
    )

    with pytest.raises(ValueError, match="ascending"):
        walk_book(levels, 1.0, 100.0, side="ask")


def test_exact_level_boundary():
    result = walk_book(_bids(), 1.0, 1000.0, side="bid")

    assert result.filled_usd == 1000.0
    assert result.depth_sufficient is True
    assert result.vwap_apr == 0.10
    assert result.impact_apr == 0.0
    assert result.levels_used == 1


def test_notional_ladder_uses_configured_values_in_order():
    snapshot = OrderBookSnapshot(
        timestamp=1787443200,
        block_number=1,
        bids=_bids(),
        asks=_asks(),
    )

    results = simulate_notional_ladder(snapshot, 1.0)

    assert [result.notional_usd for result in results] == list(NOTIONALS_USD)
    assert all(isinstance(result, NotionalExecution) for result in results)
    assert all(isinstance(result.bid, ExecutionResult) for result in results)
    assert all(isinstance(result.ask, ExecutionResult) for result in results)


def test_invalid_side_raises():
    with pytest.raises(ValueError, match="side"):
        walk_book(_bids(), 1.0, 100.0, side="long")
