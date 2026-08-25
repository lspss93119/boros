"""Read-only orchestration for CrossEx open strategy health."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .crossex_client import CrossExClient, validate_evm_address
from .position_models import PositionsSnapshot, StrategySnapshot
from .position_state import PositionEvent, PositionStateStore
from .position_telegram import (
    format_position_event,
    render_position_delivery,
    render_position_snapshot,
)


@dataclass(frozen=True)
class PositionDeliveryEvent:
    event: PositionEvent
    delivered: bool
    committed: bool
    error: str | None = None


@dataclass(frozen=True)
class PositionCycleResult:
    strategy_success: bool
    auxiliary_success: bool
    primary_unknown: bool
    strategy_count: int
    exposure_group_count: int
    events: tuple[PositionEvent, ...]
    messages: tuple[str, ...]
    snapshots: tuple[str, ...]
    warnings: tuple[str, ...]
    delivery_events: tuple[PositionDeliveryEvent, ...]
    telegram_sent_count: int


def render_position_cycle_summary(result: PositionCycleResult) -> str:
    primary = "ok" if result.strategy_success else "UNKNOWN"
    auxiliary = "ok" if result.auxiliary_success else "unavailable"
    delivered = sum(1 for event in result.delivery_events if event.delivered)
    return (
        "Open position monitor | "
        f"strategy {primary} ({result.strategy_count}) | "
        f"positions {auxiliary} ({result.exposure_group_count}) | "
        f"events {len(result.events)} | sent {result.telegram_sent_count} | "
        f"delivery-events {delivered}"
    )


class PositionMonitor:
    def __init__(
        self,
        *,
        client: CrossExClient,
        state: PositionStateStore,
        address: str,
        telegram_send: Callable[[str], None] | None,
        now_timestamp: Callable[[], int] | None = None,
        poll_interval_seconds: int = 60,
    ) -> None:
        validate_evm_address(address)
        if isinstance(poll_interval_seconds, bool) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.client = client
        self.state = state
        self.address = address
        self.telegram_send = telegram_send
        self.now_timestamp = now_timestamp or (lambda: int(time.time()))
        self.poll_interval_seconds = poll_interval_seconds

    def _fetch_sources(
        self,
    ) -> tuple[bool, tuple[StrategySnapshot, ...], str | None, bool, PositionsSnapshot | None]:
        strategy_success = False
        strategies: tuple[StrategySnapshot, ...] = ()
        strategy_error: str | None = None
        positions_success = False
        positions: PositionsSnapshot | None = None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self.client.fetch_strategy, self.address): "strategy",
                pool.submit(self.client.fetch_positions): "positions",
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    value = future.result()
                except Exception as exc:
                    if source == "strategy":
                        strategy_error = type(exc).__name__
                    continue
                if source == "strategy":
                    strategies = tuple(value)
                    strategy_success = True
                else:
                    positions = value
                    positions_success = True
        return strategy_success, strategies, strategy_error, positions_success, positions

    def _deliver(
        self,
        event: PositionEvent,
        *,
        dry_run: bool,
        timestamp: int,
    ) -> tuple[str, PositionDeliveryEvent, int]:
        state_snapshot = self.state.snapshot(event.strategy_id)
        last_seen_at = None if state_snapshot is None else state_snapshot.last_seen_at
        message = format_position_event(event, last_seen_at=last_seen_at)
        if dry_run:
            return (
                message,
                PositionDeliveryEvent(event, False, False, "dry_run"),
                0,
            )
        if self.telegram_send is None:
            return (
                message,
                PositionDeliveryEvent(event, False, False, "telegram_unavailable"),
                0,
            )
        try:
            self.telegram_send(message)
        except Exception as exc:
            return (
                message,
                PositionDeliveryEvent(event, False, False, type(exc).__name__),
                0,
            )
        committed = self.state.commit_event(event, timestamp)
        return message, PositionDeliveryEvent(event, True, committed), 1

    def run_once(self, *, dry_run: bool = False) -> PositionCycleResult:
        timestamp = self.now_timestamp()
        strategy_success, strategies, strategy_error, positions_success, positions = self._fetch_sources()
        warnings: list[str] = []
        if not positions_success:
            warnings.append("positions_unavailable")
        if not strategy_success:
            warnings.append("strategy_unknown")
            self.state.observe_unknown(timestamp)
            events: tuple[PositionEvent, ...] = ()
        else:
            events = self.state.observe_success(strategies, timestamp)

        messages: list[str] = []
        delivery_events: list[PositionDeliveryEvent] = []
        telegram_sent_count = 0
        for event in events:
            message, delivery, sent = self._deliver(
                event,
                dry_run=dry_run,
                timestamp=timestamp,
            )
            messages.append(message)
            delivery_events.append(delivery)
            telegram_sent_count += sent

        snapshots = tuple(render_position_snapshot(snapshot) for snapshot in strategies)
        if strategy_error is not None:
            warnings.append(f"strategy_error:{strategy_error}")
        return PositionCycleResult(
            strategy_success=strategy_success,
            auxiliary_success=positions_success,
            primary_unknown=not strategy_success,
            strategy_count=len(strategies),
            exposure_group_count=0 if positions is None else len(positions.exposure_groups),
            events=events,
            messages=tuple(messages),
            snapshots=snapshots,
            warnings=tuple(warnings),
            delivery_events=tuple(delivery_events),
            telegram_sent_count=telegram_sent_count,
        )

    def run_forever(self, *, dry_run: bool = False) -> None:
        while True:
            try:
                result = self.run_once(dry_run=dry_run)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"Open position monitor cycle failed: {type(exc).__name__}")
                time.sleep(self.poll_interval_seconds)
                continue
            print(render_position_cycle_summary(result))
            for message in result.messages:
                print("\n" + message)
            for delivery in result.delivery_events:
                print(render_position_delivery(delivery.event, delivered=delivery.delivered))
            for snapshot in result.snapshots:
                print("\n" + snapshot)
            time.sleep(self.poll_interval_seconds)
