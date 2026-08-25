"""Durable lifecycle state for the read-only open-position monitor."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

from .position_models import (
    Attribution,
    HedgeChecks,
    PositionLeg,
    StrategySnapshot,
)


NEW_STRATEGY = "NEW_STRATEGY"
HEDGE_WARNING = "HEDGE_WARNING"
HEDGE_RECOVERED = "HEDGE_RECOVERED"
MATURITY_14D = "MATURITY_14D"
MATURITY_7D = "MATURITY_7D"
MATURITY_3D = "MATURITY_3D"
MATURITY_1D = "MATURITY_1D"
STRATEGY_DISAPPEARED = "STRATEGY_DISAPPEARED"

_MATURITY_KINDS = {
    14: MATURITY_14D,
    7: MATURITY_7D,
    3: MATURITY_3D,
    1: MATURITY_1D,
}
_MATURITY_COLUMNS = {
    14: "maturity_14d_delivered",
    7: "maturity_7d_delivered",
    3: "maturity_3d_delivered",
    1: "maturity_1d_delivered",
}


@dataclass(frozen=True)
class PositionEvent:
    kind: str
    strategy_id: str
    lifecycle: int
    snapshot: StrategySnapshot
    previous_fully_hedged: bool | None = None
    current_fully_hedged: bool | None = None
    threshold_days: int | None = None


@dataclass(frozen=True)
class PositionLifecycle:
    strategy_id: str
    lifecycle: int
    first_seen_at: int
    last_seen_at: int
    active: bool
    successful_absence_count: int
    last_observed_fully_hedged: bool | None
    notified_fully_hedged: bool | None
    new_event_delivered: bool
    disappeared_event_delivered: bool
    maturity_14d_delivered: bool
    maturity_7d_delivered: bool
    maturity_3d_delivered: bool
    maturity_1d_delivered: bool
    last_snapshot: StrategySnapshot


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _snapshot_json(snapshot: StrategySnapshot) -> str:
    return json.dumps(_jsonable(snapshot), sort_keys=True, separators=(",", ":"))


def _snapshot_from_json(payload: str) -> StrategySnapshot:
    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("stored position snapshot must be an object")
    legs = raw.get("legs")
    checks = raw.get("hedge_checks")
    attribution = raw.get("attribution")
    if not isinstance(legs, list) or not isinstance(checks, dict) or not isinstance(attribution, dict):
        raise ValueError("stored position snapshot is incomplete")
    return StrategySnapshot(
        strategy_id=raw["strategy_id"],
        base=raw["base"],
        maturity=raw["maturity"],
        legs=tuple(PositionLeg(**leg) for leg in legs),
        hedge=raw.get("hedge"),
        hedge_checks=HedgeChecks(**checks),
        capital_usd=raw.get("capital_usd"),
        capital_split=raw.get("capital_split"),
        realized_pnl_usd=raw.get("realized_pnl_usd"),
        realized_apr=raw.get("realized_apr"),
        spread=raw.get("spread"),
        locked_apr_on_capital=raw.get("locked_apr_on_capital"),
        expected_pnl_to_maturity_usd=raw.get("expected_pnl_to_maturity_usd"),
        seconds_to_maturity=raw["seconds_to_maturity"],
        notional_mismatch_usd=raw.get("notional_mismatch_usd"),
        attribution=Attribution(**attribution),
        warnings=tuple(raw.get("warnings", ())),
    )


def _bool_value(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _bool_from_db(value: Any) -> bool | None:
    return None if value is None else bool(value)


class PositionStateStore:
    """SQLite-backed observation and notification state for P2."""

    def __init__(self, path: str | Path, *, poll_interval_seconds: int = 60) -> None:
        if isinstance(poll_interval_seconds, bool) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.poll_interval_seconds = poll_interval_seconds
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS position_state (
                strategy_id TEXT PRIMARY KEY,
                lifecycle INTEGER NOT NULL,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                active INTEGER NOT NULL,
                successful_absence_count INTEGER NOT NULL DEFAULT 0,
                last_observed_fully_hedged INTEGER,
                notified_fully_hedged INTEGER,
                new_event_delivered INTEGER NOT NULL DEFAULT 0,
                disappeared_event_delivered INTEGER NOT NULL DEFAULT 0,
                maturity_14d_delivered INTEGER NOT NULL DEFAULT 0,
                maturity_7d_delivered INTEGER NOT NULL DEFAULT 0,
                maturity_3d_delivered INTEGER NOT NULL DEFAULT 0,
                maturity_1d_delivered INTEGER NOT NULL DEFAULT 0,
                last_snapshot_json TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def _row(self, strategy_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM position_state WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()

    def _insert_new(self, snapshot: StrategySnapshot, timestamp: int) -> None:
        self._connection.execute(
            """
            INSERT INTO position_state (
                strategy_id, lifecycle, first_seen_at, last_seen_at, active,
                successful_absence_count, last_observed_fully_hedged,
                notified_fully_hedged, new_event_delivered,
                disappeared_event_delivered, maturity_14d_delivered,
                maturity_7d_delivered, maturity_3d_delivered,
                maturity_1d_delivered, last_snapshot_json
            ) VALUES (?, 1, ?, ?, 1, 0, ?, NULL, 0, 0, 0, 0, 0, 0, ?)
            """,
            (
                snapshot.strategy_id,
                timestamp,
                timestamp,
                int(snapshot.hedge_checks.fully_hedged),
                _snapshot_json(snapshot),
            ),
        )

    def _update_present(self, snapshot: StrategySnapshot, timestamp: int) -> None:
        row = self._row(snapshot.strategy_id)
        if row is None:
            self._insert_new(snapshot, timestamp)
            return
        if not row["active"]:
            self._connection.execute(
                """
                UPDATE position_state
                SET lifecycle = lifecycle + 1,
                    first_seen_at = ?, last_seen_at = ?, active = 1,
                    successful_absence_count = 0,
                    last_observed_fully_hedged = ?,
                    notified_fully_hedged = NULL,
                    new_event_delivered = 0,
                    disappeared_event_delivered = 0,
                    last_snapshot_json = ?
                WHERE strategy_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    int(snapshot.hedge_checks.fully_hedged),
                    _snapshot_json(snapshot),
                    snapshot.strategy_id,
                ),
            )
            return
        self._connection.execute(
            """
            UPDATE position_state
            SET last_seen_at = ?, active = 1, successful_absence_count = 0,
                last_observed_fully_hedged = ?, last_snapshot_json = ?
            WHERE strategy_id = ?
            """,
            (
                timestamp,
                int(snapshot.hedge_checks.fully_hedged),
                _snapshot_json(snapshot),
                snapshot.strategy_id,
            ),
        )

    def observe_success(
        self, strategies: Sequence[StrategySnapshot], timestamp: int
    ) -> tuple[PositionEvent, ...]:
        """Record one successful primary inventory and return current events."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("timestamp must be an integer")
        snapshots = tuple(strategies)
        ids = [snapshot.strategy_id for snapshot in snapshots]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate strategyId in observed inventory")
        present_ids = set(ids)
        with self._connection:
            for snapshot in snapshots:
                self._update_present(snapshot, timestamp)
            rows = self._connection.execute(
                "SELECT strategy_id, successful_absence_count FROM position_state WHERE active = 1"
            ).fetchall()
            for row in rows:
                if row["strategy_id"] not in present_ids:
                    self._connection.execute(
                        """
                        UPDATE position_state
                        SET successful_absence_count = MIN(successful_absence_count + 1, 3)
                        WHERE strategy_id = ? AND active = 1
                        """,
                        (row["strategy_id"],),
                    )
        return self.pending_events_for_cycle(present_ids)

    def observe_unknown(self, timestamp: int) -> None:
        """Break absence continuity without changing last-known observations."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("timestamp must be an integer")
        with self._connection:
            self._connection.execute(
                "UPDATE position_state SET successful_absence_count = 0 WHERE active = 1"
            )

    def _snapshot_for_row(self, row: sqlite3.Row) -> StrategySnapshot:
        return _snapshot_from_json(row["last_snapshot_json"])

    def _events_for_row(self, row: sqlite3.Row, *, present: bool) -> tuple[PositionEvent, ...]:
        if not row["active"]:
            return ()
        snapshot = self._snapshot_for_row(row)
        if not present:
            if row["successful_absence_count"] < 3 or row["disappeared_event_delivered"]:
                return ()
            return (
                PositionEvent(
                    STRATEGY_DISAPPEARED,
                    row["strategy_id"],
                    row["lifecycle"],
                    snapshot,
                    current_fully_hedged=_bool_from_db(row["last_observed_fully_hedged"]),
                ),
            )

        events: list[PositionEvent] = []
        current_hedged = snapshot.hedge_checks.fully_hedged
        if not row["new_event_delivered"]:
            events.append(
                PositionEvent(
                    NEW_STRATEGY,
                    row["strategy_id"],
                    row["lifecycle"],
                    snapshot,
                    current_fully_hedged=current_hedged,
                )
            )
        else:
            notified = _bool_from_db(row["notified_fully_hedged"])
            if notified is not None and notified != current_hedged:
                events.append(
                    PositionEvent(
                        HEDGE_RECOVERED if current_hedged else HEDGE_WARNING,
                        row["strategy_id"],
                        row["lifecycle"],
                        snapshot,
                        previous_fully_hedged=notified,
                        current_fully_hedged=current_hedged,
                    )
                )

        for threshold in (1, 3, 7, 14):
            column = _MATURITY_COLUMNS[threshold]
            if (
                not row[column]
                and 0 < snapshot.seconds_to_maturity <= threshold * 86400
            ):
                events.append(
                    PositionEvent(
                        _MATURITY_KINDS[threshold],
                        row["strategy_id"],
                        row["lifecycle"],
                        snapshot,
                        threshold_days=threshold,
                    )
                )
                break
        return tuple(events)

    def pending_events_for_cycle(self, present_ids: set[str]) -> tuple[PositionEvent, ...]:
        rows = self._connection.execute("SELECT * FROM position_state ORDER BY strategy_id").fetchall()
        events: list[PositionEvent] = []
        for row in rows:
            events.extend(self._events_for_row(row, present=row["strategy_id"] in present_ids))
        return tuple(events)

    def pending_events(self, strategy_id: str) -> tuple[PositionEvent, ...]:
        row = self._row(strategy_id)
        if row is None:
            return ()
        return self._events_for_row(row, present=True)

    def commit_event(self, event: PositionEvent, timestamp: int) -> bool:
        """Consume one event only if its condition is still current."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("timestamp must be an integer")
        row = self._row(event.strategy_id)
        if row is None or row["lifecycle"] != event.lifecycle:
            return False
        snapshot = self._snapshot_for_row(row)
        with self._connection:
            if event.kind == NEW_STRATEGY:
                if not row["active"] or row["new_event_delivered"]:
                    return False
                self._connection.execute(
                    """
                    UPDATE position_state
                    SET new_event_delivered = 1, notified_fully_hedged = ?
                    WHERE strategy_id = ? AND lifecycle = ?
                    """,
                    (
                        int(snapshot.hedge_checks.fully_hedged),
                        event.strategy_id,
                        event.lifecycle,
                    ),
                )
                return True

            if event.kind in {HEDGE_WARNING, HEDGE_RECOVERED}:
                current = snapshot.hedge_checks.fully_hedged
                notified = _bool_from_db(row["notified_fully_hedged"])
                if (
                    not row["active"]
                    or not row["new_event_delivered"]
                    or notified != event.previous_fully_hedged
                    or current != event.current_fully_hedged
                ):
                    return False
                self._connection.execute(
                    "UPDATE position_state SET notified_fully_hedged = ? WHERE strategy_id = ?",
                    (int(current), event.strategy_id),
                )
                return True

            if event.kind in _MATURITY_KINDS.values():
                threshold = event.threshold_days
                if threshold is None or not row["active"]:
                    return False
                column = _MATURITY_COLUMNS[threshold]
                if (
                    row[column]
                    or not 0 < snapshot.seconds_to_maturity <= threshold * 86400
                ):
                    return False
                columns = [_MATURITY_COLUMNS[day] for day in (1, 3, 7, 14) if day >= threshold]
                assignments = ", ".join(f"{name} = 1" for name in columns)
                self._connection.execute(
                    f"UPDATE position_state SET {assignments} WHERE strategy_id = ?",
                    (event.strategy_id,),
                )
                return True

            if event.kind == STRATEGY_DISAPPEARED:
                if (
                    not row["active"]
                    or row["successful_absence_count"] < 3
                    or row["disappeared_event_delivered"]
                ):
                    return False
                self._connection.execute(
                    """
                    UPDATE position_state
                    SET active = 0, disappeared_event_delivered = 1
                    WHERE strategy_id = ? AND lifecycle = ?
                    """,
                    (event.strategy_id, event.lifecycle),
                )
                return True
        return False

    def snapshot(self, strategy_id: str) -> PositionLifecycle | None:
        row = self._row(strategy_id)
        if row is None:
            return None
        return PositionLifecycle(
            strategy_id=row["strategy_id"],
            lifecycle=row["lifecycle"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            active=bool(row["active"]),
            successful_absence_count=row["successful_absence_count"],
            last_observed_fully_hedged=_bool_from_db(row["last_observed_fully_hedged"]),
            notified_fully_hedged=_bool_from_db(row["notified_fully_hedged"]),
            new_event_delivered=bool(row["new_event_delivered"]),
            disappeared_event_delivered=bool(row["disappeared_event_delivered"]),
            maturity_14d_delivered=bool(row["maturity_14d_delivered"]),
            maturity_7d_delivered=bool(row["maturity_7d_delivered"]),
            maturity_3d_delivered=bool(row["maturity_3d_delivered"]),
            maturity_1d_delivered=bool(row["maturity_1d_delivered"]),
            last_snapshot=self._snapshot_for_row(row),
        )

    def strategy_ids(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT strategy_id FROM position_state ORDER BY strategy_id"
        ).fetchall()
        return tuple(row["strategy_id"] for row in rows)
