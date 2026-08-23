from datetime import date

import pytest

from boros_research.normalize import asset_from_symbol, normalize_venue, parse_market_slug


def test_parse_market_slug():
    market = parse_market_slug("155-HYPERLIQUID-HYPEUSDT-25SEP2026")

    assert market.market_id == 155
    assert market.venue == "HYPERLIQUID"
    assert market.asset == "HYPE"
    assert market.maturity == date(2026, 9, 25)


def test_aliases_are_canonical():
    assert normalize_venue("Hyperliquid") == "HYPERLIQUID"
    assert asset_from_symbol("xyzGOLD") == "XAU"
    assert asset_from_symbol("ETHUSDT") == "ETH"
    assert asset_from_symbol("BTCUSDC.E") == "BTC"


@pytest.mark.parametrize(
    "slug",
    [
        "not-a-market-slug",
        "155-HYPERLIQUID-HYPEUSDT-31FEB2026",
        "0-HYPERLIQUID-HYPEUSDT-25SEP2026",
    ],
)
def test_malformed_market_slug_raises_value_error(slug):
    with pytest.raises(ValueError):
        parse_market_slug(slug)
