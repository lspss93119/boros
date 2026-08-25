from __future__ import annotations

import http.client
import json
import threading

import pytest

from boros_research.dashboard_snapshot import write_snapshot


def snapshot(kind: str) -> dict:
    return {
        "schemaVersion": 1,
        "kind": kind,
        "cycleTimestamp": 1_000,
        "pollIntervalSeconds": 60,
        "sourceStatus": "ok",
        "lastGoodTimestamp": 1_000,
        "data": (
            {"counts": {}, "currentOpportunities": []}
            if kind == "p1"
            else {"strategies": [], "positions": None}
        ),
        "diagnostics": {},
    }


@pytest.fixture
def running_server(tmp_path):
    from boros_research.dashboard_server import DashboardHTTPServer

    site = tmp_path / "site"
    (site / "dashboard").mkdir(parents=True)
    (site / "data").mkdir()
    (site / "dashboard" / "index.html").write_text("<main>dashboard</main>")
    (site / "data" / "boros_market_radar.json").write_text('{"schemaVersion":1}')
    snapshots = tmp_path / "snapshots"
    write_snapshot(snapshots / "p1_latest.json", snapshot("p1"))
    write_snapshot(snapshots / "p2_latest.json", snapshot("p2"))

    server = DashboardHTTPServer(
        site_dir=site,
        snapshot_dir=snapshots,
        port=0,
        now_timestamp=lambda: 1_000,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, site, snapshots
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def request(server, path: str, *, method: str = "GET", headers: dict[str, str] | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    return response.status, response_headers, body


def test_freshness_has_exact_inclusive_boundaries():
    from boros_research.dashboard_server import freshness

    assert freshness(150, 60) == "fresh"
    assert freshness(151, 60) == "stale"
    assert freshness(600, 60) == "stale"
    assert freshness(601, 60) == "offline"
    assert freshness(None, 60) == "offline"


def test_dashboard_api_envelopes_and_static_files(running_server):
    server, _site, _snapshots = running_server

    status, headers, body = request(server, "/api/dashboard/health")
    health = json.loads(body)
    assert status == 200
    assert health["ok"] is True
    assert health["data"]["components"]["p1"]["freshness"] == "fresh"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"

    status, _headers, body = request(server, "/api/dashboard/opportunities")
    opportunities = json.loads(body)
    assert status == 200
    assert opportunities["ok"] is True
    assert opportunities["data"]["currentOpportunities"] == []
    assert opportunities["data"]["freshness"] == "fresh"

    status, headers, body = request(server, "/")
    assert status == 302
    assert headers["Location"] == "/dashboard/"
    assert body == b""

    status, headers, body = request(server, "/dashboard/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert body == b"<main>dashboard</main>"

    status, _headers, body = request(server, "/data/boros_market_radar.json")
    assert status == 200
    assert json.loads(body)["schemaVersion"] == 1


def test_missing_or_malformed_live_snapshots_fail_closed_but_health_reports_invalid(
    running_server,
):
    server, _site, snapshots = running_server
    (snapshots / "p1_latest.json").unlink()

    status, _headers, body = request(server, "/api/dashboard/opportunities")
    assert status == 503
    assert json.loads(body)["ok"] is False
    assert "snapshots" not in body.decode().lower()

    (snapshots / "p1_latest.json").write_text("{not-json")
    status, _headers, body = request(server, "/api/dashboard/opportunities")
    assert status == 503
    assert json.loads(body)["ok"] is False

    status, _headers, body = request(server, "/api/dashboard/health")
    health = json.loads(body)
    assert status == 200
    assert health["data"]["components"]["p1"]["status"] == "invalid"


def test_incompatible_snapshot_data_fails_closed(running_server):
    server, _site, snapshots = running_server
    invalid = snapshot("p1")
    invalid["data"] = {"notCurrentOpportunities": []}
    write_snapshot(snapshots / "p1_latest.json", invalid)

    status, _headers, body = request(server, "/api/dashboard/opportunities")
    assert status == 503
    assert json.loads(body)["ok"] is False

    status, _headers, body = request(server, "/api/dashboard/health")
    assert status == 200
    assert json.loads(body)["data"]["components"]["p1"]["status"] == "invalid"


def test_host_origin_and_path_traversal_are_rejected(running_server):
    server, site, _snapshots = running_server
    (site.parent / "secret.txt").write_text("secret")

    status, _headers, _body = request(
        server, "/api/dashboard/health", headers={"Host": "evil.example"}
    )
    assert status == 403

    status, _headers, _body = request(
        server,
        "/api/dashboard/health",
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403

    status, _headers, _body = request(server, "/%2e%2e/secret.txt")
    assert status in {403, 404}

    status, _headers, _body = request(server, "/%2Fetc%2Fpasswd")
    assert status in {403, 404}


def test_mutation_methods_are_not_supported(running_server):
    server, _site, _snapshots = running_server
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        status, headers, _body = request(
            server, "/api/dashboard/health", method=method
        )
        assert status == 405
        assert headers["Allow"] == "GET"
