from __future__ import annotations

import threading

from boros_research.position_models import Attribution, HedgeChecks, PositionsSnapshot, StrategySnapshot
from boros_research.position_state import (
    HEDGE_WARNING,
    STRATEGY_DISAPPEARED,
    PositionStateStore,
)
from boros_research.position_monitor import PositionMonitor


ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


def strategy(*, fully_hedged: bool = True, seconds: int = 20 * 86400) -> StrategySnapshot:
    return StrategySnapshot(
        strategy_id="strategy-1",
        base="ETH",
        maturity=1_790_000_000,
        legs=(),
        hedge=None,
        hedge_checks=HedgeChecks(1.0, 1.0, 1.0, fully_hedged),
        capital_usd=1_000.0,
        capital_split={},
        realized_pnl_usd=0.0,
        realized_apr=0.0,
        spread=0.1,
        locked_apr_on_capital=0.2,
        expected_pnl_to_maturity_usd=100.0,
        seconds_to_maturity=seconds,
        notional_mismatch_usd=0.0,
        attribution=Attribution("test", 1.0, True, False),
        warnings=(),
    )


class FakeClient:
    def __init__(self, strategies=None):
        self.strategies = list(strategies if strategies is not None else [strategy()])
        self.positions = PositionsSnapshot(({"groupId": "exposure-1"},), 1_788_100_000, ())
        self.strategy_error: Exception | None = None
        self.positions_error: Exception | None = None
        self.calls: list[str] = []
        self.barrier: threading.Barrier | None = None

    def fetch_strategy(self, address):
        self.calls.append(f"strategy:{address}")
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        if self.strategy_error is not None:
            raise self.strategy_error
        return tuple(self.strategies)

    def fetch_positions(self):
        self.calls.append("positions")
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        if self.positions_error is not None:
            raise self.positions_error
        return self.positions


def monitor_for(client, state, sent):
    return PositionMonitor(
        client=client,
        state=state,
        address=ADDRESS,
        telegram_send=sent.append,
        now_timestamp=lambda: 1_800_000_000,
    )


def test_strategy_and_positions_are_fetched_concurrently_and_new_is_sent():
    client = FakeClient()
    client.barrier = threading.Barrier(2)
    state = PositionStateStore(":memory:")
    sent: list[str] = []
    monitor = monitor_for(client, state, sent)

    result = monitor.run_once()

    assert set(client.calls) == {f"strategy:{ADDRESS}", "positions"}
    assert result.strategy_success is True
    assert result.auxiliary_success is True
    assert result.telegram_sent_count == 1
    assert len(sent) == 1
    assert "NEW STRATEGY" in sent[0]


def test_positions_failure_does_not_suppress_valid_strategy_cycle():
    client = FakeClient()
    client.positions_error = RuntimeError("positions unavailable")
    state = PositionStateStore(":memory:")
    sent: list[str] = []

    result = monitor_for(client, state, sent).run_once()

    assert result.strategy_success is True
    assert result.auxiliary_success is False
    assert result.telegram_sent_count == 1
    assert "positions unavailable" not in "\n".join(result.warnings)


def test_strategy_failure_is_unknown_and_does_not_create_empty_inventory():
    state = PositionStateStore(":memory:")
    first_client = FakeClient()
    sent: list[str] = []
    monitor = monitor_for(first_client, state, sent)
    monitor.run_once()

    first_client.strategy_error = RuntimeError("strategy unavailable")
    result = monitor.run_once()

    assert result.strategy_success is False
    assert result.primary_unknown is True
    assert result.events == ()
    assert state.snapshot("strategy-1").successful_absence_count == 0


def test_successful_empty_inventory_advances_absence_to_disappearance():
    state = PositionStateStore(":memory:")
    client = FakeClient()
    sent: list[str] = []
    monitor = monitor_for(client, state, sent)
    monitor.run_once()

    client.strategies = []
    assert monitor.run_once().events == ()
    assert monitor.run_once().events == ()
    result = monitor.run_once()

    assert [event.kind for event in result.events] == [STRATEGY_DISAPPEARED]
    assert result.telegram_sent_count == 1
    assert "STRATEGY DISAPPEARED" in sent[-1]


def test_failed_disappearance_delivery_remains_retryable():
    state = PositionStateStore(":memory:")
    client = FakeClient()
    attempts = 0

    def sender(_message):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("telegram down")

    monitor = PositionMonitor(
        client=client,
        state=state,
        address=ADDRESS,
        telegram_send=sender,
        now_timestamp=lambda: 1_800_000_000,
    )
    monitor.run_once()
    client.strategies = []
    monitor.run_once()
    monitor.run_once()
    failed = monitor.run_once()
    retried = monitor.run_once()

    assert [event.kind for event in failed.events] == [STRATEGY_DISAPPEARED]
    assert failed.telegram_sent_count == 0
    assert [event.kind for event in retried.events] == [STRATEGY_DISAPPEARED]
    assert retried.telegram_sent_count == 1
    assert state.snapshot("strategy-1").active is False


def test_failed_event_delivery_does_not_crash_or_consume_event():
    state = PositionStateStore(":memory:")
    client = FakeClient([strategy()])
    attempts = 0

    def sender(_message):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("telegram down")

    monitor = PositionMonitor(
        client=client,
        state=state,
        address=ADDRESS,
        telegram_send=sender,
        now_timestamp=lambda: 1_800_000_000,
    )

    first = monitor.run_once()
    second = monitor.run_once()

    assert first.telegram_sent_count == 0
    assert second.telegram_sent_count == 1
    assert attempts == 2
    assert state.snapshot("strategy-1").new_event_delivered is True


def test_dry_run_renders_but_never_sends_or_commits():
    state = PositionStateStore(":memory:")
    client = FakeClient()
    sent: list[str] = []
    monitor = monitor_for(client, state, sent)

    result = monitor.run_once(dry_run=True)

    assert result.telegram_sent_count == 0
    assert result.messages
    assert sent == []
    assert state.snapshot("strategy-1").new_event_delivered is False
    assert monitor.run_once().telegram_sent_count == 1


def test_hedge_transition_is_reported_after_new_delivery():
    state = PositionStateStore(":memory:")
    client = FakeClient()
    sent: list[str] = []
    monitor = monitor_for(client, state, sent)
    monitor.run_once()

    client.strategies = [strategy(fully_hedged=False)]
    result = monitor.run_once()

    assert [event.kind for event in result.events] == [HEDGE_WARNING]
    assert "HEDGE WARNING" in sent[-1]
