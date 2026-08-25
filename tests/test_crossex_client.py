from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from boros_research.crossex_client import (
    CrossExClient,
    _default_request_json,
    normalize_opportunities_response,
)


POSITION_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


def _strategy_payload() -> dict:
    return {
        "ok": True,
        "data": {
            "strategies": [
                {
                    "strategyId": "strategy-1",
                    "base": "ETH",
                    "maturity": 1_790_000_000,
                    "legs": [],
                    "hedge": None,
                    "hedgeChecks": {
                        "borosMatchRatio": 1.0,
                        "perpMatchRatio": 1.0,
                        "borosVsPerpRatio": 1.0,
                        "fullyHedged": True,
                    },
                    "capitalUsd": 1_000.0,
                    "capitalSplit": {},
                    "realizedPnlUsd": 0.0,
                    "realizedApr": 0.0,
                    "spread": 0.1,
                    "lockedAprOnCapital": 0.1,
                    "expectedPnlToMaturityUsd": 10.0,
                    "secondsToMaturity": 100,
                    "notionalMismatchUsd": 0.0,
                    "attribution": {},
                    "warnings": [],
                }
            ]
        },
        "meta": {"asOfSec": 1_788_100_000},
    }


def _positions_payload() -> dict:
    return {
        "ok": True,
        "data": {"exposureGroups": [{"groupId": "exposure-1"}]},
        "meta": {"asOfSec": 1_788_100_001},
    }


FIXTURE = Path(__file__).parent / "fixtures" / "crossex_opportunities_sanitized.json"


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_sanitized_response_envelope_and_directed_pair_are_normalized():
    response = normalize_opportunities_response(fixture_payload(), requested_notional=10_000)

    assert response.notional_usd == 10_000
    assert response.as_of_timestamp == 1_787_500_000
    assert len(response.groups) == 1
    pair = response.groups[0].pairs[0]
    assert pair.short_leg.market_id == 190
    assert pair.short_leg.crossex_venue == "HYPERLIQUID"
    assert pair.long_leg.crossex_venue == "BYBIT"
    assert pair.exec_spread_apr == pytest.approx(0.047)
    assert pair.capital_usd == pytest.approx(6000.0)
    assert response.warnings == ("fee model uses an explicit test assumption",)


def test_three_notional_requests_use_explicit_query_and_get_only():
    payload = fixture_payload()
    calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def requester(url: str, params: dict[str, str], headers: dict[str, str]):
        calls.append((url, params, headers))
        result = copy.deepcopy(payload)
        result["data"]["meta"]["notionalUsd"] = int(params["notionalUsd"])
        return result

    client = CrossExClient(
        base_url="http://127.0.0.1:6688",
        token="test-token",
        request_json=requester,
    )
    results = [client.fetch(size) for size in (10_000, 25_000, 50_000)]

    assert [item.notional_usd for item in results] == [10_000, 25_000, 50_000]
    assert [call[1] for call in calls] == [
        {
            "notionalUsd": "10000",
            "borosEntry": "market",
            "entryMode": "both-market",
            "exitMode": "close",
        },
        {
            "notionalUsd": "25000",
            "borosEntry": "market",
            "entryMode": "both-market",
            "exitMode": "close",
        },
        {
            "notionalUsd": "50000",
            "borosEntry": "market",
            "entryMode": "both-market",
            "exitMode": "close",
        },
    ]
    assert all(call[2]["x-arb-token"] == "test-token" for call in calls)
    assert all("fresh" not in call[1] for call in calls)


def test_client_can_request_market_maker_hedge_close_explicitly():
    payload = fixture_payload()
    calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def requester(url: str, params: dict[str, str], headers: dict[str, str]):
        calls.append((url, params, headers))
        result = copy.deepcopy(payload)
        result["data"]["meta"]["notionalUsd"] = int(params["notionalUsd"])
        result["data"]["meta"]["entryMode"] = params["entryMode"]
        return result

    client = CrossExClient(
        base_url="http://127.0.0.1:6688",
        token="test-token",
        request_json=requester,
    )
    client.fetch(
        10_000,
        boros_entry="market",
        entry_mode="maker-hedge",
        exit_mode="close",
    )

    assert calls[0][1] == {
        "notionalUsd": "10000",
        "borosEntry": "market",
        "entryMode": "maker-hedge",
        "exitMode": "close",
    }


@pytest.mark.parametrize("field", ["execSpreadApr", "capitalUsd", "estProfitUsd"])
def test_nullable_economic_fields_are_preserved(field: str):
    payload = fixture_payload()
    payload["data"]["groups"][0]["pairs"][0][field] = None

    pair = normalize_opportunities_response(payload, requested_notional=10_000).groups[0].pairs[0]

    assert getattr(pair, {
        "execSpreadApr": "exec_spread_apr",
        "capitalUsd": "capital_usd",
        "estProfitUsd": "est_profit_usd",
    }[field]) is None


def test_malformed_response_fails_without_silent_empty_result():
    payload = fixture_payload()
    del payload["data"]["groups"]

    with pytest.raises(ValueError, match="groups"):
        normalize_opportunities_response(payload, requested_notional=10_000)


def test_request_failure_does_not_expose_token():
    def requester(_url, _params, _headers):
        raise OSError("connection refused")

    client = CrossExClient(
        base_url="http://127.0.0.1:6688",
        token="super-secret-token",
        request_json=requester,
    )

    with pytest.raises(RuntimeError) as exc_info:
        client.fetch(10_000)
    assert "super-secret-token" not in str(exc_info.value)


def test_default_transport_constructs_get_request(monkeypatch):
    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(fixture_payload()).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, timeout))
        return Response()

    monkeypatch.setattr("boros_research.crossex_client.urlopen", fake_urlopen)
    _default_request_json(
        "http://127.0.0.1:6688/api/opportunities",
        {"notionalUsd": "10000"},
        {},
    )

    assert seen[0][0] == "GET"
    assert "notionalUsd=10000" in seen[0][1]


def test_client_fetches_strategy_and_positions_with_get_only_routes():
    calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def requester(url: str, params: dict[str, str], headers: dict[str, str]):
        calls.append((url, params, headers))
        return _strategy_payload() if "/api/strategy/" in url else _positions_payload()

    client = CrossExClient(
        base_url="http://127.0.0.1:6688",
        token="test-token",
        request_json=requester,
    )

    strategies = client.fetch_strategy(POSITION_ADDRESS)
    positions = client.fetch_positions()

    assert strategies[0].strategy_id == "strategy-1"
    assert positions.exposure_groups[0]["groupId"] == "exposure-1"
    assert calls == [
        (
            f"http://127.0.0.1:6688/api/strategy/{POSITION_ADDRESS}",
            {},
            {"x-arb-token": "test-token"},
        ),
        (
            "http://127.0.0.1:6688/api/positions",
            {},
            {"x-arb-token": "test-token"},
        ),
    ]


@pytest.mark.parametrize(
    "address",
    [
        "https://evil.example/",
        "0x1234",
        "0X1234567890abcdef1234567890abcdef12345678",
        "0x1234567890abcdef1234567890abcdef1234567!",
    ],
)
def test_strategy_address_must_be_a_safe_evm_path_segment(address: str):
    client = CrossExClient(request_json=lambda *_args: _strategy_payload())

    with pytest.raises(ValueError, match="EVM address"):
        client.fetch_strategy(address)
