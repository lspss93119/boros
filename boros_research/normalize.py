from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class MarketSlug:
    market_id: int
    venue: str
    symbol: str
    asset: str
    maturity: date
    slug: str


_VENUE_ALIASES = {
    "BINANCE": "BINANCE",
    "BYBIT": "BYBIT",
    "GATE": "GATE",
    "GATEIO": "GATE",
    "HYPERLIQUID": "HYPERLIQUID",
    "KRAKEN": "KRAKEN",
    "LIGHTER": "LIGHTER",
    "OKEX": "OKX",
    "OKX": "OKX",
}
_MARKET_SLUG_RE = re.compile(
    r"^(?P<market_id>[1-9][0-9]*)-"
    r"(?P<venue>[^-]+)-"
    r"(?P<symbol>.+)-"
    r"(?P<maturity>[0-9]{1,2}[A-Za-z]{3}[0-9]{4})$"
)
_SYMBOL_QUOTES = ("USDT0", "USDCE", "USDE", "USDT", "USDC", "USD")
_XYZ_ASSET_ALIASES = {"GOLD": "XAU"}


def _compact_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.strip().upper())


def normalize_venue(venue: str) -> str:
    """Return the canonical uppercase venue name without restricting its universe."""
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError("venue must be a non-empty string")

    compact = _compact_name(venue)
    if not compact:
        raise ValueError("venue must contain alphanumeric characters")
    return _VENUE_ALIASES.get(compact, compact)


def asset_from_symbol(symbol: str) -> str:
    """Extract a canonical underlying asset from a Boros market symbol."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")

    normalized = _compact_name(symbol)
    if not normalized:
        raise ValueError("symbol must contain alphanumeric characters")

    # XYZ100 is an official canonical underlying symbol.  Other ``xyz...``
    # spellings in market symbols use ``xyz`` as a venue-specific wrapper.
    if normalized == "XYZ100":
        return normalized
    if normalized.startswith("XYZ") and len(normalized) > 3:
        asset = normalized[3:]
        return _XYZ_ASSET_ALIASES.get(asset, asset)

    for quote in _SYMBOL_QUOTES:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)]
    return normalized


def parse_market_slug(slug: str) -> MarketSlug:
    """Parse a Boros market slug or raise ``ValueError`` when it is malformed."""
    if not isinstance(slug, str):
        raise ValueError("market slug must be a string")

    match = _MARKET_SLUG_RE.fullmatch(slug)
    if match is None:
        raise ValueError(f"malformed market slug: {slug!r}")

    maturity_text = match.group("maturity").upper()
    try:
        maturity = datetime.strptime(maturity_text, "%d%b%Y").date()
    except ValueError as exc:
        raise ValueError(f"malformed market slug maturity: {slug!r}") from exc

    symbol = match.group("symbol")
    return MarketSlug(
        market_id=int(match.group("market_id")),
        venue=normalize_venue(match.group("venue")),
        symbol=symbol,
        asset=asset_from_symbol(symbol),
        maturity=maturity,
        slug=slug,
    )
