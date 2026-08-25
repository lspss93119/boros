"""Localhost-only, read-only bridge for published dashboard snapshots."""

from __future__ import annotations

import json
import mimetypes
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .dashboard_snapshot import (
    DashboardSnapshotError,
    P1_DASHBOARD_SNAPSHOT,
    P2_DASHBOARD_SNAPSHOT,
    read_snapshot,
)


DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765
FRESH_MULTIPLIER = 2.5
OFFLINE_MULTIPLIER = 10.0
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1"}


def freshness(age_seconds: float | int | None, interval_seconds: int) -> str:
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if age_seconds is None:
        return "offline"
    if age_seconds <= FRESH_MULTIPLIER * interval_seconds:
        return "fresh"
    if age_seconds <= OFFLINE_MULTIPLIER * interval_seconds:
        return "stale"
    return "offline"


def _valid_host(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        hostname is not None
        and hostname.lower() in _LOCAL_HOSTNAMES
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


def _valid_origin(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and hostname is not None
        and hostname.lower() in _LOCAL_HOSTNAMES
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


def _compatible_snapshot_data(kind: str, snapshot: Mapping[str, Any]) -> bool:
    data = snapshot.get("data")
    if not isinstance(data, Mapping):
        return False
    if kind == "p1":
        return isinstance(data.get("counts"), Mapping) and isinstance(
            data.get("currentOpportunities"), list
        )
    if kind == "p2":
        return isinstance(data.get("strategies"), list) and (
            data.get("positions") is None or isinstance(data.get("positions"), Mapping)
        )
    return False


class _DashboardRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def dashboard_server(self) -> "DashboardHTTPServer":
        return self.server  # type: ignore[return-value]

    def _now(self) -> int:
        return int(self.dashboard_server.now_timestamp())

    def _security_headers(self) -> dict[str, str]:
        return {
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Cache-Control": "no-store",
        }

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        for key, value in self._security_headers().items():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_envelope(self, status: int, code: str) -> None:
        self._send_json(
            status,
            {
                "ok": False,
                "error": {"code": code, "message": "dashboard request failed"},
                "meta": {"ts": self._now()},
            },
        )

    def _request_policy_allows(self) -> bool:
        if not _valid_host(self.headers.get("Host")):
            self._send_error_envelope(HTTPStatus.FORBIDDEN, "host_not_allowed")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and not _valid_origin(origin):
            self._send_error_envelope(HTTPStatus.FORBIDDEN, "origin_not_allowed")
            return False
        return True

    def _snapshot_path(self, kind: str) -> Path:
        filename = (
            P1_DASHBOARD_SNAPSHOT.name
            if kind == "p1"
            else P2_DASHBOARD_SNAPSHOT.name
        )
        return self.dashboard_server.snapshot_root / filename

    def _snapshot_component(self, kind: str) -> dict[str, Any]:
        try:
            snapshot = read_snapshot(self._snapshot_path(kind), kind)
        except DashboardSnapshotError:
            return {"status": "invalid", "freshness": "offline"}
        if snapshot is None:
            return {"status": "offline", "freshness": "offline"}
        if not _compatible_snapshot_data(kind, snapshot):
            return {"status": "invalid", "freshness": "offline"}
        age = self._now() - snapshot["cycleTimestamp"]
        state = freshness(age, snapshot["pollIntervalSeconds"])
        return {
            "status": snapshot["sourceStatus"],
            "freshness": state,
            "cycleTimestamp": snapshot["cycleTimestamp"],
            "lastGoodTimestamp": snapshot["lastGoodTimestamp"],
            "pollIntervalSeconds": snapshot["pollIntervalSeconds"],
        }

    def _handle_health(self) -> None:
        components = {
            "p1": self._snapshot_component("p1"),
            "p2": self._snapshot_component("p2"),
        }
        status_values = {item["status"] for item in components.values()}
        freshness_values = {item["freshness"] for item in components.values()}
        overall = (
            "ok"
            if status_values == {"ok"} and freshness_values == {"fresh"}
            else "degraded"
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "data": {
                    "status": overall,
                    "server": "ok",
                    "components": components,
                },
                "meta": {"ts": self._now()},
            },
        )

    def _handle_snapshot(self, kind: str) -> None:
        try:
            snapshot = read_snapshot(self._snapshot_path(kind), kind)
        except DashboardSnapshotError:
            self._send_error_envelope(HTTPStatus.SERVICE_UNAVAILABLE, "snapshot_invalid")
            return
        if snapshot is None:
            self._send_error_envelope(HTTPStatus.SERVICE_UNAVAILABLE, "snapshot_unavailable")
            return
        if not _compatible_snapshot_data(kind, snapshot):
            self._send_error_envelope(HTTPStatus.SERVICE_UNAVAILABLE, "snapshot_invalid")
            return
        age = self._now() - snapshot["cycleTimestamp"]
        data = dict(snapshot["data"])
        data.update(
            {
                "sourceStatus": snapshot["sourceStatus"],
                "freshness": freshness(age, snapshot["pollIntervalSeconds"]),
                "cycleTimestamp": snapshot["cycleTimestamp"],
                "lastGoodTimestamp": snapshot["lastGoodTimestamp"],
                "pollIntervalSeconds": snapshot["pollIntervalSeconds"],
                "diagnostics": snapshot["diagnostics"],
            }
        )
        self._send_json(
            HTTPStatus.OK,
            {"ok": True, "data": data, "meta": {"ts": self._now()}},
        )

    def _handle_api(self, path: str) -> bool:
        routes = {
            "/api/dashboard/health": self._handle_health,
            "/api/dashboard/opportunities": lambda: self._handle_snapshot("p1"),
            "/api/dashboard/positions": lambda: self._handle_snapshot("p2"),
        }
        handler = routes.get(path)
        if handler is None:
            return False
        handler()
        return True

    def _safe_static_path(self, path: str) -> Path | None:
        try:
            decoded = unquote(path)
        except (UnicodeDecodeError, ValueError):
            return None
        if "\x00" in decoded:
            return None
        relative = decoded.lstrip("/")
        candidate = (self.dashboard_server.site_root / relative).resolve()
        try:
            candidate.relative_to(self.dashboard_server.site_root)
        except ValueError:
            return None
        return candidate

    def _handle_static(self, path: str) -> None:
        if path == "/":
            self._send_bytes(
                HTTPStatus.FOUND,
                b"",
                "text/plain; charset=utf-8",
                extra_headers={"Location": "/dashboard/"},
            )
            return
        if path == "/dashboard":
            self._send_bytes(
                HTTPStatus.FOUND,
                b"",
                "text/plain; charset=utf-8",
                extra_headers={"Location": "/dashboard/"},
            )
            return
        if path == "/dashboard/":
            path = "/dashboard/index.html"
        candidate = self._safe_static_path(path)
        if candidate is None:
            self._send_error_envelope(HTTPStatus.FORBIDDEN, "path_not_allowed")
            return
        if not candidate.is_file():
            self._send_error_envelope(HTTPStatus.NOT_FOUND, "not_found")
            return
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_error_envelope(HTTPStatus.NOT_FOUND, "not_found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._request_policy_allows():
            return
        path = urlsplit(self.path).path
        if self._handle_api(path):
            return
        self._handle_static(path)

    def _method_not_allowed(self) -> None:
        if not self._request_policy_allows():
            return
        self._send_error_envelope(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed")
        # The generic sender intentionally does not accept arbitrary methods.
        # Add Allow after the fact is impossible, so use a small explicit response.

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_method_response()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_method_response()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_method_response()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_method_response()

    def _send_method_response(self) -> None:
        if not self._request_policy_allows():
            return
        payload = {
            "ok": False,
            "error": {"code": "method_not_allowed", "message": "dashboard request failed"},
            "meta": {"ts": self._now()},
        }
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(
            HTTPStatus.METHOD_NOT_ALLOWED,
            body,
            "application/json; charset=utf-8",
            extra_headers={"Allow": "GET"},
        )


class DashboardHTTPServer(ThreadingHTTPServer):
    """Threaded localhost server for snapshots and existing static research files."""

    allow_reuse_address = True

    def __init__(
        self,
        *,
        site_dir: str | Path = Path("site"),
        snapshot_dir: str | Path = Path("data/dashboard"),
        port: int = DEFAULT_DASHBOARD_PORT,
        now_timestamp: Callable[[], int] | None = None,
    ) -> None:
        self.site_root = Path(site_dir).resolve()
        self.snapshot_root = Path(snapshot_dir).resolve()
        self.now_timestamp = now_timestamp or (lambda: int(time.time()))
        super().__init__((DASHBOARD_HOST, port), _DashboardRequestHandler)
