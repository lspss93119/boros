"""Polling and alert orchestration; no CrossEx writes are present here."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .alert_state import AlertIdentity, AlertStateStore
from .crossex_client import (
    MONITORED_NOTIONALS,
    CrossExClient,
    CrossExGroup,
    CrossExPair,
    CrossExResponse,
)
from .config import CROSSEX_VENUES, DUCKDB_PATH
from .live_benchmark import (
    HistoricalBenchmarkLookup,
    LiveBenchmarkCandidate,
    LiveBenchmarkResult,
    canonical_live_asset,
)
from .normalize import normalize_venue
from .telegram import AlertMessage, SizeMessage, format_opportunity_message


@dataclass(frozen=True)
class LiveSizeRecord:
    notional_usd: int
    pair: CrossExPair
    benchmark: LiveBenchmarkResult | None
    candidate: LiveBenchmarkCandidate | None
    identity: AlertIdentity
    group: CrossExGroup


@dataclass(frozen=True)
class DeliveryEvent:
    severity: str
    asset: str
    short_venue: str
    long_venue: str
    percentile_90d: float | None
    delivered: bool


@dataclass(frozen=True)
class MonitorCycleResult:
    group_counts: dict[int, int]
    unavailable_notionals: tuple[int, ...]
    mapped_opportunity_count: int
    benchmarkable_opportunity_count: int
    normal_candidate_count: int
    urgent_candidate_count: int
    skipped_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    messages: tuple[str, ...]
    sizes_by_opportunity: tuple[dict[int, LiveSizeRecord | None], ...]
    historical_max_timestamp: int | None
    benchmark_age_seconds: int | None
    telegram_sent_count: int = 0
    delivery_events: tuple[DeliveryEvent, ...] = ()


@dataclass(frozen=True)
class _RawRecord:
    notional_usd: int
    pair: CrossExPair
    group: CrossExGroup
    identity: AlertIdentity
    timestamp: int


def _maturity_date(timestamp: int) -> date:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()


def _identity(group: CrossExGroup, pair: CrossExPair) -> AlertIdentity:
    group_asset = canonical_live_asset(group.underlying)
    pair_asset = canonical_live_asset(pair.base)
    if group_asset != pair_asset:
        raise ValueError("group_pair_underlying_mismatch")
    return AlertIdentity(
        asset=group_asset,
        maturity=_maturity_date(group.maturity_timestamp),
        short_venue=normalize_venue(pair.short_leg.crossex_venue),
        long_venue=normalize_venue(pair.long_leg.crossex_venue),
        token_id=group.token_id,
    )


def _valid_economics(pair: CrossExPair) -> bool:
    return (
        pair.exec_spread_apr is not None
        and pair.capital_usd is not None
        and pair.net_fixed_apr_on_capital is not None
        and pair.est_profit_usd is not None
        and pair.net_fixed_apr_on_capital > 0
    )


def _display_venue(value: str) -> str:
    return {
        "HYPERLIQUID": "Hyperliquid",
        "BINANCE": "Binance",
        "BYBIT": "Bybit",
        "GATE": "Gate",
        "OKX": "OKX",
        "KRAKEN": "Kraken",
    }.get(value, value)


def _clock_text(now: datetime | None = None) -> str:
    return (now or datetime.now().astimezone()).strftime("%H:%M:%S")


def _benchmark_age_text(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def render_heartbeat(
    result: MonitorCycleResult,
    *,
    now: datetime | None = None,
) -> str:
    return (
        f"{_clock_text(now)} ✓ CrossEx | "
        f"opps {result.mapped_opportunity_count} | "
        f"benchmarked {result.benchmarkable_opportunity_count} | "
        f"P95 {result.normal_candidate_count} | "
        f"P99 {result.urgent_candidate_count} | "
        f"sent {result.telegram_sent_count} | "
        f"warnings {len(result.warnings)} | "
        f"benchmark {_benchmark_age_text(result.benchmark_age_seconds)}"
    )


def render_delivery_event(
    event: DeliveryEvent,
    *,
    now: datetime | None = None,
) -> str:
    direction = (
        f"{_display_venue(event.short_venue)} → "
        f"{_display_venue(event.long_venue)}"
    )
    if event.delivered:
        percentile = "—" if event.percentile_90d is None else f"P{event.percentile_90d:.1f}"
        icon = "🟠" if event.severity == "NORMAL" else "🔴"
        return f"{_clock_text(now)} {icon} SENT {event.asset} {direction} | {percentile}"
    return f"{_clock_text(now)} ⚠ TELEGRAM FAILED {event.asset} {direction}"


class LiveMonitor:
    def __init__(
        self,
        *,
        client: CrossExClient,
        benchmark: HistoricalBenchmarkLookup,
        state: AlertStateStore,
        telegram_send: Callable[[str], None] | None,
        now_timestamp: Callable[[], int] | None = None,
        database_path: Path = DUCKDB_PATH,
        monitored_notionals: tuple[int, ...] = MONITORED_NOTIONALS,
        poll_interval_seconds: int = 60,
    ) -> None:
        if not monitored_notionals or tuple(sorted(monitored_notionals)) != tuple(monitored_notionals):
            raise ValueError("monitored_notionals must be a non-empty sorted tuple")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.client = client
        self.benchmark = benchmark
        self.state = state
        self.telegram_send = telegram_send
        self.now_timestamp = now_timestamp or (lambda: int(time.time()))
        self.database_path = Path(database_path)
        self.monitored_notionals = monitored_notionals
        self.poll_interval_seconds = poll_interval_seconds

    def _fetch_all(self) -> tuple[dict[int, CrossExResponse], dict[int, Exception]]:
        responses: dict[int, CrossExResponse] = {}
        failures: dict[int, Exception] = {}
        with ThreadPoolExecutor(max_workers=len(self.monitored_notionals)) as pool:
            futures = {
                pool.submit(self.client.fetch, notional): notional
                for notional in self.monitored_notionals
            }
            for future in as_completed(futures):
                notional = futures[future]
                try:
                    responses[notional] = future.result()
                except Exception as exc:
                    failures[notional] = exc
        return responses, failures

    def _records(
        self,
        responses: dict[int, CrossExResponse],
        skipped: list[str],
        warnings: list[str],
    ) -> dict[str, dict[int, _RawRecord]]:
        by_identity: dict[str, dict[int, _RawRecord]] = defaultdict(dict)
        for notional in sorted(responses):
            response = responses[notional]
            warnings.extend(response.warnings)
            for group in response.groups:
                warnings.extend(group.warnings)
                for pair in group.pairs:
                    try:
                        identity = _identity(group, pair)
                    except (TypeError, ValueError) as exc:
                        skipped.append(f"invalid_live_identity:{type(exc).__name__}")
                        continue
                    if identity.short_venue == identity.long_venue:
                        skipped.append("same_venue_pair")
                        continue
                    if (
                        identity.short_venue not in CROSSEX_VENUES
                        or identity.long_venue not in CROSSEX_VENUES
                    ):
                        skipped.append("unsupported_venue")
                        continue
                    record = _RawRecord(
                        notional_usd=notional,
                        pair=pair,
                        group=group,
                        identity=identity,
                        timestamp=response.as_of_timestamp,
                    )
                    previous = by_identity[identity.key].get(notional)
                    if previous is not None:
                        same_identity = (
                            previous.pair.short_leg.market_id == pair.short_leg.market_id
                            and previous.pair.long_leg.market_id == pair.long_leg.market_id
                            and previous.group.maturity_timestamp == group.maturity_timestamp
                            and previous.pair.exec_spread_apr == pair.exec_spread_apr
                        )
                        if not same_identity:
                            by_identity[identity.key].pop(notional, None)
                            skipped.append("conflicting_live_identity")
                            continue
                    by_identity[identity.key][notional] = record
        return by_identity

    def _candidate(self, record: _RawRecord) -> LiveBenchmarkCandidate | None:
        if record.pair.exec_spread_apr is None:
            return None
        dte_days = (
            record.identity.maturity
            - _maturity_date(record.timestamp)
        ).days
        if dte_days < 0:
            return None
        dte_bucket = "0-7" if dte_days <= 7 else (
            "8-21" if dte_days <= 21 else (
                "22-45" if dte_days <= 45 else ("46-90" if dte_days <= 90 else "91+")
            )
        )
        return LiveBenchmarkCandidate(
            candidate_id=f"{record.identity.key}|{record.notional_usd}",
            asset=record.identity.asset,
            short_venue=record.identity.short_venue,
            long_venue=record.identity.long_venue,
            token_id=record.identity.token_id,
            notional_usd=float(record.notional_usd),
            maturity=record.identity.maturity,
            dte_bucket=dte_bucket,
            timestamp=record.timestamp,
            spread_apr=record.pair.exec_spread_apr,
        )

    def _identity_is_historical(self, candidate: LiveBenchmarkCandidate, record: _RawRecord) -> bool:
        validator = getattr(self.benchmark, "validate_market_identity", None)
        if validator is None:
            return True
        for market_id, venue in (
            (record.pair.short_leg.market_id, candidate.short_venue),
            (record.pair.long_leg.market_id, candidate.long_venue),
        ):
            ok, reason = validator(market_id=market_id, venue=venue, candidate=candidate)
            if not ok:
                return False
        return True

    def run_once(self, *, dry_run: bool = False) -> MonitorCycleResult:
        responses, failures = self._fetch_all()
        group_counts = {notional: len(responses[notional].groups) for notional in sorted(responses)}
        unavailable = tuple(sorted(failures))
        skipped = [f"notional_{notional}_unavailable" for notional in unavailable]
        warnings: list[str] = []
        records = self._records(responses, skipped, warnings)

        candidates: list[LiveBenchmarkCandidate] = []
        record_by_candidate: dict[str, _RawRecord] = {}
        size_records: dict[str, dict[int, LiveSizeRecord | None]] = {
            key: {notional: None for notional in self.monitored_notionals}
            for key in records
        }
        for identity_key, per_size in records.items():
            for notional, record in per_size.items():
                size_records[identity_key][notional] = LiveSizeRecord(
                    notional_usd=notional,
                    pair=record.pair,
                    benchmark=None,
                    candidate=None,
                    identity=record.identity,
                    group=record.group,
                )
        for identity_key, per_size in records.items():
            for notional, record in per_size.items():
                candidate = self._candidate(record)
                if candidate is None:
                    continue
                if not self._identity_is_historical(candidate, record):
                    skipped.append("historical_identity_unavailable")
                    continue
                candidates.append(candidate)
                record_by_candidate[candidate.candidate_id] = record

        benchmark_results = self.benchmark.lookup_many(candidates) if candidates else {}
        historical_max = next(
            (result.historical_max_timestamp for result in benchmark_results.values() if result.historical_max_timestamp is not None),
            None,
        )
        benchmark_age = next(
            (result.benchmark_age_seconds for result in benchmark_results.values() if result.benchmark_age_seconds is not None),
            None,
        )
        if historical_max is None:
            health = getattr(self.benchmark, "health", None)
            if health is not None:
                try:
                    historical_max, benchmark_age, _fresh = health()
                except Exception:
                    skipped.append("historical_benchmark_unavailable")
        for candidate in candidates:
            record = record_by_candidate[candidate.candidate_id]
            benchmark = benchmark_results.get(candidate.candidate_id)
            size_records[record.identity.key][record.notional_usd] = LiveSizeRecord(
                notional_usd=record.notional_usd,
                pair=record.pair,
                benchmark=benchmark,
                candidate=candidate,
                identity=record.identity,
                group=record.group,
            )

        messages: list[str] = []
        delivery_events: list[DeliveryEvent] = []
        telegram_sent_count = 0
        normal_count = 0
        urgent_count = 0
        active_state_keys: set[str] = set()
        primary_benchmarkable = 0
        ordered_identity_keys = sorted(size_records)
        for identity_key in ordered_identity_keys:
            sizes = size_records[identity_key]
            primary = sizes.get(10_000)
            if primary is None:
                continue
            active_state_keys.add(identity_key)
            primary_benchmark = primary.benchmark
            primary_valid = (
                primary_benchmark is not None
                and primary_benchmark.benchmark_fresh
                and primary_benchmark.percentile_90d is not None
                and _valid_economics(primary.pair)
            )
            if primary.candidate is not None:
                primary_benchmarkable += 1
            if primary_valid:
                percentile = primary_benchmark.percentile_90d
                assert percentile is not None
                if percentile >= 99.0:
                    urgent_count += 1
                elif percentile >= 95.0:
                    normal_count += 1
                decision = self.state.evaluate(
                    primary.identity,
                    primary.candidate.timestamp if primary.candidate else self.now_timestamp(),
                    percentile,
                    valid=True,
                )
            else:
                if primary.pair.reasons:
                    skipped.append("cross_ex_economics_unavailable")
                decision = self.state.evaluate(
                    primary.identity,
                    primary.candidate.timestamp if primary.candidate else self.now_timestamp(),
                    None,
                    valid=False,
                )
            if decision.severity is not None and primary_valid:
                alert = AlertMessage(
                    severity=decision.severity,
                    asset=primary.identity.asset,
                    short_venue=primary.identity.short_venue,
                    long_venue=primary.identity.long_venue,
                    maturity=primary.identity.maturity,
                    remaining_days=max(0, math.ceil(primary.group.seconds_to_maturity / 86_400)),
                    primary=SizeMessage(10_000, primary.pair, primary.benchmark),
                    sizes=tuple(
                        SizeMessage(notional, item.pair, item.benchmark) if item is not None else SizeMessage(notional, None, None)
                        for notional, item in sorted(sizes.items())
                    ),
                    live_detected_minutes=decision.live_detected_minutes,
                    p95_live_detected_minutes=getattr(
                        decision,
                        "p95_live_detected_minutes",
                        decision.live_detected_minutes,
                    ),
                    warnings=tuple(dict.fromkeys(warnings)),
                )
                message = format_opportunity_message(alert)
                messages.append(message)
                delivery_timestamp = (
                    primary.candidate.timestamp
                    if primary.candidate is not None
                    else self.now_timestamp()
                )
                if dry_run:
                    # Dry runs use an ephemeral store and intentionally
                    # simulate a successful delivery for one-cycle state.
                    self.state.commit_alert_delivered(
                        primary.identity, delivery_timestamp, decision.severity
                    )
                elif self.telegram_send is None:
                    skipped.append("telegram_sender_unavailable")
                else:
                    try:
                        self.telegram_send(message)
                    except Exception:
                        # Keep the alert armed so a later cycle can make one
                        # fresh delivery attempt.  Never retry in this cycle.
                        skipped.append("telegram_send_failed")
                        delivery_events.append(
                            DeliveryEvent(
                                severity=decision.severity,
                                asset=primary.identity.asset,
                                short_venue=primary.identity.short_venue,
                                long_venue=primary.identity.long_venue,
                                percentile_90d=primary_benchmark.percentile_90d,
                                delivered=False,
                            )
                        )
                    else:
                        telegram_sent_count += 1
                        delivery_events.append(
                            DeliveryEvent(
                                severity=decision.severity,
                                asset=primary.identity.asset,
                                short_venue=primary.identity.short_venue,
                                long_venue=primary.identity.long_venue,
                                percentile_90d=primary_benchmark.percentile_90d,
                                delivered=True,
                            )
                        )
                        self.state.commit_alert_delivered(
                            primary.identity, delivery_timestamp, decision.severity
                        )

        self.state.mark_missing_except(active_state_keys, self.now_timestamp())
        return MonitorCycleResult(
            group_counts=group_counts,
            unavailable_notionals=unavailable,
            mapped_opportunity_count=len(size_records),
            benchmarkable_opportunity_count=primary_benchmarkable,
            normal_candidate_count=normal_count,
            urgent_candidate_count=urgent_count,
            skipped_reasons=tuple(sorted(set(skipped))),
            warnings=tuple(dict.fromkeys(warnings)),
            messages=tuple(messages),
            sizes_by_opportunity=tuple(size_records[key] for key in ordered_identity_keys),
            historical_max_timestamp=historical_max,
            benchmark_age_seconds=benchmark_age,
            telegram_sent_count=telegram_sent_count,
            delivery_events=tuple(delivery_events),
        )

    def run_forever(self, *, dry_run: bool = False) -> None:
        while True:
            started = time.monotonic()
            try:
                result = self.run_once(dry_run=dry_run)
                now = datetime.now().astimezone()
                print(render_heartbeat(result, now=now), flush=True)
                for event in result.delivery_events:
                    print(render_delivery_event(event, now=now), flush=True)
            except Exception as exc:
                print(f"CrossEx monitor cycle failed: {type(exc).__name__}")
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, self.poll_interval_seconds - elapsed))


def render_cycle_summary(result: MonitorCycleResult) -> str:
    lines = [
        "CrossEx monitor (read-only)",
        "Groups by notional: " + ", ".join(
            f"${size:,}={result.group_counts.get(size, 0)}" for size in MONITORED_NOTIONALS
        ),
        f"Mapped opportunities: {result.mapped_opportunity_count}",
        f"Benchmarkable opportunities: {result.benchmarkable_opportunity_count}",
        f"p95 candidates: {result.normal_candidate_count}",
        f"p99 candidates: {result.urgent_candidate_count}",
        f"Unavailable notionals: {','.join(map(str, result.unavailable_notionals)) or 'none'}",
        f"Benchmark max timestamp: {result.historical_max_timestamp or 'unknown'}",
        f"Benchmark age seconds: {result.benchmark_age_seconds if result.benchmark_age_seconds is not None else 'unknown'}",
    ]
    if result.skipped_reasons:
        lines.append("Skipped: " + ", ".join(result.skipped_reasons))
    if result.warnings:
        lines.append(f"CrossEx warnings: {len(result.warnings)}")
    if result.benchmark_age_seconds is not None and result.benchmark_age_seconds > 7 * 86400:
        lines.append("WARNING: historical benchmark too old; refresh Phase 1/2 data")
    return "\n".join(lines)
