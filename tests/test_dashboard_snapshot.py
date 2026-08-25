from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from boros_research.alert_state import AlertIdentity
from boros_research.crossex_client import (
    CrossExCosts,
    CrossExGroup,
    CrossExLeg,
    CrossExPair,
)
from boros_research.monitor import (
    LiveOpportunityView,
    LiveSizeRecord,
    MonitorCycleResult,
)
from boros_research.position_models import (
    Attribution,
    HedgeChecks,
    PositionsSnapshot,
    StrategySnapshot,
)
from boros_research.position_monitor import PositionCycleResult


def pair(*, apr: float = 0.24, seconds: int = 20 * 86400) -> CrossExPair:
    leg = CrossExLeg(1, "Hyperliquid", "HYPERLIQUID", "ETHUSDT", "ETH", 0.1, 0.1)
    other = CrossExLeg(2, "OKX", "OKX", "ETHUSDT", "ETH", 0.04, 0.04)
    return CrossExPair(
        base="ETH",
        short_leg=leg,
        long_leg=other,
        gross_spread_apr=0.28,
        exec_spread_apr=0.26,
        boros_impact_apr=0.01,
        maker_leg="short",
        costs=CrossExCosts(1, 2, 3, 4, 5, 6, 21, 0.01),
        capital_usd=1_000.0,
        net_fixed_apr=apr - 0.01,
        net_fixed_apr_on_capital=apr,
        effective_leverage=10.0,
        est_profit_usd=100.0,
        seconds_to_maturity=seconds,
        reasons=(),
    )


def view() -> LiveOpportunityView:
    identity = AlertIdentity("ETH", date(2026, 9, 25), "HYPERLIQUID", "OKX", 3)
    group = CrossExGroup(
        token_id=3,
        collateral="USDT",
        collateral_price_usd=1.0,
        maturity_timestamp=1_800_000_000,
        seconds_to_maturity=20 * 86400,
        underlying="ETH",
        pairs=(pair(),),
        warnings=(),
    )
    sizes = tuple(
        LiveSizeRecord(
            notional_usd=notional,
            pair=pair(apr=0.18) if notional <= 50_000 else None,
            maker_pair=pair() if notional <= 50_000 else None,
            benchmark=None,
            candidate=None,
            identity=identity,
            group=group,
        )
        for notional in (10_000, 25_000, 50_000, 100_000, 200_000)
    )
    return LiveOpportunityView(
        identity=identity,
        percentile_90d=99.7,
        historical_band="P95",
        high_yield_band="HIGH_YIELD",
        sizes=sizes,
    )


def p1_result(
    *,
    unavailable: tuple[int, ...] = (),
    historical_max: int | None = 1_000,
    benchmark_age: int | None = 60,
) -> MonitorCycleResult:
    return MonitorCycleResult(
        group_counts={10_000: 1},
        unavailable_notionals=unavailable,
        mapped_opportunity_count=1,
        benchmarkable_opportunity_count=1,
        normal_candidate_count=1,
        urgent_candidate_count=1,
        skipped_reasons=(),
        warnings=(),
        messages=(),
        sizes_by_opportunity=(),
        historical_max_timestamp=historical_max,
        benchmark_age_seconds=benchmark_age,
        current_opportunities=(view(),),
    )


def strategy() -> StrategySnapshot:
    return StrategySnapshot(
        strategy_id="strategy-1",
        base="ETH",
        maturity=1_800_000_000,
        legs=(),
        hedge={"venue": "OKX"},
        hedge_checks=HedgeChecks(1.0, 0.99, 0.98, True),
        capital_usd=1_000.0,
        capital_split={"boros": 400.0, "perp": 600.0},
        realized_pnl_usd=12.0,
        realized_apr=0.03,
        spread=0.1,
        locked_apr_on_capital=0.2,
        expected_pnl_to_maturity_usd=100.0,
        seconds_to_maturity=20 * 86400,
        notional_mismatch_usd=2.0,
        attribution=Attribution("cross-ex", 0.9, True, False),
        warnings=("diagnostic",),
    )


def p2_result(
    *,
    strategy_success: bool = True,
    auxiliary_success: bool = True,
) -> PositionCycleResult:
    source = (strategy(),) if strategy_success else ()
    positions = (
        PositionsSnapshot(({"groupId": "group-1"},), 1_000, ())
        if auxiliary_success
        else None
    )
    return PositionCycleResult(
        strategy_success=strategy_success,
        auxiliary_success=auxiliary_success,
        primary_unknown=not strategy_success,
        strategy_count=len(source),
        exposure_group_count=0 if positions is None else 1,
        events=(),
        messages=(),
        snapshots=(),
        warnings=(),
        delivery_events=(),
        telegram_sent_count=0,
        strategy_snapshots=source,
        positions_snapshot=positions,
    )


def test_snapshot_schema_is_finite_and_reader_rejects_recursive_non_finite(tmp_path):
    from boros_research.dashboard_snapshot import (
        DashboardSnapshotError,
        read_snapshot,
        write_snapshot,
    )

    path = tmp_path / "p1_latest.json"
    snapshot = {
        "schemaVersion": 1,
        "kind": "p1",
        "cycleTimestamp": 1_000,
        "pollIntervalSeconds": 60,
        "sourceStatus": "ok",
        "lastGoodTimestamp": 1_000,
        "data": {"nested": [1, 2]},
        "diagnostics": {},
    }
    write_snapshot(path, snapshot)
    assert read_snapshot(path, "p1") == snapshot
    assert "NaN" not in path.read_text()

    path.write_text(
        json.dumps({**snapshot, "data": {"nested": {"bad": "NaN"}}}).replace(
            '"NaN"', "NaN"
        )
    )
    with pytest.raises(DashboardSnapshotError):
        read_snapshot(path, "p1")


def test_atomic_serialization_failure_preserves_previous_valid_snapshot(tmp_path):
    from boros_research.dashboard_snapshot import read_snapshot, write_snapshot

    path = tmp_path / "p1_latest.json"
    previous = {
        "schemaVersion": 1,
        "kind": "p1",
        "cycleTimestamp": 1_000,
        "pollIntervalSeconds": 60,
        "sourceStatus": "ok",
        "lastGoodTimestamp": 1_000,
        "data": {"value": 1},
        "diagnostics": {},
    }
    write_snapshot(path, previous)

    with pytest.raises(TypeError):
        write_snapshot(path, {"schemaVersion": 1, "kind": "p1", "bad": object()})

    assert read_snapshot(path, "p1") == previous
    assert not list(tmp_path.glob("*.tmp"))


def test_p1_builder_copies_bands_and_marks_only_ten_k_signal(tmp_path):
    from boros_research.dashboard_snapshot import build_p1_snapshot

    snapshot = build_p1_snapshot(p1_result(), None, 1_000, 60)
    opportunity = snapshot["data"]["currentOpportunities"][0]

    assert snapshot["sourceStatus"] == "ok"
    assert snapshot["lastGoodTimestamp"] == 1_000
    assert opportunity["percentile90d"] == 99.7
    assert opportunity["historicalBand"] == "P95"
    assert opportunity["highYieldBand"] == "HIGH_YIELD"
    sizes = opportunity["sizes"]
    assert [item["signalSize"] for item in sizes] == [True, False, False, False, False]
    assert sizes[2]["makerHedge"]["netFixedAprOnCapital"] == 0.24
    assert sizes[3]["makerHedge"]["available"] is False
    assert sizes[4]["immediate"]["available"] is False


def test_p1_status_and_last_good_rules_retain_previous_data():
    from boros_research.dashboard_snapshot import build_p1_snapshot

    good = build_p1_snapshot(p1_result(), None, 1_000, 60)
    degraded = build_p1_snapshot(
        replace(p1_result(), unavailable_notionals=(25_000,)), good, 1_060, 60
    )
    stale = build_p1_snapshot(
        replace(p1_result(), benchmark_age_seconds=8 * 86400), good, 1_120, 60
    )
    error = build_p1_snapshot(None, good, 1_180, 60, error="RuntimeError")

    assert degraded["sourceStatus"] == "degraded"
    assert degraded["lastGoodTimestamp"] == 1_000
    assert degraded["diagnostics"]["crossExStatus"] == "degraded"
    assert stale["diagnostics"]["benchmarkStatus"] == "stale"
    assert error["sourceStatus"] == "error"
    assert error["cycleTimestamp"] == 1_180
    assert error["lastGoodTimestamp"] == 1_000
    assert error["data"] == good["data"]


def test_p2_builder_separates_primary_and_auxiliary_health_and_retains_unknown_data():
    from boros_research.dashboard_snapshot import build_p2_snapshot

    good = build_p2_snapshot(p2_result(), None, 1_000, 60)
    degraded = build_p2_snapshot(
        p2_result(strategy_success=True, auxiliary_success=False), good, 1_060, 60
    )
    unknown = build_p2_snapshot(
        p2_result(strategy_success=False, auxiliary_success=False), good, 1_120, 60
    )
    error = build_p2_snapshot(None, good, 1_180, 60, error="ValueError")

    assert good["sourceStatus"] == "ok"
    assert good["lastGoodTimestamp"] == 1_000
    assert good["data"]["strategies"][0]["strategyId"] == "strategy-1"
    assert good["data"]["strategies"][0]["hedgeChecks"]["fullyHedged"] is True
    assert degraded["sourceStatus"] == "degraded"
    assert degraded["lastGoodTimestamp"] == 1_060
    assert unknown["sourceStatus"] == "unknown"
    assert unknown["lastGoodTimestamp"] == 1_000
    assert unknown["data"] == good["data"]
    assert error["sourceStatus"] == "error"
    assert error["data"] == good["data"]
