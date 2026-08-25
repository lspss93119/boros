"""P2-only Telegram and diagnostic formatting."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .position_models import PositionLeg, StrategySnapshot
from .position_state import (
    HEDGE_RECOVERED,
    HEDGE_WARNING,
    MATURITY_1D,
    MATURITY_14D,
    MATURITY_3D,
    MATURITY_7D,
    NEW_STRATEGY,
    STRATEGY_DISAPPEARED,
    PositionEvent,
)


def _number(value: float | None, suffix: str = "") -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"{value:.2f}{suffix}"


def _apr(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"{value * 100:.2f}%"


def _money(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"${value:+,.0f}"


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def _timestamp(timestamp: int | None) -> str:
    if timestamp is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _dte_days(snapshot: StrategySnapshot) -> int:
    return max(0, math.ceil(snapshot.seconds_to_maturity / 86400))


def _hedge_label(fully_hedged: bool | None) -> str:
    return "已對沖" if fully_hedged else "未對沖"


def _direction(legs: tuple[PositionLeg, ...]) -> str:
    short = next((leg.venue for leg in legs if leg.side.lower() == "short"), None)
    long = next((leg.venue for leg in legs if leg.side.lower() == "long"), None)
    if short and long:
        return f"{short} → {long}"
    venues = [leg.venue for leg in legs if leg.venue]
    if len(venues) >= 2:
        return f"{venues[0]} → {venues[1]}"
    return "方向 unknown"


def _base_header(snapshot: StrategySnapshot) -> list[str]:
    return [
        f"{snapshot.base}｜{_direction(snapshot.legs)}",
        f"到期：{_date(snapshot.maturity)}｜DTE {_dte_days(snapshot)} 天",
    ]


def _snapshot_lines(snapshot: StrategySnapshot) -> list[str]:
    return [
        *_base_header(snapshot),
        f"鎖定利差：{_apr(snapshot.spread)}",
        f"鎖定資本 APR：{_apr(snapshot.locked_apr_on_capital)}",
        f"模型資本：{_money(snapshot.capital_usd)}",
        f"到期預估淨收益：{_money(snapshot.expected_pnl_to_maturity_usd)}",
        f"Hedge：{_hedge_label(snapshot.hedge_checks.fully_hedged)}",
        f"歸因信心：{_number(snapshot.attribution.confidence)}",
    ]


def render_position_snapshot(snapshot: StrategySnapshot) -> str:
    """Render a human-readable snapshot without dumping raw warning arrays."""
    return "\n".join(
        [
            f"策略：{snapshot.strategy_id}",
            *_snapshot_lines(snapshot),
            f"Boros match ratio (borosMatchRatio)：{_number(snapshot.hedge_checks.boros_match_ratio)}",
            f"Perp match ratio (perpMatchRatio)：{_number(snapshot.hedge_checks.perp_match_ratio)}",
            f"Boros vs Perp ratio (borosVsPerpRatio)：{_number(snapshot.hedge_checks.boros_vs_perp_ratio)}",
            f"名義額差：{_money(snapshot.notional_mismatch_usd)}",
        ]
    )


def format_position_event(
    event: PositionEvent,
    snapshot: StrategySnapshot | None = None,
    *,
    last_seen_at: int | None = None,
) -> str:
    current = snapshot or event.snapshot
    if event.kind == NEW_STRATEGY:
        return "\n".join(["🆕 NEW STRATEGY", f"策略：{current.strategy_id}", *_snapshot_lines(current)])

    if event.kind in {HEDGE_WARNING, HEDGE_RECOVERED}:
        heading = "⚠️ HEDGE WARNING" if event.kind == HEDGE_WARNING else "✅ HEDGE RECOVERED"
        previous = "unknown" if event.previous_fully_hedged is None else str(event.previous_fully_hedged)
        current_value = "unknown" if event.current_fully_hedged is None else str(event.current_fully_hedged)
        return "\n".join(
            [
                heading,
                *_base_header(current),
                f"Hedge：{previous} → {current_value}",
                f"Boros match ratio：{_number(current.hedge_checks.boros_match_ratio)}",
                f"Perp match ratio：{_number(current.hedge_checks.perp_match_ratio)}",
                f"Boros vs Perp ratio：{_number(current.hedge_checks.boros_vs_perp_ratio)}",
                f"名義額差：{_money(current.notional_mismatch_usd)}",
            ]
        )

    maturity_labels = {
        MATURITY_14D: 14,
        MATURITY_7D: 7,
        MATURITY_3D: 3,
        MATURITY_1D: 1,
    }
    if event.kind in maturity_labels:
        threshold = maturity_labels[event.kind]
        return "\n".join(
            [
                f"⏰ MATURITY REMINDER｜{threshold}D",
                *_base_header(current),
                f"鎖定利差：{_apr(current.spread)}",
                f"鎖定資本 APR：{_apr(current.locked_apr_on_capital)}",
                f"到期預估淨收益：{_money(current.expected_pnl_to_maturity_usd)}",
                f"Hedge：{_hedge_label(current.hedge_checks.fully_hedged)}",
            ]
        )

    if event.kind == STRATEGY_DISAPPEARED:
        compact_id = current.strategy_id[:18]
        return "\n".join(
            [
                "⚫ STRATEGY DISAPPEARED",
                f"{current.base}｜到期：{_date(current.maturity)}",
                f"策略：{compact_id}",
                f"最後觀察：{_timestamp(last_seen_at)}",
                f"Hedge：{_hedge_label(event.current_fully_hedged)}",
            ]
        )

    raise ValueError(f"unsupported position event kind: {event.kind}")


def render_position_delivery(event: PositionEvent, *, delivered: bool) -> str:
    status = "SENT" if delivered else "TELEGRAM FAILED"
    return f"{status} {event.kind} {event.strategy_id[:18]}"
