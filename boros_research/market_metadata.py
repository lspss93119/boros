from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import BOROS_OPEN_API_BASE_URL, RAW_API_DIR
from .normalize import asset_from_symbol, normalize_venue


JsonObject = Mapping[str, Any]
RequestJson = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class MarketInfo:
    market_id: int
    token_id: int
    venue: str
    asset: str
    maturity: date
    symbol: str
    name: str


@dataclass(frozen=True)
class CollateralAsset:
    token_id: int
    symbol: str
    asset_id: str
    address: str
    name: str
    decimals: int
    usd_price: Decimal | None


def _require_mapping(value: Any, context: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _require_results(value: Any, context: str) -> list[JsonObject]:
    payload = _require_mapping(value, context)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{context}.results must be a list")
    if not results:
        raise ValueError(f"{context}.results must not be empty")
    if not all(isinstance(item, Mapping) for item in results):
        raise ValueError(f"{context}.results must contain objects")
    return results


def _market_pages(value: Any) -> list[JsonObject]:
    if isinstance(value, Mapping) and "pages" in value:
        pages = value["pages"]
        if not isinstance(pages, list) or not pages:
            raise ValueError("markets.pages must be a non-empty list")
        return [_require_mapping(page, "markets page") for page in pages]

    if isinstance(value, Mapping):
        _require_results(value, "markets response")
        return [value]

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("markets response must not be empty")
        if all(isinstance(item, Mapping) and "results" in item for item in value):
            return [_require_mapping(page, "markets page") for page in value]
        if all(isinstance(item, Mapping) and "marketId" in item for item in value):
            return [{"results": list(value)}]

    raise ValueError("unknown markets response shape")


def _market_info(raw: JsonObject) -> MarketInfo:
    market_id = raw.get("marketId")
    token_id = raw.get("tokenId")
    if not isinstance(market_id, int) or isinstance(market_id, bool) or market_id <= 0:
        raise ValueError("market.marketId must be a positive integer")
    if not isinstance(token_id, int) or isinstance(token_id, bool) or token_id <= 0:
        raise ValueError("market.tokenId must be a positive integer")

    metadata = _require_mapping(raw.get("metadata"), "market.metadata")
    im_data = _require_mapping(raw.get("imData"), "market.imData")

    platform = raw.get("platform")
    if platform is not None:
        platform = _require_mapping(platform, "market.platform")

    underlying = raw.get("underlyingSymbol")
    if not isinstance(underlying, str) or not underlying.strip():
        underlying = metadata.get("underlyingSymbol")
    if not isinstance(underlying, str) or not underlying.strip():
        underlying = metadata.get("assetSymbol")
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError(f"market {market_id} is missing underlyingSymbol")

    venue = None
    if platform is not None:
        venue = platform.get("name") or platform.get("platformId")
    if not isinstance(venue, str) or not venue.strip():
        venue = metadata.get("platformName")
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError(f"market {market_id} is missing platform name")

    maturity_raw = im_data.get("maturity")
    if isinstance(maturity_raw, bool):
        raise ValueError(f"market {market_id}.imData.maturity must be a timestamp")
    try:
        maturity_timestamp = float(maturity_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"market {market_id}.imData.maturity must be a timestamp") from exc
    if not math.isfinite(maturity_timestamp) or maturity_timestamp <= 0:
        raise ValueError(f"market {market_id}.imData.maturity must be a positive timestamp")
    maturity = datetime.fromtimestamp(maturity_timestamp, tz=timezone.utc).date()

    symbol = im_data.get("symbol") or raw.get("symbol")
    name = im_data.get("name") or raw.get("name")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"market {market_id} is missing symbol")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"market {market_id} is missing name")

    return MarketInfo(
        market_id=market_id,
        token_id=token_id,
        venue=normalize_venue(venue),
        asset=asset_from_symbol(underlying),
        maturity=maturity,
        symbol=symbol,
        name=name,
    )


def normalize_market_catalog(value: Any) -> dict[int, MarketInfo]:
    """Normalize one or more official market response pages by market ID."""
    catalog: dict[int, MarketInfo] = {}
    for page in _market_pages(value):
        for raw in _require_results(page, "markets page"):
            market = _market_info(raw)
            if market.market_id in catalog:
                raise ValueError(f"duplicate marketId: {market.market_id}")
            catalog[market.market_id] = market
    if not catalog:
        raise ValueError("market catalog must not be empty")
    return catalog


def _asset_rows(value: Any) -> list[JsonObject]:
    if isinstance(value, Mapping):
        results = value.get("results")
        if results is None:
            results = value.get("assets")
        if not isinstance(results, list):
            raise ValueError("assets response must contain a results list")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        results = value
    else:
        raise ValueError("unknown assets response shape")

    if not results or not all(isinstance(item, Mapping) for item in results):
        raise ValueError("assets response must contain non-empty object results")
    return results


def _parse_decimal(value: Any, context: str) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{context} must be a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{context} must be finite")
    return parsed


def normalize_assets(value: Any) -> dict[int, CollateralAsset]:
    """Normalize collateral assets, keyed by their positive Boros token ID."""
    assets: dict[int, CollateralAsset] = {}
    for raw in _asset_rows(value):
        token_id = raw.get("tokenId")
        if not isinstance(token_id, int) or isinstance(token_id, bool):
            raise ValueError("asset.tokenId must be an integer")

        is_collateral = raw.get("isCollateral")
        if not isinstance(is_collateral, bool):
            raise ValueError("asset.isCollateral must be boolean")
        if not is_collateral:
            continue
        if token_id <= 0:
            raise ValueError("collateral asset.tokenId must be positive")
        if token_id in assets:
            raise ValueError(f"duplicate collateral tokenId: {token_id}")

        metadata = raw.get("metadata", {})
        metadata = _require_mapping(metadata, "asset.metadata")
        symbol = metadata.get("proSymbol") or raw.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"collateral asset {token_id} is missing symbol")

        asset_id = raw.get("id")
        address = raw.get("address")
        name = raw.get("name")
        decimals = raw.get("decimals")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError(f"collateral asset {token_id} is missing id")
        if not isinstance(address, str) or not address.strip():
            raise ValueError(f"collateral asset {token_id} is missing address")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"collateral asset {token_id} is missing name")
        if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0:
            raise ValueError(f"collateral asset {token_id}.decimals must be a non-negative integer")

        assets[token_id] = CollateralAsset(
            token_id=token_id,
            symbol=asset_from_symbol(symbol),
            asset_id=asset_id,
            address=address,
            name=name,
            decimals=decimals,
            usd_price=_parse_decimal(raw.get("usdPrice"), f"asset {token_id}.usdPrice"),
        )

    if not assets:
        raise ValueError("collateral asset catalog must not be empty")
    return assets


def _request_json(url: str, params: Mapping[str, Any]) -> Any:
    query = urlencode(params)
    request_url = f"{url}?{query}" if query else url
    request = Request(request_url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _next_market_params(
    page: JsonObject,
    results_count: int,
    limit: int,
    seen_resume_tokens: set[str],
) -> dict[str, Any] | None:
    if "cursor" in page:
        cursor = _require_mapping(page.get("cursor"), "markets.cursor")
        has_more = cursor.get("hasMore")
        if not isinstance(has_more, bool):
            raise ValueError("markets.cursor.hasMore must be boolean")
        if not has_more:
            return None
        next_cursor = cursor.get("next")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise ValueError("markets.cursor.next is required when hasMore is true")
        return {"limit": limit, "cursor": next_cursor}

    if "resumeToken" in page:
        resume_token = page.get("resumeToken")
        if resume_token in (None, "") or results_count == 0:
            return None
        if not isinstance(resume_token, str):
            raise ValueError("markets.resumeToken must be a string")
        if resume_token in seen_resume_tokens:
            raise ValueError("markets.resumeToken did not advance")
        seen_resume_tokens.add(resume_token)
        return {"limit": limit, "resumeToken": resume_token}

    if "total" in page or "skip" in page:
        total = page.get("total")
        skip = page.get("skip")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or not isinstance(skip, int)
            or isinstance(skip, bool)
            or total < 0
            or skip < 0
        ):
            raise ValueError("legacy markets pagination requires integer total and skip")
        next_skip = skip + results_count
        if next_skip < total:
            if results_count == 0:
                raise ValueError("legacy markets pagination made no progress")
            return {"limit": limit, "skip": next_skip}
        return None

    raise ValueError("markets response missing supported pagination envelope")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_and_cache_market_catalog(
    raw_api_dir: Path = RAW_API_DIR,
    request_json: RequestJson | None = None,
    limit: int = 100,
    api_base_url: str = BOROS_OPEN_API_BASE_URL,
) -> dict[int, MarketInfo]:
    """Fetch, validate, and cache raw market pages plus the raw asset response."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    requester = request_json or _request_json
    base_url = api_base_url.rstrip("/")
    market_pages: list[JsonObject] = []
    params: dict[str, Any] = {"limit": limit}
    seen_resume_tokens: set[str] = set()

    while True:
        response = requester(f"{base_url}/markets", params)
        page = _require_mapping(response, "markets response")
        results = _require_results(page, "markets response")
        market_pages.append(page)
        next_params = _next_market_params(
            page,
            len(results),
            limit,
            seen_resume_tokens,
        )
        if next_params is None:
            break
        params = next_params

    assets_response = requester(f"{base_url}/assets", {})
    markets = normalize_market_catalog(market_pages)
    normalize_assets(assets_response)

    raw_api_dir = Path(raw_api_dir)
    raw_api_dir.mkdir(parents=True, exist_ok=True)
    _write_json(raw_api_dir / "markets.json", market_pages)
    _write_json(raw_api_dir / "assets.json", assets_response)
    return markets


def load_market_catalog(path: Path = RAW_API_DIR / "markets.json") -> dict[int, MarketInfo]:
    return normalize_market_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def load_assets(path: Path = RAW_API_DIR / "assets.json") -> dict[int, CollateralAsset]:
    return normalize_assets(json.loads(Path(path).read_text(encoding="utf-8")))
