"""Finite, atomic dashboard snapshots built from monitor cycle results."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .crossex_client import CrossExPair
from .monitor import LiveOpportunityView, LiveSizeRecord, MonitorCycleResult
from .position_models import PositionLeg, PositionsSnapshot, StrategySnapshot
from .position_monitor import PositionCycleResult


SCHEMA_VERSION = 1
P1_DASHBOARD_SNAPSHOT = Path("data/dashboard/p1_latest.json")
P2_DASHBOARD_SNAPSHOT = Path("data/dashboard/p2_latest.json")
_BENCHMARK_STALE_SECONDS = 7 * 86400


class DashboardSnapshotError(ValueError):
    """Raised when a dashboard snapshot is missing required contract fields."""


def _validate_finite(value: Any, context: str = "snapshot") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{context} keys must be strings")
            _validate_finite(item, f"{context}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{context}[{index}]")
    elif value is None or isinstance(value, (str, int, bool)):
        return
    else:
        raise TypeError(f"{context} contains a non-JSON value")


def _json_text(snapshot: Mapping[str, Any]) -> str:
    _validate_finite(snapshot)
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def write_snapshot(path: str | Path, snapshot: Mapping[str, Any]) -> None:
    """Atomically write one finite JSON snapshot beside the destination."""
    destination = Path(path)
    text = _json_text(snapshot)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _reject_json_constant(value: str) -> None:
    raise DashboardSnapshotError(f"non-finite JSON constant: {value}")


def _required_int(value: Any, context: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardSnapshotError(f"invalid {context}")
    if positive and value <= 0:
        raise DashboardSnapshotError(f"invalid {context}")
    return value


def _validate_envelope(snapshot: Any, expected_kind: str) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise DashboardSnapshotError("snapshot must be an object")
    if snapshot.get("schemaVersion") != SCHEMA_VERSION:
        raise DashboardSnapshotError("incompatible snapshot schema")
    if snapshot.get("kind") != expected_kind:
        raise DashboardSnapshotError("incompatible snapshot kind")
    _required_int(snapshot.get("cycleTimestamp"), "cycleTimestamp")
    _required_int(snapshot.get("pollIntervalSeconds"), "pollIntervalSeconds", positive=True)
    if not isinstance(snapshot.get("sourceStatus"), str):
        raise DashboardSnapshotError("invalid sourceStatus")
    last_good = snapshot.get("lastGoodTimestamp")
    if last_good is not None:
        _required_int(last_good, "lastGoodTimestamp")
    if not isinstance(snapshot.get("data"), Mapping):
        raise DashboardSnapshotError("invalid snapshot data")
    if not isinstance(snapshot.get("diagnostics"), Mapping):
        raise DashboardSnapshotError("invalid snapshot diagnostics")
    _validate_finite(snapshot)
    return dict(snapshot)


def read_snapshot(path: str | Path, expected_kind: str) -> dict[str, Any] | None:
    """Read and strictly validate a snapshot; missing files return ``None``."""
    source = Path(path)
    if not source.exists():
        return None
    try:
        with source.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle, parse_constant=_reject_json_constant)
    except DashboardSnapshotError:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DashboardSnapshotError("invalid dashboard snapshot") from exc
    try:
        return _validate_envelope(parsed, expected_kind)
    except DashboardSnapshotError:
        raise
    except (TypeError, ValueError) as exc:
        raise DashboardSnapshotError("invalid dashboard snapshot") from exc


def _previous_data(previous: Mapping[str, Any] | None, kind: str) -> dict[str, Any]:
    if previous is None or previous.get("kind") != kind:
        return {}
    data = previous.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _previous_last_good(previous: Mapping[str, Any] | None) -> int | None:
    if previous is None:
        return None
    value = previous.get("lastGoodTimestamp")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _base_snapshot(
    *,
    kind: str,
    cycle_timestamp: int,
    poll_interval_seconds: int,
    source_status: str,
    last_good_timestamp: int | None,
    data: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(cycle_timestamp, bool) or not isinstance(cycle_timestamp, int):
        raise ValueError("cycle_timestamp must be an integer")
    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, int)
        or poll_interval_seconds <= 0
    ):
        raise ValueError("poll_interval_seconds must be a positive integer")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "cycleTimestamp": cycle_timestamp,
        "pollIntervalSeconds": poll_interval_seconds,
        "sourceStatus": source_status,
        "lastGoodTimestamp": last_good_timestamp,
        "data": dict(data),
        "diagnostics": dict(diagnostics),
    }


def _pair_data(pair: CrossExPair | None) -> dict[str, Any]:
    fields = {
        "grossSpreadApr": None,
        "execSpreadApr": None,
        "borosImpactApr": None,
        "makerLeg": None,
        "costsTotalUsd": None,
        "capitalUsd": None,
        "netFixedApr": None,
        "netFixedAprOnCapital": None,
        "effectiveLeverage": None,
        "estProfitUsd": None,
        "secondsToMaturity": None,
    }
    if pair is None:
        return {"available": False, **fields}
    return {
        "available": True,
        "grossSpreadApr": pair.gross_spread_apr,
        "execSpreadApr": pair.exec_spread_apr,
        "borosImpactApr": pair.boros_impact_apr,
        "makerLeg": pair.maker_leg,
        "costsTotalUsd": pair.costs.total_usd,
        "capitalUsd": pair.capital_usd,
        "netFixedApr": pair.net_fixed_apr,
        "netFixedAprOnCapital": pair.net_fixed_apr_on_capital,
        "effectiveLeverage": pair.effective_leverage,
        "estProfitUsd": pair.est_profit_usd,
        "secondsToMaturity": pair.seconds_to_maturity,
    }


def _size_data(size: LiveSizeRecord) -> dict[str, Any]:
    return {
        "notionalUsd": size.notional_usd,
        "signalSize": size.notional_usd == 10_000,
        "makerHedge": _pair_data(size.maker_pair),
        "immediate": _pair_data(size.pair),
    }


def _opportunity_data(view: LiveOpportunityView) -> dict[str, Any]:
    identity = view.identity
    return {
        "identityKey": identity.key,
        "asset": identity.asset,
        "maturity": identity.maturity.isoformat(),
        "shortVenue": identity.short_venue,
        "longVenue": identity.long_venue,
        "tokenId": identity.token_id,
        "percentile90d": view.percentile_90d,
        "historicalBand": view.historical_band,
        "highYieldBand": view.high_yield_band,
        "sizes": [_size_data(size) for size in view.sizes],
    }


def _p1_data(result: MonitorCycleResult) -> dict[str, Any]:
    views = result.current_opportunities
    return {
        "counts": {
            "opportunities": len(views),
            "mapped": result.mapped_opportunity_count,
            "benchmarkable": result.benchmarkable_opportunity_count,
            "p95": sum(view.historical_band == "P95" for view in views),
            "p99": sum(view.historical_band == "P99" for view in views),
            "highYield": sum(view.high_yield_band == "HIGH_YIELD" for view in views),
            "exceptional": sum(view.high_yield_band == "EXCEPTIONAL" for view in views),
        },
        "currentOpportunities": [_opportunity_data(view) for view in views],
    }


def build_p1_snapshot(
    result: MonitorCycleResult | None,
    previous: Mapping[str, Any] | None,
    cycle_timestamp: int,
    poll_interval_seconds: int,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a P1 snapshot without recalculating any alert thresholds."""
    if error is not None or result is None:
        diagnostics = {"error": error or "unknown"}
        return _base_snapshot(
            kind="p1",
            cycle_timestamp=cycle_timestamp,
            poll_interval_seconds=poll_interval_seconds,
            source_status="error",
            last_good_timestamp=_previous_last_good(previous),
            data=_previous_data(previous, "p1"),
            diagnostics=diagnostics,
        )

    cross_ex_status = "ok" if not result.unavailable_notionals else "degraded"
    if result.historical_max_timestamp is None or result.benchmark_age_seconds is None:
        benchmark_status = "unavailable"
    elif result.benchmark_age_seconds > _BENCHMARK_STALE_SECONDS:
        benchmark_status = "stale"
    else:
        benchmark_status = "ok"
    source_status = (
        "ok" if cross_ex_status == "ok" and benchmark_status == "ok" else "degraded"
    )
    diagnostics = {
        "crossExStatus": cross_ex_status,
        "benchmarkStatus": benchmark_status,
        "historicalMaxTimestamp": result.historical_max_timestamp,
        "benchmarkAgeSeconds": result.benchmark_age_seconds,
        "unavailableNotionals": list(result.unavailable_notionals),
        "skippedReasons": list(result.skipped_reasons),
        "warningCount": len(result.warnings),
    }
    return _base_snapshot(
        kind="p1",
        cycle_timestamp=cycle_timestamp,
        poll_interval_seconds=poll_interval_seconds,
        source_status=source_status,
        last_good_timestamp=(
            cycle_timestamp
            if source_status == "ok"
            else _previous_last_good(previous)
        ),
        data=_p1_data(result),
        diagnostics=diagnostics,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _leg_data(leg: PositionLeg) -> dict[str, Any]:
    return {
        "kind": leg.kind,
        "venue": leg.venue,
        "base": leg.base,
        "side": leg.side,
        "notionalUsd": leg.notional_usd,
        "collateral": leg.collateral,
        "notionalToken": leg.notional_token,
        "marketId": leg.market_id,
        "entryApr": leg.entry_apr,
        "markApr": leg.mark_apr,
        "floatingApr": leg.floating_apr,
        "entryPrice": leg.entry_price,
        "venueEntry": leg.venue_entry,
        "cashFlowUsd": leg.cash_flow_usd,
        "mtmUsd": leg.mtm_usd,
        "tradePnlUsd": leg.trade_pnl_usd,
        "feesUsd": leg.fees_usd,
        "netUsd": leg.net_usd,
        "openedAt": leg.opened_at,
        "maturity": leg.maturity,
        "symbol": leg.symbol,
        "share": leg.share,
        "warnings": list(leg.warnings),
    }


def _strategy_data(strategy: StrategySnapshot) -> dict[str, Any]:
    return {
        "strategyId": strategy.strategy_id,
        "base": strategy.base,
        "maturity": strategy.maturity,
        "secondsToMaturity": strategy.seconds_to_maturity,
        "legs": [_leg_data(leg) for leg in strategy.legs],
        "hedge": _json_value(strategy.hedge),
        "hedgeChecks": {
            "borosMatchRatio": strategy.hedge_checks.boros_match_ratio,
            "perpMatchRatio": strategy.hedge_checks.perp_match_ratio,
            "borosVsPerpRatio": strategy.hedge_checks.boros_vs_perp_ratio,
            "fullyHedged": strategy.hedge_checks.fully_hedged,
        },
        "capitalUsd": strategy.capital_usd,
        "capitalSplit": _json_value(strategy.capital_split),
        "realizedPnlUsd": strategy.realized_pnl_usd,
        "realizedApr": strategy.realized_apr,
        "spread": strategy.spread,
        "lockedAprOnCapital": strategy.locked_apr_on_capital,
        "expectedPnlToMaturityUsd": strategy.expected_pnl_to_maturity_usd,
        "notionalMismatchUsd": strategy.notional_mismatch_usd,
        "attribution": {
            "source": strategy.attribution.source,
            "confidence": strategy.attribution.confidence,
            "pinned": strategy.attribution.pinned,
            "unclaimed": strategy.attribution.unclaimed,
        },
        "warnings": list(strategy.warnings),
        "hasWarnings": bool(strategy.warnings or any(leg.warnings for leg in strategy.legs)),
    }


def _positions_data(positions: PositionsSnapshot | None) -> dict[str, Any] | None:
    if positions is None:
        return None
    return {
        "exposureGroups": [_json_value(group) for group in positions.exposure_groups],
        "asOfTimestamp": positions.as_of_timestamp,
        "warnings": list(positions.warnings),
        "hasWarnings": bool(positions.warnings),
    }


def _p2_data(result: PositionCycleResult) -> dict[str, Any]:
    return {
        "strategies": [_strategy_data(strategy) for strategy in result.strategy_snapshots],
        "positions": _positions_data(result.positions_snapshot),
    }


def build_p2_snapshot(
    result: PositionCycleResult | None,
    previous: Mapping[str, Any] | None,
    cycle_timestamp: int,
    poll_interval_seconds: int,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a P2 snapshot while keeping primary and auxiliary health distinct."""
    if error is not None or result is None:
        return _base_snapshot(
            kind="p2",
            cycle_timestamp=cycle_timestamp,
            poll_interval_seconds=poll_interval_seconds,
            source_status="error",
            last_good_timestamp=_previous_last_good(previous),
            data=_previous_data(previous, "p2"),
            diagnostics={"error": error or "unknown"},
        )

    if not result.strategy_success:
        source_status = "unknown"
        data = _previous_data(previous, "p2")
    elif result.auxiliary_success:
        source_status = "ok"
        data = _p2_data(result)
    else:
        source_status = "degraded"
        data = _p2_data(result)
    diagnostics = {
        "primaryStatus": "ok" if result.strategy_success else "unknown",
        "auxiliaryStatus": "ok" if result.auxiliary_success else "unavailable",
        "strategyCount": result.strategy_count,
        "exposureGroupCount": result.exposure_group_count,
        "warningCount": len(result.warnings),
        "warnings": list(result.warnings),
    }
    return _base_snapshot(
        kind="p2",
        cycle_timestamp=cycle_timestamp,
        poll_interval_seconds=poll_interval_seconds,
        source_status=source_status,
        last_good_timestamp=(
            cycle_timestamp
            if result.strategy_success
            else _previous_last_good(previous)
        ),
        data=data,
        diagnostics=diagnostics,
    )
