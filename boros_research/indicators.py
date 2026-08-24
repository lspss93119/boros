from __future__ import annotations

import csv
import io
import math
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import BOROS_OPEN_API_BASE_URL, MAX_SNAPSHOT_AGE_SEC, RAW_INDICATORS_DIR
from .market_metadata import CollateralAsset, MarketInfo


INDICATOR_CODE = "ap"
TIME_FRAME = "5m"
GRID_INTERVAL_SEC = 300
MAX_EXPORT_ROWS = 10_000
STABLE_COLLATERAL_PRICES = {"USDT": 1.0}

IndicatorRequester = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class AssetPrice:
    asset: str
    timestamp: int
    price_usd: float
    source_market_id: int | None
    source_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.asset, str) or not self.asset:
            raise ValueError("asset must be a non-empty string")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise ValueError("timestamp must be a Unix timestamp in seconds")
        if not math.isfinite(self.price_usd) or self.price_usd <= 0:
            raise ValueError("price_usd must be finite and positive")
        if self.source_market_id is not None and (
            isinstance(self.source_market_id, bool)
            or not isinstance(self.source_market_id, int)
        ):
            raise ValueError("source_market_id must be an integer or null")
        if not isinstance(self.source_path, str) or not self.source_path:
            raise ValueError("source_path must be a non-empty string")


@dataclass(frozen=True)
class PriceLookupResult:
    target_timestamp: int
    price_timestamp: int | None
    price_age_sec: int | None
    price_usd: float | None
    status: str


def _catalog_values(value: Mapping[int, Any] | Iterable[Any]) -> list[Any]:
    values = value.values() if isinstance(value, Mapping) else value
    return list(values)


def _require_timestamp(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a Unix timestamp in seconds")
    return value


def _grid_bounds(start_timestamp: int, end_timestamp: int) -> tuple[int, int] | None:
    start = _require_timestamp(start_timestamp, "start_timestamp")
    end = _require_timestamp(end_timestamp, "end_timestamp")
    if start > end:
        raise ValueError("start_timestamp must not be after end_timestamp")
    first = ((start + GRID_INTERVAL_SEC - 1) // GRID_INTERVAL_SEC) * GRID_INTERVAL_SEC
    last = (end // GRID_INTERVAL_SEC) * GRID_INTERVAL_SEC
    if first > last:
        return None
    return first, last


def select_reference_market(
    asset: CollateralAsset | str,
    markets: Mapping[int, MarketInfo] | Iterable[MarketInfo],
) -> MarketInfo:
    """Select the lowest market ID among exact underlying-asset matches."""
    asset_symbol = asset.symbol if isinstance(asset, CollateralAsset) else asset
    if not isinstance(asset_symbol, str) or not asset_symbol:
        raise ValueError("asset symbol must be a non-empty string")

    candidates = [
        market
        for market in _catalog_values(markets)
        if market.asset == asset_symbol
    ]
    if not candidates:
        raise ValueError(f"no reference market found for collateral asset {asset_symbol}")
    return min(
        candidates,
        key=lambda market: (market.market_id, market.venue, market.maturity, market.symbol),
    )


def _cache_path(
    raw_root: Path,
    asset: str,
    market_id: int,
    start_timestamp: int,
    end_timestamp: int,
    time_frame: str,
) -> Path:
    return (
        Path(raw_root)
        / "asset-price"
        / asset
        / f"market-{market_id}"
        / f"export-{INDICATOR_CODE}-{time_frame}-{start_timestamp}-{end_timestamp}.csv"
    )


def _payload_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, (bytearray, memoryview)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")

    reader = getattr(payload, "read", None)
    if not callable(reader):
        raise TypeError("indicator exporter must return CSV bytes, text, or a readable response")
    try:
        value = reader()
    finally:
        close = getattr(payload, "close", None)
        if callable(close):
            close()
    return _payload_bytes(value)


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    part = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        part.replace(path)
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def _request_export(url: str, params: Mapping[str, Any]) -> bytes:
    query = urlencode(params)
    request = Request(
        f"{url}?{query}" if query else url,
        headers={"Accept": "text/csv"},
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def _parse_csv_prices(
    payload: bytes,
    asset: str,
    source_market_id: int,
    source_path: str,
) -> list[AssetPrice]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source_path}: indicator export is not UTF-8 CSV") from exc

    reader = csv.DictReader(io.StringIO(text))
    required = {"timestamp", "asset_price_usd"}
    if not required <= set(reader.fieldnames or ()):
        raise ValueError(
            f"{source_path}: indicator export must contain timestamp and asset_price_usd"
        )

    rows: list[AssetPrice] = []
    for line_number, record in enumerate(reader, start=2):
        try:
            timestamp = int(record["timestamp"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{source_path} line {line_number}: invalid timestamp") from exc
        raw_price = record.get("asset_price_usd")
        try:
            price_usd = float(raw_price) if raw_price not in (None, "") else math.nan
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{source_path} line {line_number}: invalid price") from exc
        if not math.isfinite(price_usd) or price_usd <= 0:
            raise ValueError(f"{source_path} line {line_number}: price must be positive")
        rows.append(
            AssetPrice(
                asset=asset,
                timestamp=timestamp,
                price_usd=price_usd,
                source_market_id=source_market_id,
                source_path=source_path,
            )
        )
    return rows


def _merge_prices(prices: dict[tuple[str, int], AssetPrice], rows: Iterable[AssetPrice]) -> None:
    for row in rows:
        key = (row.asset, row.timestamp)
        previous = prices.get(key)
        if previous is not None:
            if previous.price_usd != row.price_usd:
                raise ValueError(
                    f"conflicting asset prices for {row.asset} at {row.timestamp}"
                )
            continue
        prices[key] = row


def materialize_asset_prices(
    asset_catalog: Mapping[int, CollateralAsset] | Iterable[CollateralAsset],
    market_catalog: Mapping[int, MarketInfo] | Iterable[MarketInfo],
    start_timestamp: int,
    end_timestamp: int,
    *,
    raw_root: Path = RAW_INDICATORS_DIR,
    exporter: IndicatorRequester | None = None,
    fetcher: IndicatorRequester | None = None,
    api_base_url: str = BOROS_OPEN_API_BASE_URL,
    time_frame: str = TIME_FRAME,
    max_rows_per_request: int = MAX_EXPORT_ROWS,
    request_delay_sec: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[AssetPrice, ...]:
    """Materialize stable and indicator-backed 5-minute asset price rows."""
    if time_frame != TIME_FRAME:
        raise ValueError(f"asset price materialization requires timeFrame={TIME_FRAME}")
    if (
        isinstance(max_rows_per_request, bool)
        or not isinstance(max_rows_per_request, int)
        or not 0 < max_rows_per_request <= MAX_EXPORT_ROWS
    ):
        raise ValueError(f"max_rows_per_request must be between 1 and {MAX_EXPORT_ROWS}")
    if request_delay_sec < 0 or not math.isfinite(request_delay_sec):
        raise ValueError("request_delay_sec must be finite and non-negative")
    if exporter is not None and fetcher is not None:
        raise ValueError("provide exporter or fetcher, not both")

    bounds = _grid_bounds(start_timestamp, end_timestamp)
    if bounds is None:
        return ()
    first_timestamp, last_timestamp = bounds
    requester = exporter or fetcher or _request_export
    raw_root = Path(raw_root)
    markets = _catalog_values(market_catalog)
    prices: dict[tuple[str, int], AssetPrice] = {}
    last_request_at: float | None = None

    assets = sorted(
        _catalog_values(asset_catalog),
        key=lambda asset: (asset.symbol, asset.token_id),
    )
    for asset in assets:
        if asset.symbol in STABLE_COLLATERAL_PRICES:
            for timestamp in range(first_timestamp, last_timestamp + 1, GRID_INTERVAL_SEC):
                _merge_prices(
                    prices,
                    [
                        AssetPrice(
                            asset=asset.symbol,
                            timestamp=timestamp,
                            price_usd=STABLE_COLLATERAL_PRICES[asset.symbol],
                            source_market_id=None,
                            source_path=f"synthetic/stable/{asset.symbol}",
                        )
                    ],
                )
            continue

        reference_market = select_reference_market(asset, markets)
        chunk_start = first_timestamp
        while chunk_start <= last_timestamp:
            chunk_end = min(
                last_timestamp,
                chunk_start + (max_rows_per_request - 1) * GRID_INTERVAL_SEC,
            )
            cache_path = _cache_path(
                raw_root,
                asset.symbol,
                reference_market.market_id,
                chunk_start,
                chunk_end,
                time_frame,
            )
            if cache_path.exists():
                payload = cache_path.read_bytes()
                parsed_rows = _parse_csv_prices(
                    payload,
                    asset.symbol,
                    reference_market.market_id,
                    cache_path.as_posix(),
                )
            else:
                if last_request_at is not None:
                    elapsed = time.monotonic() - last_request_at
                    if elapsed < request_delay_sec:
                        sleep(request_delay_sec - elapsed)
                params = {
                    "marketId": reference_market.market_id,
                    "timeFrame": time_frame,
                    "select": INDICATOR_CODE,
                    "startTimestamp": chunk_start,
                    "endTimestamp": chunk_end,
                }
                payload = _payload_bytes(
                    requester(f"{api_base_url.rstrip('/')}/indicators/export", params)
                )
                last_request_at = time.monotonic()
                parsed_rows = _parse_csv_prices(
                    payload,
                    asset.symbol,
                    reference_market.market_id,
                    cache_path.as_posix(),
                )
                _write_bytes_atomically(cache_path, payload)

            _merge_prices(prices, parsed_rows)
            chunk_start = chunk_end + GRID_INTERVAL_SEC

    return tuple(sorted(prices.values(), key=lambda row: (row.asset, row.timestamp)))


def price_at_or_before(
    prices: Iterable[AssetPrice],
    target_timestamp: int,
    *,
    asset: str | None = None,
    max_age_sec: int = MAX_SNAPSHOT_AGE_SEC,
) -> PriceLookupResult:
    """Return the newest non-future price and an explicit freshness status."""
    target_timestamp = _require_timestamp(target_timestamp, "target_timestamp")
    if isinstance(max_age_sec, bool) or not isinstance(max_age_sec, int) or max_age_sec < 0:
        raise ValueError("max_age_sec must be a non-negative integer")

    prior = [
        row
        for row in prices
        if row.timestamp <= target_timestamp and (asset is None or row.asset == asset)
    ]
    if not prior:
        return PriceLookupResult(target_timestamp, None, None, None, "missing")

    selected = max(prior, key=lambda row: row.timestamp)
    age = target_timestamp - selected.timestamp
    if age <= max_age_sec:
        return PriceLookupResult(
            target_timestamp,
            selected.timestamp,
            age,
            selected.price_usd,
            "ok",
        )
    return PriceLookupResult(target_timestamp, selected.timestamp, age, None, "stale")
