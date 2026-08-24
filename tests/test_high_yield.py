from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from boros_research.alert_state import AlertIdentity, AlertStateStore
from boros_research.crossex_client import (
    MONITORED_NOTIONALS,
    CrossExCosts,
    CrossExGroup,
    CrossExLeg,
    CrossExPair,
    CrossExResponse,
)
from boros_research.live_benchmark import LiveBenchmarkResult
from boros_research.monitor import LiveMonitor
from boros_research.telegram import (
    AlertMessage,
    SizeMessage,
    format_opportunity_message,
    holding_period_return,
)


SECONDS_PER_YEAR = 365 * 86400
AS_OF = 1_800_000_000
MATURITY = AS_OF + 31 * 86400


def make_pair(
    *,
    notional: int,
    apr: float = 0.18,
    seconds: int = 31 * 86400,
    base: str = "ETH",
    maker: bool = False,
    capital: float | None = 5_000.0,
    profit: float | None = None,
) -> CrossExPair:
    if profit is None and capital is not None:
        profit = capital * apr * seconds / SECONDS_PER_YEAR
    return CrossExPair(
        base=base,
        short_leg=CrossExLeg(
            190,
            "Hyperliquid",
            "HYPERLIQUID",
            f"{base}USDT",
            base,
            0.20,
            0.19,
        ),
        long_leg=CrossExLeg(
            192,
            "OKX",
            "OKX",
            f"{base}USDT",
            base,
            0.04,
            0.05,
        ),
        gross_spread_apr=0.16,
        exec_spread_apr=0.15,
        boros_impact_apr=0.01,
        maker_leg="short" if maker else None,
        costs=CrossExCosts(1, 1, 1, 1, 1, 1, 6, 0.001),
        capital_usd=capital,
        net_fixed_apr=apr,
        net_fixed_apr_on_capital=apr,
        effective_leverage=2.0,
        est_profit_usd=profit,
        seconds_to_maturity=seconds,
        reasons=(),
    )


def make_group(
    *,
    notional: int,
    token_id: int = 3,
    seconds: int = 31 * 86400,
    pair: CrossExPair | None = None,
    underlying: str = "ETH",
):
    return CrossExGroup(
        token_id=token_id,
        collateral="USDT",
        collateral_price_usd=1.0,
        maturity_timestamp=MATURITY,
        seconds_to_maturity=seconds,
        underlying=underlying,
        pairs=(pair or make_pair(notional=notional, seconds=seconds),),
        warnings=(),
    )


def make_response(
    notional: int,
    pair: CrossExPair,
    *,
    token_id: int = 3,
    seconds: int = 31 * 86400,
) -> CrossExResponse:
    return CrossExResponse(
        notional_usd=notional,
        as_of_timestamp=AS_OF,
        groups=(
            make_group(
                notional=notional,
                token_id=token_id,
                seconds=seconds,
                pair=pair,
                underlying=pair.base,
            ),
        ),
        warnings=(),
    )


class ModeClient:
    def __init__(
        self,
        *,
        maker_apr: float = 0.18,
        maker_aprs: dict[int, float] | None = None,
        market_apr: float = 0.18,
        seconds: int = 31 * 86400,
        maker_invalid: bool = False,
        fail_maker: bool = False,
        fail_sizes: set[int] | None = None,
    ) -> None:
        self.maker_apr = maker_apr
        self.maker_aprs = maker_aprs or {}
        self.market_apr = market_apr
        self.seconds = seconds
        self.maker_invalid = maker_invalid
        self.fail_maker = fail_maker
        self.fail_sizes = fail_sizes or set()
        self.calls: list[tuple[int, str, str, str]] = []

    def fetch(self, notional, *, boros_entry, entry_mode, exit_mode):
        self.calls.append((notional, boros_entry, entry_mode, exit_mode))
        if notional in self.fail_sizes:
            raise RuntimeError(f"missing {notional}")
        if entry_mode == "maker-hedge":
            if self.fail_maker:
                raise RuntimeError("maker-hedge unavailable")
            pair = make_pair(
                notional=notional,
                apr=self.maker_aprs.get(notional, self.maker_apr),
                seconds=self.seconds,
                maker=True,
                capital=None if self.maker_invalid else 5_000.0,
                profit=None if self.maker_invalid else None,
            )
        else:
            pair = make_pair(
                notional=notional,
                apr=self.market_apr,
                seconds=self.seconds,
            )
        return make_response(notional, pair, seconds=self.seconds)


class Benchmark:
    def __init__(self, percentile: float | None = None, *, fail: bool = False) -> None:
        self.percentile = percentile
        self.fail = fail

    def lookup_many(self, candidates):
        if self.fail:
            raise RuntimeError("historical benchmark unavailable")
        return {
            candidate.candidate_id: LiveBenchmarkResult(
                candidate_id=candidate.candidate_id,
                benchmark_level="dte",
                dte_bucket=candidate.dte_bucket,
                percentile_30d=self.percentile,
                percentile_90d=self.percentile,
                percentile_lifetime=self.percentile,
                sample_count_30d=100,
                sample_count_90d=100,
                sample_count_lifetime=100,
                historical_max_timestamp=AS_OF,
                benchmark_age_seconds=0,
                benchmark_fresh=self.percentile is not None,
            )
            for candidate in candidates
        }

    def health(self):
        return (None, None, False)


def monitor_for(tmp_path, client, benchmark=None, sent=None):
    state = AlertStateStore(tmp_path / "live_monitor.sqlite3", poll_interval_seconds=60)
    monitor = LiveMonitor(
        client=client,
        benchmark=benchmark or Benchmark(),
        state=state,
        telegram_send=None if sent is None else sent.append,
        now_timestamp=lambda: AS_OF,
        database_path=tmp_path / "unused.duckdb",
    )
    return monitor, state


@pytest.mark.parametrize(
    ("apr", "seconds", "classification"),
    [
        (0.1999, 14 * 86400, None),
        (0.20, 14 * 86400, "HIGH_YIELD"),
        (0.30, 14 * 86400, "EXCEPTIONAL"),
        (0.35, 14 * 86400 - 1, None),
    ],
)
def test_high_yield_thresholds_are_inclusive_and_use_actual_dte(
    tmp_path, apr, seconds, classification
):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=apr, seconds=seconds),
        Benchmark(None),
        sent,
    )

    result = monitor.run_once()

    assert ("EXCEPTIONAL" in result.messages[0]) if classification == "EXCEPTIONAL" else (
        bool(result.messages) == (classification is not None)
    )
    if classification is None:
        assert sent == []


def test_only_ten_k_can_trigger_high_yield_and_ladder_is_exact(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(tmp_path, ModeClient(maker_apr=0.18), Benchmark(None), sent)

    result = monitor.run_once()

    assert MONITORED_NOTIONALS == (10_000, 25_000, 50_000, 100_000, 200_000)
    assert sorted({call[0] for call in monitor.client.calls}) == list(MONITORED_NOTIONALS)
    assert all(call[0] <= 200_000 for call in monitor.client.calls)
    assert not result.messages


def test_high_yield_uses_ten_k_maker_apr_not_larger_capacity_or_reference(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=0.18, market_apr=0.40),
        Benchmark(None),
        sent,
    )
    result = monitor.run_once()
    assert not result.messages

    sent.clear()
    monitor.client = ModeClient(maker_apr=0.31, market_apr=0.18)
    result = monitor.run_once()
    assert result.messages
    assert "EXCEPTIONAL" in result.messages[0]


def test_high_yield_signal_uses_only_ten_k_when_capacity_apr_differs(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_aprs={10_000: 0.31, 25_000: 0.18}),
        Benchmark(None),
        sent,
    )
    result = monitor.run_once()
    assert len(result.messages) == 1
    assert "EXCEPTIONAL" in result.messages[0]

    sent.clear()
    monitor, _state = monitor_for(
        tmp_path / "no-ten-k",
        ModeClient(maker_aprs={10_000: 0.18, 25_000: 0.35}),
        Benchmark(None),
        sent,
    )
    assert not monitor.run_once().messages


def test_invalid_maker_does_not_use_valid_both_market_result(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=0.40, market_apr=0.40, maker_invalid=True),
        Benchmark(None),
        sent,
    )

    result = monitor.run_once()

    assert not result.messages


def test_maker_failure_does_not_suppress_valid_historical_alert(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=0.40, market_apr=0.40, fail_maker=True),
        Benchmark(96.0),
        sent,
    )

    result = monitor.run_once()

    assert len(sent) == 1
    assert "歷史前 5%" in sent[0]
    assert "HIGH YIELD" not in sent[0]
    assert result.unavailable_notionals == MONITORED_NOTIONALS


def test_high_yield_survives_historical_benchmark_failure(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=0.22),
        Benchmark(None, fail=True),
        sent,
    )

    result = monitor.run_once()

    assert len(sent) == 1
    assert "HIGH YIELD" in sent[0]
    assert "90D" not in sent[0]
    assert result.benchmarkable_opportunity_count == 1


def test_response_ordering_is_matched_by_full_identity(tmp_path):
    class ReorderedClient(ModeClient):
        def fetch(self, notional, *, boros_entry, entry_mode, exit_mode):
            self.calls.append((notional, boros_entry, entry_mode, exit_mode))
            first = make_response(notional, make_pair(notional=notional, base="ETH"), token_id=3)
            second = make_response(notional, make_pair(notional=notional, base="BTC"), token_id=4)
            if entry_mode == "maker-hedge":
                return replace(
                    first,
                    groups=(
                        replace(second.groups[0], pairs=(make_pair(notional=notional, base="BTC", maker=True),)),
                        replace(first.groups[0], pairs=(make_pair(notional=notional, base="ETH", maker=True),)),
                    ),
                )
            return replace(second, groups=(first.groups[0], second.groups[0]))

    monitor, _state = monitor_for(tmp_path, ReorderedClient(), Benchmark(None), [])
    result = monitor.run_once()

    identities = {
        item[10_000].identity.asset: item[10_000]
        for item in result.sizes_by_opportunity
    }
    assert identities["ETH"].maker_pair.base == "ETH"
    assert identities["BTC"].maker_pair.base == "BTC"


def test_capacity_message_keeps_unavailable_rows_explicit(tmp_path):
    sent: list[str] = []
    monitor, _state = monitor_for(
        tmp_path,
        ModeClient(maker_apr=0.34, fail_sizes={100_000, 200_000}),
        Benchmark(None),
        sent,
    )

    result = monitor.run_once()
    message = result.messages[0]

    assert "$50,000" in message
    assert "$100,000" in message and "$200,000" in message
    assert message.count("$50,000") == 1
    assert "不可用" in message or "—" in message
    assert "300,000" not in message


def test_holding_period_return_is_consistent_with_apr_and_profit():
    pair = make_pair(notional=10_000, apr=0.34, seconds=14 * 86400)

    assert holding_period_return(pair) == pytest.approx(
        pair.net_fixed_apr_on_capital * pair.seconds_to_maturity / SECONDS_PER_YEAR
    )
    assert holding_period_return(pair) == pytest.approx(pair.est_profit_usd / pair.capital_usd)


def test_merged_historical_and_high_yield_send_once_and_commit_both(tmp_path):
    sent: list[str] = []
    monitor, state = monitor_for(tmp_path, ModeClient(maker_apr=0.22), Benchmark(96.0), sent)

    monitor.run_once()

    identity = state.identities()[0]
    snapshot = state.snapshot(identity)
    assert len(sent) == 1
    assert "HIGH YIELD" in sent[0]
    assert "歷史 P96.0" in sent[0]
    assert snapshot.p95_armed is False
    assert snapshot.hy20_armed is False


def test_failed_merged_delivery_commits_none_of_new_states(tmp_path):
    def fail(_message):
        raise RuntimeError("telegram unavailable")

    monitor, state = monitor_for(tmp_path, ModeClient(maker_apr=0.31), Benchmark(99.0), None)
    monitor.telegram_send = fail

    result = monitor.run_once()

    snapshot = state.snapshot(state.identities()[0])
    assert result.delivery_events[0].delivered is False
    assert snapshot.p95_armed is True
    assert snapshot.p99_armed is True
    assert snapshot.hy20_armed is True
    assert snapshot.hy30_armed is True


def test_historical_then_hy30_and_hy_then_historical_are_independent(tmp_path):
    sent: list[str] = []
    monitor, state = monitor_for(tmp_path, ModeClient(maker_apr=0.18), Benchmark(96.0), sent)
    assert len(monitor.run_once().messages) == 1

    monitor.client = ModeClient(maker_apr=0.31)
    assert len(monitor.run_once().messages) == 1
    assert len(sent) == 2

    sent.clear()
    state.close()
    monitor, _state = monitor_for(tmp_path / "second", ModeClient(maker_apr=0.22), Benchmark(94.0), sent)
    assert len(monitor.run_once().messages) == 1
    monitor.benchmark = Benchmark(99.0)
    assert len(monitor.run_once().messages) == 1
    assert len(sent) == 2


def test_alert_state_hy20_hy30_rearm_and_unknown_semantics():
    identity = AlertIdentity("ETH", date(2026, 9, 25), "HYPERLIQUID", "OKX", 3)
    store = AlertStateStore(":memory:", poll_interval_seconds=60)

    first = store.observe_high_yield(identity, 1_000, 0.22, 14 * 86400)
    assert first.classification == "HIGH_YIELD"
    store.commit_alert_delivered(identity, 1_000, None, high_yield=first.classification)
    assert store.observe_high_yield(identity, 1_060, 0.31, 14 * 86400).classification == "EXCEPTIONAL"

    store = AlertStateStore(":memory:", poll_interval_seconds=60)
    first = store.observe_high_yield(identity, 1_000, 0.31, 14 * 86400)
    store.commit_alert_delivered(identity, 1_000, None, high_yield=first.classification)
    for timestamp in range(1_060, 1_060 + 6 * 3600, 60):
        store.observe_high_yield(identity, timestamp, 0.19, 14 * 86400)
    assert store.observe_high_yield(identity, 1_060 + 6 * 3600, 0.22, 14 * 86400).classification == "HIGH_YIELD"

    store = AlertStateStore(":memory:", poll_interval_seconds=60)
    first = store.observe_high_yield(identity, 1_000, 0.31, 14 * 86400)
    store.commit_alert_delivered(identity, 1_000, None, high_yield=first.classification)
    for timestamp in range(1_060, 1_060 + 6 * 3600, 60):
        store.observe_high_yield(identity, timestamp, 0.29, 14 * 86400)
    assert store.observe_high_yield(identity, 1_060 + 6 * 3600, 0.31, 14 * 86400).classification == "EXCEPTIONAL"

    store = AlertStateStore(":memory:", poll_interval_seconds=60)
    first = store.observe_high_yield(identity, 1_000, 0.22, 14 * 86400)
    store.commit_alert_delivered(identity, 1_000, None, high_yield=first.classification)
    store.observe_high_yield(identity, 1_060, 0.19, 14 * 86400)
    store.observe_high_yield(identity, 1_120, None, None, valid=False)
    assert store.observe_high_yield(identity, 1_060 + 6 * 3600, 0.22, 14 * 86400).classification is None


def test_historical_and_high_yield_state_are_independent():
    identity = AlertIdentity("ETH", date(2026, 9, 25), "HYPERLIQUID", "OKX", 3)
    store = AlertStateStore(":memory:")
    historical = store.observe(identity, 1_000, 96.0)
    high = store.observe_high_yield(identity, 1_000, 0.22, 14 * 86400)
    assert historical.severity == "NORMAL"
    assert high.classification == "HIGH_YIELD"
    snapshot = store.snapshot(identity)
    assert snapshot.p95_armed is True
    assert snapshot.hy20_armed is True


def test_production_schema_migration_preserves_historical_state(tmp_path):
    path = tmp_path / "live_monitor.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE alert_state (
            identity_key TEXT PRIMARY KEY,
            asset TEXT NOT NULL,
            maturity TEXT NOT NULL,
            short_venue TEXT NOT NULL,
            long_venue TEXT NOT NULL,
            token_id INTEGER NOT NULL,
            p95_armed INTEGER NOT NULL DEFAULT 1,
            p99_armed INTEGER NOT NULL DEFAULT 1,
            p95_below_since INTEGER,
            p99_below_since INTEGER,
            p95_last_alert_timestamp INTEGER,
            p99_last_alert_timestamp INTEGER,
            p95_run_start INTEGER,
            p95_run_last_success INTEGER,
            p95_observation_count INTEGER NOT NULL DEFAULT 0,
            p99_run_start INTEGER,
            p99_run_last_success INTEGER,
            p99_observation_count INTEGER NOT NULL DEFAULT 0,
            last_success_timestamp INTEGER,
            last_poll_timestamp INTEGER
        )
        """
    )
    identity = AlertIdentity("ETH", date(2026, 9, 25), "HYPERLIQUID", "OKX", 3)
    connection.execute(
        "INSERT INTO alert_state (identity_key, asset, maturity, short_venue, long_venue, token_id, p95_armed) VALUES (?, ?, ?, ?, ?, ?, 0)",
        (identity.key, identity.asset, identity.maturity.isoformat(), identity.short_venue, identity.long_venue, identity.token_id),
    )
    connection.commit()
    connection.close()

    store = AlertStateStore(path)
    snapshot = store.snapshot(identity)
    assert snapshot.p95_armed is False
    assert snapshot.p99_armed is True
    assert snapshot.hy20_armed is True
    assert snapshot.hy30_armed is True


def test_high_yield_telegram_contains_reference_and_unavailable_capacity():
    maker = make_pair(notional=10_000, apr=0.34, maker=True)
    reference = make_pair(notional=10_000, apr=0.27)
    message = format_opportunity_message(
        AlertMessage(
            severity=None,
            asset="ETH",
            short_venue="HYPERLIQUID",
            long_venue="OKX",
            maturity=date(2026, 9, 25),
            remaining_days=31,
            primary=SizeMessage(10_000, maker, None),
            sizes=(
                SizeMessage(10_000, maker, None),
                SizeMessage(25_000, replace(maker, net_fixed_apr_on_capital=0.31), None),
                SizeMessage(50_000, replace(maker, net_fixed_apr_on_capital=0.27), None),
                SizeMessage(100_000, None, None),
                SizeMessage(200_000, None, None),
            ),
            live_detected_minutes=0,
            high_yield_classification="EXCEPTIONAL",
            reference=SizeMessage(10_000, reference, None),
            dte_seconds=31 * 86400,
        )
    )

    assert "EXCEPTIONAL" in message
    assert "Limit + Hedge" in message
    assert "掛單成交未保證" in message
    assert "立即成交參考" in message
    assert "34.00%" in message and "27.00%" in message
    assert "$100,000" in message and "$200,000" in message
    assert "—" in message
    assert "⚠️ CrossEx：" not in message
