from __future__ import annotations

from dataclasses import replace
from boros_research.alert_state import AlertStateStore
from boros_research.crossex_client import (
    CrossExCosts,
    CrossExGroup,
    CrossExLeg,
    CrossExPair,
    CrossExResponse,
)
from boros_research.live_benchmark import LiveBenchmarkResult
from boros_research.monitor import LiveMonitor, render_delivery_event, render_heartbeat


def pair(short="HYPERLIQUID", long="BYBIT", spread=0.047):
    return CrossExPair(
        base="HYPE",
        short_leg=CrossExLeg(190, "Hyperliquid", short, "HYPEUSDT", "HYPE", 0.11, 0.109),
        long_leg=CrossExLeg(192, "Bybit", long, "HYPEUSDT", "HYPE", 0.06, 0.062),
        gross_spread_apr=0.05,
        exec_spread_apr=spread,
        boros_impact_apr=0.003,
        maker_leg=None,
        costs=CrossExCosts(1, 1, 1, 1, 1, 1, 6, 0.001),
        capital_usd=6_000,
        net_fixed_apr=0.04,
        net_fixed_apr_on_capital=0.06,
        effective_leverage=1.6,
        est_profit_usd=120,
        seconds_to_maturity=2_000_000,
        reasons=(),
    )


def response(size):
    return CrossExResponse(
        notional_usd=size,
        as_of_timestamp=1_800_000_000,
        groups=(
            CrossExGroup(
                token_id=3,
                collateral="USDT",
                collateral_price_usd=1.0,
                maturity_timestamp=1_802_764_800,
                seconds_to_maturity=2_764_800,
                underlying="HYPE",
                pairs=(pair(),),
                warnings=(),
            ),
        ),
        warnings=(),
    )


class FakeBenchmark:
    percentile = 96.0

    def lookup_many(self, candidates):
        return {
            item.candidate_id: LiveBenchmarkResult(
                candidate_id=item.candidate_id,
                benchmark_level="dte",
                dte_bucket=item.dte_bucket,
                percentile_30d=self.percentile,
                percentile_90d=self.percentile,
                percentile_lifetime=95.0,
                sample_count_30d=100,
                sample_count_90d=100,
                sample_count_lifetime=100,
                historical_max_timestamp=1_800_000_000,
                benchmark_age_seconds=0,
                benchmark_fresh=True,
            )
            for item in candidates
        }


class FakeClient:
    def __init__(self):
        self.calls = []

    def fetch(self, notional):
        self.calls.append(notional)
        return response(notional)


class FakeState:
    def __init__(self):
        self.observations = []

    def observe(self, identity, timestamp, percentile, *, valid=True):
        self.observations.append((identity, timestamp, percentile, valid))
        return type("Decision", (), {"severity": None, "live_detected_minutes": 0})()

    def evaluate(self, identity, timestamp, percentile, *, valid=True):
        return self.observe(identity, timestamp, percentile, valid=valid)

    def commit_alert_delivered(self, _identity, _timestamp, _severity):
        return None

    def mark_missing_except(self, _ids, _timestamp):
        return None


def test_monitor_queries_all_sizes_aggregates_identity_and_dry_run_sends_nothing(tmp_path):
    client = FakeClient()
    sent = []
    monitor = LiveMonitor(
        client=client,
        benchmark=FakeBenchmark(),
        state=FakeState(),
        telegram_send=sent.append,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )

    result = monitor.run_once(dry_run=True)

    assert sorted(client.calls) == [10_000, 25_000, 50_000]
    assert result.group_counts == {10_000: 1, 25_000: 1, 50_000: 1}
    assert result.mapped_opportunity_count == 1
    assert result.benchmarkable_opportunity_count == 1
    assert result.normal_candidate_count == 1
    assert result.urgent_candidate_count == 0
    assert sent == []
    assert result.telegram_sent_count == 0


def test_missing_size_is_reported_without_reusing_old_cross_ex_data(tmp_path):
    class PartialClient(FakeClient):
        def fetch(self, notional):
            self.calls.append(notional)
            if notional == 25_000:
                raise RuntimeError("unavailable")
            return response(notional)

    client = PartialClient()
    monitor = LiveMonitor(
        client=client,
        benchmark=FakeBenchmark(),
        state=FakeState(),
        telegram_send=lambda _message: None,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )

    result = monitor.run_once(dry_run=True)

    assert result.unavailable_notionals == (25_000,)
    assert result.sizes_by_opportunity[0][25_000] is None


def test_stale_benchmark_suppresses_candidates(tmp_path):
    class StaleBenchmark(FakeBenchmark):
        def lookup_many(self, candidates):
            result = super().lookup_many(candidates)
            return {
                key: value.__class__(
                    candidate_id=value.candidate_id,
                    benchmark_level=value.benchmark_level,
                    dte_bucket=value.dte_bucket,
                    percentile_30d=value.percentile_30d,
                    percentile_90d=value.percentile_90d,
                    percentile_lifetime=value.percentile_lifetime,
                    sample_count_30d=value.sample_count_30d,
                    sample_count_90d=value.sample_count_90d,
                    sample_count_lifetime=value.sample_count_lifetime,
                    historical_max_timestamp=value.historical_max_timestamp,
                    benchmark_age_seconds=8 * 86400,
                    benchmark_fresh=False,
                )
                for key, value in result.items()
            }

    monitor = LiveMonitor(
        client=FakeClient(),
        benchmark=StaleBenchmark(),
        state=FakeState(),
        telegram_send=None,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )

    result = monitor.run_once(dry_run=True)

    assert result.normal_candidate_count == 0
    assert result.urgent_candidate_count == 0


class VariableBenchmark(FakeBenchmark):
    def __init__(self, percentile):
        self.percentile = percentile


def real_state_monitor(tmp_path, benchmark, telegram_send):
    state = AlertStateStore(tmp_path / "live_monitor.sqlite3", poll_interval_seconds=60)
    monitor = LiveMonitor(
        client=FakeClient(),
        benchmark=benchmark,
        state=state,
        telegram_send=telegram_send,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )
    return monitor, state


def test_p95_without_telegram_sender_keeps_state_armed(tmp_path):
    monitor, state = real_state_monitor(tmp_path, VariableBenchmark(96.0), None)

    monitor.run_once()

    identity = state.identities()[0]
    assert state.snapshot(identity).p95_armed is True


def test_p95_telegram_failure_keeps_state_armed(tmp_path):
    def fail(_message):
        raise RuntimeError("telegram unavailable")

    monitor, state = real_state_monitor(tmp_path, VariableBenchmark(96.0), fail)

    result = monitor.run_once()

    identity = state.identities()[0]
    assert result.messages
    assert state.snapshot(identity).p95_armed is True
    assert result.delivery_events[0].delivered is False


def test_next_successful_poll_delivers_and_disarms_p95(tmp_path):
    sent = []
    monitor, state = real_state_monitor(tmp_path, VariableBenchmark(96.0), None)

    monitor.run_once()
    monitor.telegram_send = sent.append
    result = monitor.run_once()

    identity = state.identities()[0]
    assert len(sent) == 1
    assert state.snapshot(identity).p95_armed is False
    assert result.telegram_sent_count == 1
    assert result.delivery_events[0].delivered is True
    assert "SENT" in render_delivery_event(result.delivery_events[0])


def test_failed_p99_delivery_keeps_p95_and_p99_armed(tmp_path):
    def fail(_message):
        raise RuntimeError("telegram unavailable")

    monitor, state = real_state_monitor(tmp_path, VariableBenchmark(99.0), fail)

    result = monitor.run_once()

    identity = state.identities()[0]
    snapshot = state.snapshot(identity)
    assert snapshot.p95_armed is True
    assert snapshot.p99_armed is True
    assert result.delivery_events[0].severity == "URGENT"
    assert result.delivery_events[0].delivered is False


def test_successful_p95_then_p99_sends_urgent_escalation(tmp_path):
    sent = []
    benchmark = VariableBenchmark(96.0)
    monitor, state = real_state_monitor(tmp_path, benchmark, sent.append)

    first = monitor.run_once()
    benchmark.percentile = 99.0
    second = monitor.run_once()

    identity = state.identities()[0]
    snapshot = state.snapshot(identity)
    assert first.normal_candidate_count == 1
    assert second.urgent_candidate_count == 1
    assert len(sent) == 2
    assert "歷史前 5%" in sent[0]
    assert "歷史前 1%" in sent[1]
    assert snapshot.p95_armed is False
    assert snapshot.p99_armed is False


def test_monitor_retains_cross_ex_warnings_for_diagnostics_only(tmp_path):
    class WarningClient(FakeClient):
        def fetch(self, notional):
            raw = super().fetch(notional)
            return replace(
                raw,
                warnings=(f"response warning {notional}",),
                groups=(replace(raw.groups[0], warnings=(f"group warning {notional}",)),),
            )

    monitor = LiveMonitor(
        client=WarningClient(),
        benchmark=FakeBenchmark(),
        state=FakeState(),
        telegram_send=None,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )

    result = monitor.run_once(dry_run=True)

    assert len(result.warnings) == 6
    assert "response warning 10000" in result.warnings
    assert "group warning 50000" in result.warnings


def test_heartbeat_contains_compact_cycle_observability(tmp_path):
    monitor = LiveMonitor(
        client=FakeClient(),
        benchmark=FakeBenchmark(),
        state=FakeState(),
        telegram_send=None,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )
    result = monitor.run_once(dry_run=True)
    result = replace(
        result,
        normal_candidate_count=1,
        urgent_candidate_count=0,
        warnings=("w1", "w2", "w3", "w4", "w5", "w6", "w7"),
        benchmark_age_seconds=int(12.7 * 3600),
    )

    heartbeat = render_heartbeat(result)

    assert "✓ CrossEx" in heartbeat
    assert "opps 1" in heartbeat
    assert "benchmarked 1" in heartbeat
    assert "P95 1" in heartbeat
    assert "P99 0" in heartbeat
    assert "sent 0" in heartbeat
    assert "warnings 7" in heartbeat
    assert "benchmark 12.7h" in heartbeat


def test_run_forever_prints_one_heartbeat_for_each_successful_cycle(
    tmp_path, monkeypatch, capsys
):
    monitor = LiveMonitor(
        client=FakeClient(),
        benchmark=FakeBenchmark(),
        state=FakeState(),
        telegram_send=None,
        now_timestamp=lambda: 1_800_000_000,
        database_path=tmp_path / "unused.duckdb",
    )
    result = monitor.run_once(dry_run=True)
    calls = 0

    def cycle(*, dry_run):
        nonlocal calls
        calls += 1
        if calls == 1:
            return result
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "run_once", cycle)
    monkeypatch.setattr("boros_research.monitor.time.sleep", lambda _seconds: None)

    try:
        monitor.run_forever(dry_run=True)
    except KeyboardInterrupt:
        pass

    output = capsys.readouterr().out
    assert output.count("✓ CrossEx") == 1
