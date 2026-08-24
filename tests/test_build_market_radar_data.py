from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from boros_research.crossex_client import normalize_opportunities_response
from boros_research.radar_economics import ProxyEconomics


FIXTURE = Path(__file__).parent / "fixtures" / "crossex_opportunities_sanitized.json"
GENERATED_AT = "2026-08-24T00:00:00Z"


class FakeCrossExClient:
    """A deterministic read-only CrossEx boundary for builder tests."""

    def __init__(self) -> None:
        self.fetch_calls: list[int] = []

    def fetch(self, notional_usd: int):
        self.fetch_calls.append(notional_usd)
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["data"]["meta"]["notionalUsd"] = notional_usd
        payload["data"]["groups"][0]["warnings"] = ["group quote is delayed"]
        return normalize_opportunities_response(
            payload, requested_notional=notional_usd
        )


class FailingCrossExClient:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception

    def fetch(self, notional_usd: int):
        raise self.exception


def _create_fixture_database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE markets (
            market_id BIGINT,
            token_id BIGINT,
            venue VARCHAR,
            asset VARCHAR,
            maturity DATE,
            symbol VARCHAR,
            name VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE executable_opportunities (
            timestamp BIGINT,
            asset VARCHAR,
            maturity DATE,
            dte_days INTEGER,
            token_id BIGINT,
            short_market_id BIGINT,
            short_venue VARCHAR,
            long_market_id BIGINT,
            long_venue VARCHAR,
            notional_usd DOUBLE,
            short_bid_vwap_apr DOUBLE,
            long_ask_vwap_apr DOUBLE,
            executable_spread_apr DOUBLE,
            fully_executable BOOLEAN,
            invalid_reason VARCHAR
        )
        """
    )
    maturity = date(2026, 9, 25)
    connection.executemany(
        "INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 3, "HYPERLIQUID", "HYPE", maturity, "HYPE-HL", "HYPE HL"),
            (2, 3, "BYBIT", "HYPE", maturity, "HYPE-BYBIT", "HYPE Bybit"),
        ],
    )
    timestamp = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())
    connection.executemany(
        "INSERT INTO executable_opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                timestamp,
                "HYPE",
                maturity,
                32,
                3,
                1,
                "HYPERLIQUID",
                2,
                "BYBIT",
                notional,
                0.10,
                0.01,
                0.09,
                True,
                None,
            )
            for notional in (10_000, 25_000, 50_000)
        ],
    )
    connection.close()


def _contains_time_series_array(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in {"daily", "history", "historical", "timeseries"}
            and isinstance(item, list)
            or _contains_time_series_array(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_time_series_array(item) for item in value)
    return False


def test_collect_proxies_fetches_each_notional_once_and_preserves_proxy_diagnostics():
    """Catches an extra/fresh proxy request or discarded current warning/venue evidence."""
    from build_market_radar_data import collect_proxies

    fake_client = FakeCrossExClient()

    proxies, diagnostics = collect_proxies(fake_client)

    assert fake_client.fetch_calls == [10_000, 25_000, 50_000]
    assert proxies == {
        10_000: {"HYPE": ProxyEconomics(5.7, 6000.0, 1_787_500_000, 1)},
        25_000: {"HYPE": ProxyEconomics(5.7, 6000.0, 1_787_500_000, 1)},
        50_000: {"HYPE": ProxyEconomics(5.7, 6000.0, 1_787_500_000, 1)},
    }
    assert diagnostics["currentVenues"] == ["BYBIT", "HYPERLIQUID"]
    assert diagnostics["warnings"] == [
        {
            "notionalUsd": notional,
            "scope": "response",
            "text": "fee model uses an explicit test assumption",
        }
        for notional in (10_000, 25_000, 50_000)
    ] + [
        {
            "notionalUsd": notional,
            "scope": "group",
            "tokenId": 3,
            "text": "group quote is delayed",
        }
        for notional in (10_000, 25_000, 50_000)
    ]


def test_builder_opens_duckdb_read_only_and_writes_small_deterministic_finite_payload(
    tmp_path, monkeypatch
):
    """Catches a writable database open or a non-compact/non-deterministic Radar export."""
    import build_market_radar_data as builder

    database_path = tmp_path / "boros.duckdb"
    output_path = tmp_path / "boros_market_radar.json"
    _create_fixture_database(database_path)
    original_connect = builder.duckdb.connect
    calls: list[tuple[str, bool | None]] = []

    def connect(path: str, **kwargs):
        calls.append((path, kwargs.get("read_only")))
        return original_connect(path, **kwargs)

    monkeypatch.setattr(builder.duckdb, "connect", connect)
    fake_client = FakeCrossExClient()

    payload = builder.build_payload(
        database_path, fake_client, generated_at=GENERATED_AT
    )
    encoded_size = builder.write_payload(output_path, payload)
    encoded = output_path.read_text(encoding="utf-8")

    assert calls == [(str(database_path), True)]
    assert fake_client.fetch_calls == [10_000, 25_000, 50_000]
    assert payload["schemaVersion"] == 1
    assert payload["generatedAt"] == GENERATED_AT
    assert payload["notionals"] == [10_000, 25_000, 50_000]
    assert payload["viability"]["proxyModel"]
    assert payload["proxyDiagnostics"]["currentVenues"] == ["BYBIT", "HYPERLIQUID"]
    assert payload["rows"][0]["proxy"]["asOfTimestamp"] == 1_787_500_000
    assert "build_arbitrage_site_data" not in sys.modules
    assert not _contains_time_series_array(payload)
    assert encoded_size == len(encoded.encode("utf-8"))
    assert encoded_size < 1_048_576
    assert encoded == json.dumps(
        payload, separators=(",", ":"), sort_keys=True, allow_nan=False
    )
    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert "super-secret-token" not in encoded


def test_write_payload_rejects_a_payload_at_or_above_one_mib(tmp_path):
    """Catches writing a payload that exceeds the static site's compact-data limit."""
    import build_market_radar_data as builder

    output_path = tmp_path / "oversized.json"

    with pytest.raises(RuntimeError, match="exceeds 1 MiB"):
        builder.write_payload(
            output_path,
            {"payload": "x" * builder.MAX_PAYLOAD_BYTES},
        )

    assert not output_path.exists()


@pytest.mark.parametrize(
    "exception", [RuntimeError("offline"), ValueError("malformed")]
)
def test_cli_fails_clearly_when_crossex_is_unavailable_or_invalid(
    tmp_path, monkeypatch, capsys, exception
):
    """Catches silently replacing unavailable or invalid current proxy economics."""
    import build_market_radar_data as builder

    monkeypatch.setattr(
        builder,
        "CrossExClient",
        lambda base_url: FailingCrossExClient(exception),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_market_radar_data.py",
            "--database",
            str(tmp_path / "unused.duckdb"),
            "--output",
            str(tmp_path / "unused.json"),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        builder.main()

    assert raised.value.code == 1
    assert capsys.readouterr().err == (
        "Market Radar proxy unavailable: start the local CrossEx terminal/API and retry.\n"
    )
