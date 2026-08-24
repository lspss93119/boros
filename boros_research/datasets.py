from __future__ import annotations

import json
import math
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from .market_metadata import CollateralAsset, MarketInfo
from .normalize import asset_from_symbol, normalize_venue, parse_market_slug


JsonRow = dict[str, Any]
_MARKET_DATA_PREFIXES = {"market-data", "settlement"}
_FUNDING_FIELD_BY_DATASET = {
    # Both archive layouts use the explicit official field name. Keeping the
    # mapping source-specific prevents a future fallback from guessing fields.
    "funding-rate": "annualizedFundingRate",
    "underlying-apr": "annualizedFundingRate",
}


def iter_ndjson_zip(path: Path) -> Iterator[JsonRow]:
    """Yield JSON-object rows from sorted, non-directory ZIP members."""
    zip_path = Path(path)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = sorted(
                archive.infolist(), key=lambda info: (info.filename, info.header_offset)
            )
            for info in infos:
                if info.is_dir() or info.filename.endswith("/"):
                    continue
                try:
                    member = archive.open(info)
                except (OSError, KeyError, RuntimeError) as exc:
                    raise ValueError(
                        f"{zip_path} member {info.filename!r}: cannot read ZIP member: {exc}"
                    ) from exc
                with member:
                    for line_number, raw_line in enumerate(member, start=1):
                        if not raw_line.strip():
                            continue
                        try:
                            text = raw_line.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise ValueError(
                                f"{zip_path} member {info.filename!r} line {line_number}: "
                                "invalid UTF-8"
                            ) from exc
                        try:
                            value = json.loads(text)
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                f"{zip_path} member {info.filename!r} line {line_number}: "
                                f"malformed JSON: {exc.msg}"
                            ) from exc
                        if not isinstance(value, dict):
                            raise ValueError(
                                f"{zip_path} member {info.filename!r} line {line_number}: "
                                "NDJSON row must be a JSON object"
                            )
                        yield value
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{zip_path}: invalid ZIP archive: {exc}") from exc


def _source_path(path: Path, raw_root: Path | None) -> str:
    path = Path(path)
    if raw_root is not None:
        try:
            return path.relative_to(Path(raw_root)).as_posix()
        except ValueError:
            pass

    parts = PurePosixPath(path.as_posix()).parts
    for dataset_prefix in (
        "market-data",
        "funding-rate",
        "underlying-apr",
        "settlement",
        "ohlcv",
    ):
        if dataset_prefix in parts:
            return "/".join(parts[parts.index(dataset_prefix) :])
    return path.as_posix()


def _source_parts(source_path: str) -> tuple[str, ...]:
    return PurePosixPath(source_path).parts


def _market_slug(source_path: str) -> tuple[Any, str]:
    parts = _source_parts(source_path)
    if parts[0] in _MARKET_DATA_PREFIXES:
        if len(parts) < 3:
            raise ValueError(f"{source_path}: missing market slug in archive path")
        slug = parts[1]
    elif parts[0] == "ohlcv":
        if len(parts) < 4 or parts[1] not in {"5m", "1d"}:
            raise ValueError(f"{source_path}: expected ohlcv/{{5m|1d}}/{{market}} path")
        slug = parts[2]
    else:
        raise ValueError(f"{source_path}: archive is not market-scoped")

    try:
        return parse_market_slug(slug), slug
    except ValueError as exc:
        raise ValueError(f"{source_path}: malformed market slug {slug!r}: {exc}") from exc


def _funding_identity(source_path: str) -> tuple[str, str, str]:
    parts = _source_parts(source_path)
    dataset = parts[0] if parts else ""
    if dataset not in _FUNDING_FIELD_BY_DATASET:
        raise ValueError(f"{source_path}: not a funding archive path")
    filename = parts[-1]
    suffix = ".ndjson.zip"
    if not filename.endswith(suffix):
        raise ValueError(f"{source_path}: funding archive must end with {suffix}")
    stem = filename[: -len(suffix)]
    venue_text, separator, asset_text = stem.partition("-")
    if not separator or not venue_text or not asset_text:
        raise ValueError(f"{source_path}: expected {{venue}}-{{asset}} funding filename")
    return dataset, normalize_venue(venue_text), asset_from_symbol(asset_text)


def _timestamp(value: Any, context: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{context} must be a Unix timestamp in seconds")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{context} must be an integral Unix timestamp in seconds")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{context} must be a Unix timestamp in seconds") from exc
    raise ValueError(f"{context} must be a Unix timestamp in seconds")


def _nullable_int(value: Any, context: str) -> int | None:
    if value is None:
        return None
    return _timestamp(value, context)


def _nullable_apr(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a decimal APR fraction")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must be a decimal APR fraction") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{context} must be a finite decimal APR fraction")
    return parsed


def _nullable_text(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string or null")
    return value


def normalize_market_data(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    market, _ = _market_slug(source_path)
    for record in iter_ndjson_zip(path):
        context = f"{source_path} market-data row"
        yield {
            "timestamp": _timestamp(record.get("timestamp"), f"{context}.timestamp"),
            "block_number": _nullable_int(record.get("blockNumber"), f"{context}.blockNumber"),
            "market_id": market.market_id,
            "venue": market.venue,
            "asset": market.asset,
            "maturity": market.maturity,
            "mid_apr": _nullable_apr(record.get("midApr"), f"{context}.midApr"),
            "best_bid_apr": _nullable_apr(record.get("bestBid"), f"{context}.bestBid"),
            "best_ask_apr": _nullable_apr(record.get("bestAsk"), f"{context}.bestAsk"),
            "amm_implied_apr": _nullable_apr(
                record.get("ammImpliedApr"), f"{context}.ammImpliedApr"
            ),
            "mark_apr": _nullable_apr(record.get("markApr"), f"{context}.markApr"),
            "notional_oi_collateral": _nullable_apr(
                record.get("notionalOI"), f"{context}.notionalOI"
            ),
            "last_traded_apr": _nullable_apr(
                record.get("lastTradedApr"), f"{context}.lastTradedApr"
            ),
            "latest_settlement_apr": _nullable_apr(
                record.get("latestSettlementApr"), f"{context}.latestSettlementApr"
            ),
            "source_path": source_path,
        }


def normalize_funding_rates(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    dataset, venue, asset = _funding_identity(source_path)
    funding_field = _FUNDING_FIELD_BY_DATASET[dataset]
    for record in iter_ndjson_zip(path):
        context = f"{source_path} funding row"
        yield {
            "timestamp": _timestamp(record.get("timestamp"), f"{context}.timestamp"),
            "venue": venue,
            "asset": asset,
            "annualized_funding_apr": _nullable_apr(
                record.get(funding_field), f"{context}.{funding_field}"
            ),
            "source_path": source_path,
        }


def normalize_settlements(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    market, _ = _market_slug(source_path)
    for record in iter_ndjson_zip(path):
        context = f"{source_path} settlement row"
        yield {
            "timestamp": _timestamp(record.get("timestamp"), f"{context}.timestamp"),
            "block_number": _nullable_int(record.get("blockNumber"), f"{context}.blockNumber"),
            "market_id": market.market_id,
            "venue": market.venue,
            "asset": market.asset,
            "maturity": market.maturity,
            "settlement_apr": _nullable_apr(
                record.get("settlementApr"), f"{context}.settlementApr"
            ),
            "tx_hash": _nullable_text(record.get("txHash"), f"{context}.txHash"),
            "source_path": source_path,
        }


def normalize_ohlcv(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    market, _ = _market_slug(source_path)
    for record in iter_ndjson_zip(path):
        context = f"{source_path} ohlcv row"
        yield {
            "period_start_timestamp": _timestamp(
                record.get("periodStartTimestamp"), f"{context}.periodStartTimestamp"
            ),
            "market_id": market.market_id,
            "venue": market.venue,
            "asset": market.asset,
            "maturity": market.maturity,
            "open_apr": _nullable_apr(record.get("open"), f"{context}.open"),
            "high_apr": _nullable_apr(record.get("high"), f"{context}.high"),
            "low_apr": _nullable_apr(record.get("low"), f"{context}.low"),
            "close_apr": _nullable_apr(record.get("close"), f"{context}.close"),
            "volume_collateral": _nullable_apr(
                record.get("volume"), f"{context}.volume"
            ),
            "source_path": source_path,
        }


def normalize_ohlcv_5m(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    parts = _source_parts(source_path)
    if len(parts) < 2 or parts[0:2] != ("ohlcv", "5m"):
        raise ValueError(f"{source_path}: expected an ohlcv/5m archive")
    yield from normalize_ohlcv(path, raw_root=raw_root)


def normalize_archive(path: Path, raw_root: Path | None = None) -> Iterator[JsonRow]:
    source_path = _source_path(path, raw_root)
    dataset = _source_parts(source_path)[0]
    if dataset == "market-data":
        yield from normalize_market_data(path, raw_root=raw_root)
    elif dataset in _FUNDING_FIELD_BY_DATASET:
        yield from normalize_funding_rates(path, raw_root=raw_root)
    elif dataset == "settlement":
        yield from normalize_settlements(path, raw_root=raw_root)
    elif dataset == "ohlcv":
        yield from normalize_ohlcv(path, raw_root=raw_root)
    else:
        raise ValueError(f"{source_path}: unsupported lightweight dataset")


def normalize_markets(
    markets: Mapping[int, MarketInfo] | Iterable[MarketInfo],
) -> list[JsonRow]:
    values = markets.values() if isinstance(markets, Mapping) else markets
    ordered = sorted(values, key=lambda market: market.market_id)
    return [
        {
            "market_id": market.market_id,
            "token_id": market.token_id,
            "venue": market.venue,
            "asset": market.asset,
            "maturity": market.maturity,
            "symbol": market.symbol,
            "name": market.name,
        }
        for market in ordered
    ]


def normalize_assets(
    assets: Mapping[int, CollateralAsset] | Iterable[CollateralAsset],
) -> list[JsonRow]:
    values = assets.values() if isinstance(assets, Mapping) else assets
    ordered = sorted(values, key=lambda asset: asset.token_id)
    return [
        {
            "token_id": asset.token_id,
            "symbol": asset.symbol,
            "asset_id": asset.asset_id,
            "address": asset.address,
            "name": asset.name,
            "decimals": asset.decimals,
            "usd_price": asset.usd_price,
        }
        for asset in ordered
    ]


market_catalog_rows = normalize_markets
asset_catalog_rows = normalize_assets
