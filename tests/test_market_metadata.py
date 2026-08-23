import json
from pathlib import Path

import pytest

from boros_research.market_metadata import (
    fetch_and_cache_market_catalog,
    load_assets,
    load_market_catalog,
    normalize_assets,
    normalize_market_catalog,
    venue_from_market_symbol,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_market_catalog_keeps_collateral_identity_and_prefers_underlying_symbol():
    markets = normalize_market_catalog(load_fixture("markets.json"))

    market = markets[155]
    assert market.market_id == 155
    assert market.token_id == 3
    assert market.venue == "HYPERLIQUID"
    assert market.asset == "HYPE"
    assert market.maturity.isoformat() == "2026-09-18"
    assert markets[156].asset == "HYPE"


def test_market_without_platform_fields_uses_imdata_symbol_for_venue():
    raw = load_fixture("markets.json")["pages"][0]["results"][0]

    assert "platform" not in raw
    assert "platformName" not in raw["metadata"]
    assert normalize_market_catalog({"results": [raw]})[155].venue == "HYPERLIQUID"


def test_venue_from_market_symbol_rejects_missing_prefix():
    with pytest.raises(ValueError, match="no venue prefix"):
        venue_from_market_symbol("HYPEUSDT")


def test_usdt_collateral_without_pro_symbol_uses_canonical_asset_id():
    asset = {
        "id": "USDT",
        "address": "...",
        "tokenId": 3,
        "name": "USD₮0",
        "symbol": "USD₮0",
        "decimals": 6,
        "usdPrice": "1.0",
        "isCollateral": True,
    }

    assert normalize_assets({"results": [asset]})[3].symbol == "USDT"


def test_assets_map_token_id_to_canonical_symbol_and_exclude_non_collateral():
    assets = normalize_assets(load_fixture("assets.json"))

    assert assets[3].symbol == "USDT"
    assert assets[5].symbol == "HYPE"
    assert -1 not in assets


def test_fetch_caches_raw_pages_and_follows_cursor_pagination(tmp_path):
    market_pages = load_fixture("markets.json")["pages"]
    assets_payload = load_fixture("assets.json")
    market_calls = []

    def fake_request_json(url, params):
        if url.endswith("/markets"):
            market_calls.append(dict(params))
            return market_pages[len(market_calls) - 1]
        if url.endswith("/assets"):
            return assets_payload
        raise AssertionError(f"unexpected URL: {url}")

    catalog = fetch_and_cache_market_catalog(
        tmp_path,
        request_json=fake_request_json,
        limit=1,
    )

    assert set(catalog) == {155, 156}
    assert market_calls == [
        {"limit": 1},
        {"limit": 1, "cursor": "page-2"},
    ]
    assert load_market_catalog(tmp_path / "markets.json") == catalog
    assert load_assets(tmp_path / "assets.json")[3].symbol == "USDT"
    assert json.loads((tmp_path / "markets.json").read_text()) == market_pages
    assert json.loads((tmp_path / "assets.json").read_text()) == assets_payload


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": {}},
        {"results": []},
    ],
)
def test_unknown_or_empty_market_response_raises(payload):
    with pytest.raises(ValueError):
        normalize_market_catalog(payload)


def test_unknown_asset_response_raises():
    with pytest.raises(ValueError):
        normalize_assets({"assets": []})
