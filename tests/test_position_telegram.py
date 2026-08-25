from __future__ import annotations

from boros_research.position_models import Attribution, HedgeChecks, StrategySnapshot
from boros_research.position_state import (
    HEDGE_RECOVERED,
    HEDGE_WARNING,
    MATURITY_14D,
    NEW_STRATEGY,
    STRATEGY_DISAPPEARED,
    PositionEvent,
)
from boros_research.position_telegram import (
    format_position_event,
    render_position_snapshot,
)


def snapshot(*, fully_hedged: bool = True, seconds: int = 14 * 86400) -> StrategySnapshot:
    return StrategySnapshot(
        strategy_id="strategy-abcdef1234567890",
        base="ETH",
        maturity=1_790_000_000,
        legs=(),
        hedge={"venue": "HYPERLIQUID"},
        hedge_checks=HedgeChecks(0.98, 0.97, 1.01, fully_hedged),
        capital_usd=6_000.0,
        capital_split={"boros": 3_000.0, "perp": 3_000.0},
        realized_pnl_usd=12.0,
        realized_apr=0.11,
        spread=0.15,
        locked_apr_on_capital=0.22,
        expected_pnl_to_maturity_usd=132.0,
        seconds_to_maturity=seconds,
        notional_mismatch_usd=100.0,
        attribution=Attribution("crossex", 0.95, True, False),
        warnings=("⚠️ CrossEx：diagnostic only",),
    )


def event(kind: str, *, fully_hedged: bool = True, **kwargs) -> PositionEvent:
    current = snapshot(fully_hedged=fully_hedged)
    return PositionEvent(
        kind=kind,
        strategy_id=current.strategy_id,
        lifecycle=1,
        snapshot=current,
        current_fully_hedged=fully_hedged,
        **kwargs,
    )


def test_new_message_contains_approved_position_snapshot_fields_without_warning_dump():
    message = format_position_event(event(NEW_STRATEGY), snapshot(fully_hedged=True))

    assert "NEW STRATEGY" in message
    assert "ETH" in message
    assert "到期" in message
    assert "鎖定利差" in message
    assert "鎖定資本 APR" in message
    assert "模型資本" in message
    assert "到期預估淨收益" in message
    assert "Hedge：已對沖" in message
    assert "歸因信心" in message
    assert "⚠️ CrossEx：" not in message


def test_hedge_messages_show_transition_ratios_and_mismatch():
    warning = format_position_event(
        event(HEDGE_WARNING, fully_hedged=False, previous_fully_hedged=True), snapshot(fully_hedged=False)
    )
    recovered = format_position_event(
        event(HEDGE_RECOVERED, fully_hedged=True, previous_fully_hedged=False), snapshot(fully_hedged=True)
    )

    assert "HEDGE WARNING" in warning
    assert "True → False" in warning
    assert "Boros match ratio" in warning
    assert "Perp match ratio" in warning
    assert "Boros vs Perp ratio" in warning
    assert "名義額差" in warning
    assert "HEDGE RECOVERED" in recovered
    assert "False → True" in recovered


def test_maturity_message_is_informational_and_has_no_roll_or_close_advice():
    message = format_position_event(
        event(MATURITY_14D), snapshot(seconds=14 * 86400)
    )

    assert "MATURITY REMINDER｜14D" in message
    assert "DTE 14 天" in message
    assert "鎖定利差" in message
    assert "鎖定資本 APR" in message
    assert "到期預估淨收益" in message
    assert "roll" not in message.lower()
    assert "close" not in message.lower()


def test_disappearance_message_has_compact_id_last_seen_and_hedge_state():
    message = format_position_event(
        event(STRATEGY_DISAPPEARED, fully_hedged=False), snapshot(fully_hedged=False)
    )

    assert "STRATEGY DISAPPEARED" in message
    assert "strategy-abcdef" in message
    assert "最後觀察" in message
    assert "Hedge：未對沖" in message


def test_snapshot_renderer_is_separate_and_renders_diagnostics_without_raw_warnings():
    message = render_position_snapshot(snapshot())

    assert "strategy-abcdef" in message
    assert "ETH" in message
    assert "Hedge：已對沖" in message
    assert "borosMatchRatio" in message
    assert "⚠️ CrossEx：" not in message
