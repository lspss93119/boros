"""Durable, fail-closed live alert and persistence state."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path


REARM_SECONDS = 6 * 60 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 60
P95 = 95.0
P99 = 99.0


@dataclass(frozen=True)
class AlertIdentity:
    asset: str
    maturity: date
    short_venue: str
    long_venue: str
    token_id: int

    @property
    def key(self) -> str:
        return json.dumps(
            {
                "asset": self.asset,
                "long_venue": self.long_venue,
                "maturity": self.maturity.isoformat(),
                "short_venue": self.short_venue,
                "token_id": self.token_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class AlertSnapshot:
    identity: AlertIdentity
    p95_armed: bool
    p99_armed: bool
    p95_below_since: int | None
    p99_below_since: int | None
    p95_last_alert_timestamp: int | None
    p99_last_alert_timestamp: int | None
    p95_run_start: int | None
    p95_run_last_success: int | None
    p95_observation_count: int
    p99_run_start: int | None
    p99_run_last_success: int | None
    p99_observation_count: int
    last_success_timestamp: int | None


@dataclass(frozen=True)
class AlertDecision:
    severity: str | None
    live_detected_minutes: int
    p95_above: bool
    p99_above: bool


def _default_snapshot(identity: AlertIdentity) -> AlertSnapshot:
    return AlertSnapshot(
        identity=identity,
        p95_armed=True,
        p99_armed=True,
        p95_below_since=None,
        p99_below_since=None,
        p95_last_alert_timestamp=None,
        p99_last_alert_timestamp=None,
        p95_run_start=None,
        p95_run_last_success=None,
        p95_observation_count=0,
        p99_run_start=None,
        p99_run_last_success=None,
        p99_observation_count=0,
        last_success_timestamp=None,
    )


class AlertStateStore:
    """SQLite state store that never writes into the Phase 1/2 DuckDB."""

    def __init__(
        self,
        path: str | Path,
        *,
        rearm_seconds: int = REARM_SECONDS,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if rearm_seconds <= 0:
            raise ValueError("rearm_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_state (
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
        self.rearm_seconds = rearm_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.continuity_tolerance_seconds = max(2 * poll_interval_seconds, 120)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "AlertStateStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _ensure(self, identity: AlertIdentity) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO alert_state
                (identity_key, asset, maturity, short_venue, long_venue, token_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identity.key,
                identity.asset,
                identity.maturity.isoformat(),
                identity.short_venue,
                identity.long_venue,
                identity.token_id,
            ),
        )

    def snapshot(self, identity: AlertIdentity) -> AlertSnapshot:
        row = self._connection.execute(
            "SELECT * FROM alert_state WHERE identity_key = ?", (identity.key,)
        ).fetchone()
        if row is None:
            return _default_snapshot(identity)
        columns = [item[1] for item in self._connection.execute("PRAGMA table_info(alert_state)").fetchall()]
        values = dict(zip(columns, row))
        return AlertSnapshot(
            identity=identity,
            p95_armed=bool(values["p95_armed"]),
            p99_armed=bool(values["p99_armed"]),
            p95_below_since=values["p95_below_since"],
            p99_below_since=values["p99_below_since"],
            p95_last_alert_timestamp=values["p95_last_alert_timestamp"],
            p99_last_alert_timestamp=values["p99_last_alert_timestamp"],
            p95_run_start=values["p95_run_start"],
            p95_run_last_success=values["p95_run_last_success"],
            p95_observation_count=values["p95_observation_count"],
            p99_run_start=values["p99_run_start"],
            p99_run_last_success=values["p99_run_last_success"],
            p99_observation_count=values["p99_observation_count"],
            last_success_timestamp=values["last_success_timestamp"],
        )

    def _mark_unknown(self, identity: AlertIdentity, timestamp: int) -> None:
        self._ensure(identity)
        self._connection.execute(
            """
            UPDATE alert_state SET
                p95_below_since = NULL,
                p99_below_since = NULL,
                p95_run_start = NULL,
                p95_run_last_success = NULL,
                p95_observation_count = 0,
                p99_run_start = NULL,
                p99_run_last_success = NULL,
                p99_observation_count = 0,
                last_poll_timestamp = ?
            WHERE identity_key = ?
            """,
            (timestamp, identity.key),
        )

    def evaluate(
        self,
        identity: AlertIdentity,
        timestamp: int,
        percentile_90d: float | None,
        *,
        valid: bool = True,
    ) -> AlertDecision:
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("timestamp must be a non-negative integer")
        if percentile_90d is not None:
            if isinstance(percentile_90d, bool) or not math.isfinite(float(percentile_90d)):
                raise ValueError("percentile_90d must be finite or null")

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._ensure(identity)
            if not valid or percentile_90d is None:
                self._mark_unknown(identity, timestamp)
                self._connection.execute("COMMIT")
                return AlertDecision(None, 0, False, False)

            value = float(percentile_90d)
            p95_above = value >= P95
            p99_above = value >= P99
            snapshot = self.snapshot(identity)

            continuity_broken = (
                snapshot.last_success_timestamp is not None
                and timestamp - snapshot.last_success_timestamp
                > self.continuity_tolerance_seconds
            )

            p95_run_start = (
                None if continuity_broken else snapshot.p95_run_start
            )
            p95_observation_count = (
                0 if continuity_broken else snapshot.p95_observation_count
            )
            p99_run_start = (
                None if continuity_broken else snapshot.p99_run_start
            )
            p99_observation_count = (
                0 if continuity_broken else snapshot.p99_observation_count
            )
            p95_below_since = None if continuity_broken else snapshot.p95_below_since
            p99_below_since = None if continuity_broken else snapshot.p99_below_since

            p95_run_start = timestamp if p95_above and p95_run_start is None else p95_run_start
            p95_count = p95_observation_count + 1 if p95_above else 0
            p95_last = timestamp if p95_above else None
            p99_run_start = timestamp if p99_above and p99_run_start is None else p99_run_start
            p99_count = p99_observation_count + 1 if p99_above else 0
            p99_last = timestamp if p99_above else None

            p95_armed = snapshot.p95_armed
            p99_armed = snapshot.p99_armed
            if not p95_armed and p95_below_since is not None and timestamp - p95_below_since >= self.rearm_seconds:
                p95_armed = True
                p95_below_since = None
            if p95_above:
                p95_below_since = None
            elif not p95_armed:
                p95_below_since = p95_below_since if p95_below_since is not None else timestamp
            else:
                p95_below_since = None

            if not p99_armed and p99_below_since is not None and timestamp - p99_below_since >= self.rearm_seconds:
                p99_armed = True
                p99_below_since = None
            if p99_above:
                p99_below_since = None
            elif not p99_armed:
                p99_below_since = p99_below_since if p99_below_since is not None else timestamp
            else:
                p99_below_since = None

            severity: str | None = None
            p95_last_alert = snapshot.p95_last_alert_timestamp
            p99_last_alert = snapshot.p99_last_alert_timestamp
            if p99_above and p99_armed:
                severity = "URGENT"
            elif p95_above and p95_armed:
                severity = "NORMAL"

            self._connection.execute(
                """
                UPDATE alert_state SET
                    p95_armed = ?, p99_armed = ?,
                    p95_below_since = ?, p99_below_since = ?,
                    p95_last_alert_timestamp = ?, p99_last_alert_timestamp = ?,
                    p95_run_start = ?, p95_run_last_success = ?, p95_observation_count = ?,
                    p99_run_start = ?, p99_run_last_success = ?, p99_observation_count = ?,
                    last_success_timestamp = ?, last_poll_timestamp = ?
                WHERE identity_key = ?
                """,
                (
                    int(p95_armed),
                    int(p99_armed),
                    p95_below_since,
                    p99_below_since,
                    p95_last_alert,
                    p99_last_alert,
                    p95_run_start,
                    p95_last,
                    p95_count,
                    p99_run_start,
                    p99_last,
                    p99_count,
                    timestamp,
                    timestamp,
                    identity.key,
                ),
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

        run_start = p99_run_start if p99_above else p95_run_start
        duration = 0 if run_start is None else max(0, (timestamp - run_start) // 60)
        return AlertDecision(severity, duration, p95_above, p99_above)

    def commit_alert_delivered(
        self,
        identity: AlertIdentity,
        timestamp: int,
        severity: str,
    ) -> None:
        """Consume an alert only after its delivery has succeeded.

        The caller must perform any network operation before invoking this
        method.  This transaction is intentionally short and never spans a
        Telegram request.
        """
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("timestamp must be a non-negative integer")
        if severity not in {"NORMAL", "URGENT"}:
            raise ValueError("severity must be NORMAL or URGENT")

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._ensure(identity)
            snapshot = self.snapshot(identity)
            if severity == "URGENT" and snapshot.p99_armed:
                self._connection.execute(
                    """
                    UPDATE alert_state SET
                        p95_armed = 0,
                        p99_armed = 0,
                        p99_last_alert_timestamp = ?
                    WHERE identity_key = ?
                    """,
                    (timestamp, identity.key),
                )
            elif severity == "NORMAL" and snapshot.p95_armed:
                self._connection.execute(
                    """
                    UPDATE alert_state SET
                        p95_armed = 0,
                        p95_last_alert_timestamp = ?
                    WHERE identity_key = ?
                    """,
                    (timestamp, identity.key),
                )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def observe(
        self,
        identity: AlertIdentity,
        timestamp: int,
        percentile_90d: float | None,
        *,
        valid: bool = True,
    ) -> AlertDecision:
        """Observe and immediately simulate a successful alert delivery.

        Production monitoring uses :meth:`evaluate` and commits only after
        the sender succeeds.  This compatibility helper is retained for
        callers that intentionally model delivery in one synchronous step.
        """
        decision = self.evaluate(identity, timestamp, percentile_90d, valid=valid)
        if decision.severity is not None:
            self.commit_alert_delivered(identity, timestamp, decision.severity)
        return decision

    def mark_unknown(self, identity: AlertIdentity, timestamp: int) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._mark_unknown(identity, timestamp)
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def mark_missing_except(self, active_identity_keys: set[str], timestamp: int) -> None:
        rows = self._connection.execute("SELECT identity_key FROM alert_state").fetchall()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for (key,) in rows:
                if key not in active_identity_keys:
                    self._mark_unknown_by_key(str(key), timestamp)
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _mark_unknown_by_key(self, key: str, timestamp: int) -> None:
        self._connection.execute(
            """
            UPDATE alert_state SET
                p95_below_since = NULL, p99_below_since = NULL,
                p95_run_start = NULL, p95_run_last_success = NULL, p95_observation_count = 0,
                p99_run_start = NULL, p99_run_last_success = NULL, p99_observation_count = 0,
                last_poll_timestamp = ?
            WHERE identity_key = ?
            """,
            (timestamp, key),
        )

    def identities(self) -> tuple[AlertIdentity, ...]:
        rows = self._connection.execute(
            "SELECT asset, maturity, short_venue, long_venue, token_id FROM alert_state ORDER BY identity_key"
        ).fetchall()
        return tuple(
            AlertIdentity(
                asset=row[0],
                maturity=date.fromisoformat(row[1]),
                short_venue=row[2],
                long_venue=row[3],
                token_id=int(row[4]),
            )
            for row in rows
        )
