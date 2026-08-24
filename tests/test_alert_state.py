from __future__ import annotations

from datetime import date

from boros_research.alert_state import AlertIdentity, AlertStateStore


IDENTITY = AlertIdentity("HYPE", date(2026, 9, 25), "HYPERLIQUID", "BYBIT", 3)
OTHER = AlertIdentity("HYPE", date(2026, 9, 25), "BYBIT", "HYPERLIQUID", 3)


def observe(store, timestamp, percentile, *, valid=True):
    decision = store.observe(IDENTITY, timestamp, percentile, valid=valid)
    if decision.severity is not None:
        store.commit_alert_delivered(IDENTITY, timestamp, decision.severity)
    return decision


def test_first_p95_and_p99_severity_and_escalation():
    store = AlertStateStore(":memory:")

    normal = observe(store, 1_000, 96.0)
    assert normal.severity == "NORMAL"
    assert observe(store, 1_060, 97.0).severity is None
    urgent = observe(store, 1_120, 99.0)
    assert urgent.severity == "URGENT"

    fresh = AlertStateStore(":memory:")
    assert observe(fresh, 1_000, 99.0).severity == "URGENT"
    assert observe(fresh, 1_060, 99.0).severity is None

    boundary = AlertStateStore(":memory:")
    assert observe(boundary, 1_000, 95.0).severity == "NORMAL"
    assert observe(boundary, 1_060, 94.999).severity is None


def test_observe_does_not_disarm_before_delivery_commit():
    store = AlertStateStore(":memory:")

    decision = store.observe(IDENTITY, 1_000, 96.0)

    assert decision.severity == "NORMAL"
    assert store.snapshot(IDENTITY).p95_armed is True

    store.commit_alert_delivered(IDENTITY, 1_000, "NORMAL")
    assert store.snapshot(IDENTITY).p95_armed is False


def test_rearm_requires_six_hours_below_and_unknown_does_not_count():
    store = AlertStateStore(":memory:")
    assert observe(store, 1_000, 96.0).severity == "NORMAL"
    observe(store, 1_060, 94.0)
    for timestamp in range(1_120, 1_060 + 6 * 3600, 60):
        observe(store, timestamp, 94.0)
    assert observe(store, 1_060 + 6 * 3600, 96.0).severity == "NORMAL"

    observe(store, 1_060 + 6 * 3600 + 60, 94.0)
    observe(store, 1_060 + 6 * 3600 + 120, None, valid=False)
    observe(store, 1_060 + 12 * 3600, 94.0)
    assert observe(store, 1_060 + 12 * 3600 + 60, 96.0).severity is None


def test_p99_rearm_is_independent_and_live_runs_break_on_unknown():
    store = AlertStateStore(":memory:")
    assert observe(store, 1_000, 99.0).severity == "URGENT"
    state = store.snapshot(IDENTITY)
    assert state.p95_run_start == 1_000
    assert state.p99_run_start == 1_000
    observe(store, 1_060, 99.0)
    assert store.snapshot(IDENTITY).p99_observation_count == 2
    observe(store, 1_120, None, valid=False)
    state = store.snapshot(IDENTITY)
    assert state.p95_run_start is None
    assert state.p99_run_start is None


def test_state_survives_sqlite_reopen_and_direction_is_independent(tmp_path):
    path = tmp_path / "live_monitor.sqlite3"
    first = AlertStateStore(path)
    assert observe(first, 1_000, 96.0).severity == "NORMAL"
    first.close()

    reopened = AlertStateStore(path)
    assert reopened.snapshot(IDENTITY).p95_armed is False
    assert reopened.snapshot(OTHER).p95_armed is True

    different_maturity = AlertIdentity("HYPE", date(2026, 9, 26), "HYPERLIQUID", "BYBIT", 3)
    assert reopened.snapshot(different_maturity).p95_armed is True


def test_missing_identity_ends_live_run_without_rearming(tmp_path):
    store = AlertStateStore(tmp_path / "state.sqlite3")
    assert observe(store, 1_000, 96.0).severity == "NORMAL"
    store.mark_missing_except(set(), 1_060)
    snapshot = store.snapshot(IDENTITY)
    assert snapshot.p95_run_start is None
    assert snapshot.p95_below_since is None
    assert snapshot.p95_armed is False


def test_normal_polls_extend_live_persistence():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    observe(store, 1_000, 96.0)
    observe(store, 1_060, 96.0)
    observe(store, 1_120, 96.0)

    snapshot = store.snapshot(IDENTITY)
    assert snapshot.p95_run_start == 1_000
    assert snapshot.p95_run_last_success == 1_120
    assert snapshot.p95_observation_count == 3


def test_short_poll_jitter_within_tolerance_extends_live_persistence():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    observe(store, 1_000, 96.0)
    observe(store, 1_060, 96.0)
    observe(store, 1_180, 96.0)

    snapshot = store.snapshot(IDENTITY)
    assert snapshot.p95_run_start == 1_000
    assert snapshot.p95_run_last_success == 1_180
    assert snapshot.p95_observation_count == 3


def test_long_gap_resets_live_persistence_without_rearming():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    observe(store, 1_000, 96.0)
    observe(store, 1_060, 96.0)
    decision = observe(store, 1_060 + 3_600, 96.0)

    snapshot = store.snapshot(IDENTITY)
    assert decision.live_detected_minutes == 0
    assert snapshot.p95_run_start == 1_060 + 3_600
    assert snapshot.p95_run_last_success == 1_060 + 3_600
    assert snapshot.p95_observation_count == 1
    assert snapshot.p95_armed is False


def test_long_gap_below_threshold_does_not_count_toward_rearm():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    observe(store, 1_000, 96.0)
    observe(store, 1_060, 94.0)
    long_gap_timestamp = 1_060 + 3_600
    observe(store, long_gap_timestamp, 94.0)
    final_timestamp = long_gap_timestamp + 6 * 3600 - 1
    observe(store, final_timestamp, 94.0)

    snapshot = store.snapshot(IDENTITY)
    assert snapshot.p95_armed is False
    assert snapshot.p95_below_since == final_timestamp


def test_sqlite_reopen_long_gap_resets_live_persistence(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = AlertStateStore(path, poll_interval_seconds=60)
    observe(first, 1_000, 96.0)
    observe(first, 1_060, 96.0)
    first.close()

    reopened = AlertStateStore(path, poll_interval_seconds=60)
    decision = observe(reopened, 1_060 + 3_600, 96.0)

    assert decision.live_detected_minutes == 0
    assert reopened.snapshot(IDENTITY).p95_run_start == 1_060 + 3_600


def test_failed_poll_breaks_live_persistence():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    observe(store, 1_000, 96.0)
    observe(store, 1_060, 96.0)
    observe(store, 1_120, None, valid=False)
    decision = observe(store, 1_180, 96.0)

    snapshot = store.snapshot(IDENTITY)
    assert decision.live_detected_minutes == 0
    assert snapshot.p95_run_start == 1_180
    assert snapshot.p95_observation_count == 1


def test_p99_escalation_exposes_durable_p95_run_duration():
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    first = store.observe(IDENTITY, 1_000, 96.0)
    store.commit_alert_delivered(IDENTITY, 1_000, first.severity)
    final_timestamp = 1_000 + 163 * 60
    for timestamp in range(1_060, final_timestamp, 60):
        store.observe(IDENTITY, timestamp, 96.0)

    decision = store.observe(IDENTITY, final_timestamp, 99.0)

    assert decision.severity == "URGENT"
    assert decision.p95_live_detected_minutes == 163
