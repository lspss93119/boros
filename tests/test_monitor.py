from __future__ import annotations

from datetime import date

from boros_research.crossex_client import (
    CrossExCosts,
    CrossExGroup,
    CrossExLeg,
    CrossExPair,
    CrossExResponse,
)
from boros_research.live_benchmark import LiveBenchmarkResult
from boros_research.monitor import LiveMonitor


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
    def lookup_many(self, candidates):
        return {
            item.candidate_id: LiveBenchmarkResult(
                candidate_id=item.candidate_id,
                benchmark_level="dte",
                dte_bucket=item.dte_bucket,
                percentile_30d=96.0,
                percentile_90d=96.0,
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
