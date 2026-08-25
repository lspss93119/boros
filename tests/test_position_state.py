from __future__ import annotations

from boros_research.position_models import Attribution, HedgeChecks, StrategySnapshot
from boros_research.position_state import (
    HEDGE_RECOVERED,
    HEDGE_WARNING,
    MATURITY_1D,
    MATURITY_14D,
    MATURITY_3D,
    MATURITY_7D,
    NEW_STRATEGY,
    STRATEGY_DISAPPEARED,
    PositionStateStore,
)


def strategy(
    strategy_id: str = "strategy-1",
    *,
    fully_hedged: bool = True,
    seconds_to_maturity: int = 20 * 86400,
) -> StrategySnapshot:
    return StrategySnapshot(
        strategy_id=strategy_id,
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
        seconds_to_maturity=seconds_to_maturity,
        notional_mismatch_usd=0.0,
        attribution=Attribution("test", 1.0, True, False),
        warnings=(),
    )


def kinds(events):
    return [event.kind for event in events]


def commit(store, events, timestamp=1_000):
    for event in events:
        assert store.commit_event(event, timestamp) is True


def test_first_successful_observation_creates_retryable_new_event():
    store = PositionStateStore(":memory:")
    first = store.observe_success([strategy()], 1_000)

    assert kinds(first) == [NEW_STRATEGY]
    assert kinds(store.observe_success([strategy()], 1_060)) == [NEW_STRATEGY]

    commit(store, first)
    assert store.observe_success([strategy()], 1_120) == ()


def test_initial_unhedged_strategy_has_new_only_until_new_is_delivered():
    store = PositionStateStore(":memory:")

    events = store.observe_success([strategy(fully_hedged=False)], 1_000)

    assert kinds(events) == [NEW_STRATEGY]
    commit(store, events)
    assert store.observe_success([strategy(fully_hedged=False)], 1_060) == ()


def test_hedge_warning_and_recovery_are_fully_hedged_transitions():
    store = PositionStateStore(":memory:")
    commit(store, store.observe_success([strategy()], 1_000))

    warning = store.observe_success([strategy(fully_hedged=False)], 1_060)
    assert kinds(warning) == [HEDGE_WARNING]
    commit(store, warning, 1_060)
    assert store.observe_success([strategy(fully_hedged=False)], 1_120) == ()

    recovered = store.observe_success([strategy(fully_hedged=True)], 1_180)
    assert kinds(recovered) == [HEDGE_RECOVERED]


def test_failed_hedge_warning_is_discarded_after_recovery():
    store = PositionStateStore(":memory:")
    commit(store, store.observe_success([strategy()], 1_000))

    assert kinds(store.observe_success([strategy(fully_hedged=False)], 1_060)) == [HEDGE_WARNING]
    assert store.observe_success([strategy(fully_hedged=True)], 1_120) == ()


def test_maturity_thresholds_are_inclusive_and_advance_in_order():
    store = PositionStateStore(":memory:")
    current = strategy(seconds_to_maturity=14 * 86400)
    events = store.observe_success([current], 1_000)
    assert MATURITY_14D in kinds(events)
    commit(store, events)

    for threshold, kind, seconds in (
        (7, MATURITY_7D, 7 * 86400),
        (3, MATURITY_3D, 3 * 86400),
        (1, MATURITY_1D, 86400),
    ):
        events = store.observe_success([strategy(seconds_to_maturity=seconds)], 1_000 + threshold)
        assert kinds(events) == [kind]
        commit(store, events, 1_000 + threshold)

    assert store.observe_success([strategy(seconds_to_maturity=1)], 2_000) == ()


def test_startup_maturity_suppression_selects_only_smallest_satisfied_threshold():
    seven_day = PositionStateStore(":memory:")
    events = seven_day.observe_success([strategy(seconds_to_maturity=int(6.5 * 86400))], 1_000)
    assert [kind for kind in kinds(events) if kind.startswith("MATURITY")] == [MATURITY_7D]

    one_day = PositionStateStore(":memory:")
    events = one_day.observe_success([strategy(seconds_to_maturity=int(0.8 * 86400))], 1_000)
    assert [kind for kind in kinds(events) if kind.startswith("MATURITY")] == [MATURITY_1D]


def test_expired_strategy_has_no_maturity_reminder():
    store = PositionStateStore(":memory:")

    events = store.observe_success([strategy(seconds_to_maturity=0)], 1_000)

    assert not any(kind.startswith("MATURITY") for kind in kinds(events))


def test_failed_maturity_event_remains_retryable():
    store = PositionStateStore(":memory:")
    events = store.observe_success([strategy(seconds_to_maturity=14 * 86400)], 1_000)
    new = [event for event in events if event.kind == NEW_STRATEGY][0]
    maturity = [event for event in events if event.kind == MATURITY_14D][0]
    assert store.commit_event(new, 1_000) is True
    assert kinds(store.observe_success([strategy(seconds_to_maturity=14 * 86400)], 1_060)) == [
        MATURITY_14D
    ]
    assert store.commit_event(maturity, 1_060) is True


def test_three_consecutive_successful_absences_create_disappearance():
    store = PositionStateStore(":memory:")
    commit(store, store.observe_success([strategy()], 1_000))

    assert store.observe_success([], 1_060) == ()
    assert store.observe_success([], 1_120) == ()
    events = store.observe_success([], 1_180)
    assert kinds(events) == [STRATEGY_DISAPPEARED]
    assert store.snapshot("strategy-1").successful_absence_count == 3

    commit(store, events, 1_180)
    assert store.snapshot("strategy-1").active is False


def test_unknown_breaks_absence_evidence_and_does_not_change_last_seen():
    store = PositionStateStore(":memory:")
    commit(store, store.observe_success([strategy()], 1_000))
    store.observe_success([], 1_060)
    store.observe_unknown(1_120)

    assert store.snapshot("strategy-1").successful_absence_count == 0
    assert store.snapshot("strategy-1").last_seen_at == 1_000
    assert store.observe_success([], 1_180) == ()
    assert store.observe_success([], 1_240) == ()


def test_reappearance_before_third_absence_resets_count():
    store = PositionStateStore(":memory:")
    commit(store, store.observe_success([strategy()], 1_000))
    store.observe_success([], 1_060)
    store.observe_success([], 1_120)

    assert store.observe_success([strategy()], 1_180) == ()
    assert store.snapshot("strategy-1").successful_absence_count == 0
    assert store.observe_success([], 1_240) == ()
    assert store.observe_success([], 1_300) == ()


def test_confirmed_disappearance_reappearance_starts_new_lifecycle_without_replaying_maturity(
):
    store = PositionStateStore(":memory:")
    first = store.observe_success([strategy(seconds_to_maturity=14 * 86400)], 1_000)
    commit(store, first)
    store.observe_success([], 1_060)
    store.observe_success([], 1_120)
    disappeared = store.observe_success([], 1_180)
    commit(store, disappeared, 1_180)

    reappeared = store.observe_success([strategy(seconds_to_maturity=14 * 86400)], 2_000)

    assert kinds(reappeared) == [NEW_STRATEGY]
    assert store.snapshot("strategy-1").lifecycle == 2
    assert store.snapshot("strategy-1").maturity_14d_delivered is True


def test_position_state_reopens_with_durable_lifecycle(tmp_path):
    path = tmp_path / "position_monitor.sqlite3"
    first = PositionStateStore(path)
    events = first.observe_success([strategy()], 1_000)
    commit(first, events)
    first.close()

    reopened = PositionStateStore(path)

    snapshot = reopened.snapshot("strategy-1")
    assert snapshot is not None
    assert snapshot.active is True
    assert snapshot.last_snapshot.strategy_id == "strategy-1"
