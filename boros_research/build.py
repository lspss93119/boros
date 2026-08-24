from __future__ import annotations

import os
import tempfile
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from .config import (
    DUCKDB_PATH,
    MAX_SNAPSHOT_AGE_SEC,
    NOTIONALS_USD,
    PARQUET_DIR,
    RAW_API_DIR,
    RAW_BOROS_DIR,
    RAW_INDICATORS_DIR,
    SAMPLE_INTERVAL_SEC,
)
from .datasets import (
    iter_ndjson_zip,
    normalize_assets as normalize_asset_rows,
    normalize_funding_rates,
    normalize_market_data,
    normalize_markets,
    normalize_ohlcv_5m,
    normalize_settlements,
)
from .download import (
    Fetcher,
    collision_safe_target_paths,
    download_archive_file,
    load_cached_manifest,
    refresh_manifest,
)
from .execution import simulate_notional_ladder
from .indicators import (
    AssetPrice,
    PriceLookupResult,
    materialize_asset_prices,
)
from .manifest import select_archive_files
from .market_metadata import (
    CollateralAsset,
    MarketInfo,
    fetch_and_cache_market_catalog,
    load_assets,
    load_market_catalog,
)
from .normalize import parse_market_slug
from .opportunities import (
    MarketExecutionObservation,
    build_executable_opportunities,
    build_market_groups,
)
from .orderbook import BookLevel, OrderBookSnapshot, parse_combined_snapshot
from .storage import build_duckdb_catalog, write_dataset
from .timeline import align_snapshots_to_grid
from .validation import BuildReport, write_report


ManifestFetcher = Callable[[str], Any]
MetadataRequester = Callable[[str, Mapping[str, Any]], Any]
IndicatorExporter = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class BuildPaths:
    raw_boros_dir: Path = RAW_BOROS_DIR
    raw_api_dir: Path = RAW_API_DIR
    raw_indicators_dir: Path = RAW_INDICATORS_DIR
    parquet_dir: Path = PARQUET_DIR
    duckdb_path: Path = DUCKDB_PATH
    report_path: Path = Path("data/build_report.json")


@dataclass(frozen=True)
class BuildResult:
    report: BuildReport
    parquet_dir: Path
    duckdb_path: Path


@dataclass
class _BuildState:
    selected: list[Mapping[str, Any]] = field(default_factory=list)
    physical_targets: dict[str, Path] = field(default_factory=dict)
    source_files_present: int = 0
    parse_failures: int = 0
    parse_failure_paths: set[str] = field(default_factory=set)
    failure_details: list[str] = field(default_factory=list)
    metadata_missing_market_ids: set[int] = field(default_factory=set)
    dataset_row_counts: dict[str, int] = field(default_factory=dict)
    stale_observations: int = 0
    missing_observations: int = 0
    unpriceable_observations: int = 0
    market_group_count: int = 0
    opportunity_row_count: int = 0
    fully_executable_rows: int = 0
    invalid_opportunity_rows: int = 0
    depth_sufficient_rate_by_notional: dict[str, float | None] = field(
        default_factory=dict
    )
    coverage_start: int | None = None
    coverage_end: int | None = None


class _PriceIndex:
    """Bisect-backed price lookup with Task 7 freshness semantics."""

    def __init__(self, rows: Iterable[AssetPrice]):
        ordered = sorted(rows, key=lambda row: (row.timestamp, row.source_path))
        self._rows = tuple(ordered)
        self._timestamps = tuple(row.timestamp for row in ordered)

    def lookup(self, target_timestamp: int) -> PriceLookupResult:
        index = bisect_right(self._timestamps, target_timestamp) - 1
        if index < 0:
            return PriceLookupResult(target_timestamp, None, None, None, "missing")
        row = self._rows[index]
        age = target_timestamp - row.timestamp
        if age <= MAX_SNAPSHOT_AGE_SEC:
            return PriceLookupResult(
                target_timestamp,
                row.timestamp,
                age,
                row.price_usd,
                "ok",
            )
        return PriceLookupResult(
            target_timestamp,
            row.timestamp,
            age,
            None,
            "stale",
        )


def _path_text(entry: Mapping[str, Any]) -> str:
    path = entry.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("selected manifest entry path must be a non-empty string")
    return path


def _target_for_entry(
    raw_root: Path,
    entry: Mapping[str, Any],
    physical_targets: Mapping[str, Path] | None = None,
) -> Path:
    path_text = _path_text(entry)
    if physical_targets is not None and path_text in physical_targets:
        return Path(physical_targets[path_text])
    relative = PurePosixPath(path_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe selected archive path: {relative}")
    return Path(raw_root).joinpath(*relative.parts)


def _entry_size(entry: Mapping[str, Any]) -> int:
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"invalid manifest size for {_path_text(entry)!r}")
    return size


def _sort_rows(rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    def key(row: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple("" if row.get(field) is None else str(row.get(field)) for field in fields)

    return [dict(row) for row in sorted(rows, key=key)]


def _is_prefix(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _download_and_validate_sources(
    state: _BuildState,
    *,
    raw_root: Path,
    workers: int,
    refresh: bool,
    manifest_fetcher: ManifestFetcher | None,
    archive_fetcher: Fetcher | None,
) -> None:
    if refresh:
        manifest = refresh_manifest(raw_root, fetcher=manifest_fetcher)
    else:
        manifest = load_cached_manifest(raw_root)

    selected = sorted(
        select_archive_files(manifest),
        key=lambda entry: _path_text(entry),
    )
    state.selected = selected
    state.physical_targets = collision_safe_target_paths(selected, raw_root)
    download_archive_files = download_selected_files
    download_archive_files(
        selected,
        raw_dir=raw_root,
        workers=workers,
        fetcher=archive_fetcher,
    )

    missing: list[str] = []
    for entry in selected:
        path = _path_text(entry)
        expected = _entry_size(entry)
        target = _target_for_entry(raw_root, entry, state.physical_targets)
        if target.is_file() and target.stat().st_size == expected:
            state.source_files_present += 1
        else:
            missing.append(path)
    if missing:
        raise ValueError(
            "selected source files are incomplete: " + ", ".join(sorted(missing))
        )


def download_selected_files(
    selected: Sequence[Mapping[str, Any]],
    *,
    raw_dir: Path,
    workers: int,
    fetcher: Fetcher | None,
) -> None:
    """Import the existing CLI downloader without making build depend on CLI."""
    from .cli import download_selected_files as download_batch

    download_batch(
        selected,
        raw_dir=raw_dir,
        workers=workers,
        fetcher=fetcher,
    )


def _load_metadata(
    paths: BuildPaths,
    *,
    refresh: bool,
    requester: MetadataRequester | None,
) -> tuple[dict[int, MarketInfo], dict[int, CollateralAsset]]:
    markets_path = Path(paths.raw_api_dir) / "markets.json"
    assets_path = Path(paths.raw_api_dir) / "assets.json"
    if refresh or not markets_path.exists() or not assets_path.exists():
        markets = fetch_and_cache_market_catalog(
            paths.raw_api_dir,
            request_json=requester,
        )
        assets = load_assets(assets_path)
        return markets, assets
    return load_market_catalog(markets_path), load_assets(assets_path)


def _ingest_lightweight(
    selected: Sequence[Mapping[str, Any]],
    raw_root: Path,
    physical_targets: Mapping[str, Path] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "market_data": [],
        "funding_rates": [],
        "settlements": [],
        "ohlcv_5m": [],
    }
    funding_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    funding_priority: dict[tuple[str, str, int], int] = {}

    for entry in sorted(selected, key=_path_text):
        source_path = _path_text(entry)
        path = _target_for_entry(raw_root, entry, physical_targets)
        if _is_prefix(source_path, "market-data/"):
            result["market_data"].extend(normalize_market_data(path, raw_root=None))
        elif _is_prefix(source_path, "funding-rate/") or _is_prefix(
            source_path, "underlying-apr/"
        ):
            priority = 0 if source_path.startswith("funding-rate/") else 1
            for row in normalize_funding_rates(path, raw_root=None):
                key = (row["venue"], row["asset"], row["timestamp"])
                previous = funding_by_key.get(key)
                if previous is None:
                    funding_by_key[key] = dict(row)
                    funding_priority[key] = priority
                    continue
                if previous["annualized_funding_apr"] != row["annualized_funding_apr"]:
                    raise ValueError(
                        "conflicting funding observations for "
                        f"{key[0]}/{key[1]} at {key[2]}"
                    )
                if priority < funding_priority[key]:
                    funding_by_key[key] = dict(row)
                    funding_priority[key] = priority
        elif _is_prefix(source_path, "settlement/"):
            result["settlements"].extend(normalize_settlements(path, raw_root=None))
        elif _is_prefix(source_path, "ohlcv/5m/"):
            result["ohlcv_5m"].extend(normalize_ohlcv_5m(path, raw_root=None))
        elif _is_prefix(source_path, "ohlcv/1d/"):
            # Validate the raw member stream but deliberately do not write daily
            # rows into the canonical ohlcv_5m dataset.
            for _ in iter_ndjson_zip(path):
                pass

    result["funding_rates"] = list(funding_by_key.values())
    result["market_data"] = _sort_rows(
        result["market_data"], ("timestamp", "market_id", "source_path")
    )
    result["funding_rates"] = _sort_rows(
        result["funding_rates"], ("timestamp", "venue", "asset", "source_path")
    )
    result["settlements"] = _sort_rows(
        result["settlements"], ("timestamp", "market_id", "source_path")
    )
    result["ohlcv_5m"] = _sort_rows(
        result["ohlcv_5m"], ("period_start_timestamp", "market_id", "source_path")
    )
    return result


def _validate_book_market(
    source_path: str,
    market_slug: Any,
    markets: Mapping[int, MarketInfo],
    missing_ids: set[int],
) -> MarketInfo:
    market = markets.get(market_slug.market_id)
    if market is None:
        missing_ids.add(market_slug.market_id)
        raise ValueError(
            f"{source_path}: metadata missing market ID {market_slug.market_id}"
        )
    mismatches = []
    for name in ("market_id", "venue", "maturity"):
        if getattr(market_slug, name) != getattr(market, name):
            mismatches.append(name)

    asset_matches = market_slug.asset == market.asset
    if not asset_matches:
        # Some official archive symbols use a venue shorthand while the
        # metadata's underlyingSymbol is canonical (for example xyzCL/CLOIL).
        # The metadata's complete symbol is the unambiguous identity bridge;
        # accept the asset mismatch only when every other symbol component is
        # exactly the same.
        try:
            metadata_slug = parse_market_slug(
                f"{market.market_id}-{market.symbol}"
            )
        except ValueError:
            metadata_slug = None
        asset_matches = metadata_slug is not None and (
            metadata_slug.venue == market_slug.venue
            and metadata_slug.symbol == market_slug.symbol
            and metadata_slug.maturity == market_slug.maturity
        )
    if not asset_matches:
        mismatches.append("asset")
    if mismatches:
        raise ValueError(
            f"{source_path}: slug/metadata mismatch for market {market_slug.market_id}: "
            + ", ".join(mismatches)
        )
    return market


def _book_rows(
    selected: Sequence[Mapping[str, Any]],
    raw_root: Path,
    markets: Mapping[int, MarketInfo],
    missing_ids: set[int],
    physical_targets: Mapping[str, Path] | None = None,
) -> list[dict[str, Any]]:
    book_entries = [
        entry
        for entry in selected
        if _path_text(entry).startswith("order-book/")
    ]
    entries_by_market: dict[int, list[tuple[str, Mapping[str, Any]]]] = defaultdict(list)
    for entry in sorted(book_entries, key=_path_text):
        source_path = _path_text(entry)
        parts = PurePosixPath(source_path).parts
        if len(parts) < 4 or parts[2] != "combined_0.0001":
            raise ValueError(f"{source_path}: expected combined_0.0001 order-book archive")
        market_slug = parse_market_slug(parts[1])
        market = _validate_book_market(source_path, market_slug, markets, missing_ids)
        entries_by_market[market.market_id].append((source_path, entry))

    rows: list[dict[str, Any]] = []
    for market_id in sorted(entries_by_market):
        market = markets[market_id]
        snapshots: dict[
            tuple[int, int | None], tuple[OrderBookSnapshot, str]
        ] = {}
        for source_path, entry in entries_by_market[market_id]:
            for raw in iter_ndjson_zip(
                _target_for_entry(raw_root, entry, physical_targets)
            ):
                snapshot = parse_combined_snapshot(raw)
                identity = (snapshot.timestamp, snapshot.block_number)
                previous = snapshots.get(identity)
                if previous is not None:
                    if previous[0] != snapshot:
                        raise ValueError(
                            f"{source_path}: conflicting duplicate order-book snapshot "
                            f"for market {market.market_id} at {identity}"
                        )
                    continue
                snapshots[identity] = (snapshot, source_path)

        values = sorted(
            snapshots.values(),
            key=lambda item: (
                item[0].timestamp,
                -1 if item[0].block_number is None else item[0].block_number,
                item[1],
            ),
        )
        if not values:
            continue
        first_timestamp = values[0][0].timestamp
        last_timestamp = values[-1][0].timestamp
        grid_start = (
            (first_timestamp + SAMPLE_INTERVAL_SEC - 1) // SAMPLE_INTERVAL_SEC
        ) * SAMPLE_INTERVAL_SEC
        grid_end = (
            (last_timestamp + SAMPLE_INTERVAL_SEC - 1) // SAMPLE_INTERVAL_SEC
        ) * SAMPLE_INTERVAL_SEC
        if grid_start > grid_end:
            continue
        aligned = align_snapshots_to_grid(
            [item[0] for item in values],
            grid_start,
            grid_end,
        )
        provenance = {
            (item[0].timestamp, item[0].block_number): item[1] for item in values
        }
        for observation in aligned:
            source = None
            if observation.snapshot_timestamp is not None:
                source = provenance.get(
                    (observation.snapshot_timestamp, observation.block_number)
                )
            rows.append(
                {
                    "grid_timestamp": observation.grid_timestamp,
                    "market_id": market.market_id,
                    "venue": market.venue,
                    "asset": market.asset,
                    "maturity": market.maturity,
                    "snapshot_timestamp": observation.snapshot_timestamp,
                    "snapshot_age_sec": observation.snapshot_age_sec,
                    "block_number": observation.block_number,
                    "status": observation.status,
                    "bids": None
                    if observation.bids is None
                    else [
                        {
                            "rate_apr": level.rate_apr,
                            "size_collateral": level.size_collateral,
                        }
                        for level in observation.bids
                    ],
                    "asks": None
                    if observation.asks is None
                    else [
                        {
                            "rate_apr": level.rate_apr,
                            "size_collateral": level.size_collateral,
                        }
                        for level in observation.asks
                    ],
                    "source_path": source,
                }
            )

    return _sort_rows(rows, ("grid_timestamp", "market_id", "source_path"))


def _materialize_prices(
    book_rows: Sequence[Mapping[str, Any]],
    markets: Mapping[int, MarketInfo],
    assets: Mapping[int, CollateralAsset],
    *,
    raw_root: Path,
    exporter: IndicatorExporter | None,
    request_delay_sec: float,
) -> list[AssetPrice]:
    ranges: dict[int, list[int]] = {}
    for row in book_rows:
        market = markets[int(row["market_id"])]
        timestamps = ranges.setdefault(market.token_id, [])
        timestamps.append(int(row["grid_timestamp"]))

    prices: list[AssetPrice] = []
    for token_id in sorted(ranges):
        asset = assets.get(token_id)
        if asset is None:
            raise ValueError(f"metadata missing collateral token ID {token_id}")
        timestamps = ranges[token_id]
        prices.extend(
            materialize_asset_prices(
                {token_id: asset},
                markets,
                min(timestamps),
                max(timestamps),
                raw_root=raw_root,
                exporter=exporter,
                request_delay_sec=request_delay_sec,
            )
        )
    return sorted(prices, key=lambda row: (row.asset, row.timestamp, row.source_path))


def _book_level_rows(value: Any) -> tuple[BookLevel, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("canonical order-book side must be a list or null")
    return tuple(
        BookLevel(
            rate_apr=float(level["rate_apr"]),
            size_collateral=float(level["size_collateral"]),
        )
        for level in value
    )


def _build_observations(
    book_rows: Sequence[Mapping[str, Any]],
    markets: Mapping[int, MarketInfo],
    assets: Mapping[int, CollateralAsset],
    prices: Sequence[AssetPrice],
) -> dict[int, tuple[MarketExecutionObservation, ...]]:
    prices_by_asset: dict[str, list[AssetPrice]] = defaultdict(list)
    for row in prices:
        prices_by_asset[row.asset].append(row)
    indexes = {
        asset: _PriceIndex(rows) for asset, rows in prices_by_asset.items()
    }
    observations: dict[int, list[MarketExecutionObservation]] = defaultdict(list)

    for row in book_rows:
        market = markets[int(row["market_id"])]
        collateral = assets[market.token_id]
        price_result = indexes.get(collateral.symbol, _PriceIndex(())).lookup(
            int(row["grid_timestamp"])
        )
        bids = _book_level_rows(row["bids"])
        asks = _book_level_rows(row["asks"])
        book_status = str(row["status"])
        top_bid = bids[0].rate_apr if book_status == "ok" and bids else None
        top_ask = asks[0].rate_apr if book_status == "ok" and asks else None
        executions = None
        if book_status == "ok" and price_result.status == "ok":
            book = OrderBookSnapshot(
                timestamp=int(row["snapshot_timestamp"]),
                block_number=row["block_number"],
                bids=bids or (),
                asks=asks or (),
            )
            executions = simulate_notional_ladder(
                book,
                float(price_result.price_usd),
                NOTIONALS_USD,
            )
        observation = MarketExecutionObservation(
            market_id=market.market_id,
            timestamp=int(row["grid_timestamp"]),
            book_status=book_status,
            snapshot_age_sec=row["snapshot_age_sec"],
            price_status=price_result.status,
            price_age_sec=price_result.price_age_sec,
            collateral_price_usd=price_result.price_usd,
            executions=executions,
            top_bid_apr=top_bid,
            top_ask_apr=top_ask,
        )
        observations[market.market_id].append(observation)
    return {
        market_id: tuple(sorted(values, key=lambda item: item.timestamp))
        for market_id, values in sorted(observations.items())
    }


def _depth_metrics(
    rows: Sequence[Mapping[str, Any]],
    notionals: Sequence[float],
) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {}
    for notional in notionals:
        denominator = 0
        numerator = 0
        for row in rows:
            if float(row["notional_usd"]) != float(notional):
                continue
            reasons = set(filter(None, str(row.get("invalid_reason") or "").split(";")))
            if any(
                reason.startswith(("short_book_", "long_book_", "short_price_", "long_price_"))
                or reason in {"short_execution_missing", "long_execution_missing", "expired"}
                for reason in reasons
            ):
                continue
            denominator += 1
            if row["fully_executable"]:
                numerator += 1
        metrics[str(int(notional) if float(notional).is_integer() else notional)] = (
            None if denominator == 0 else numerator / denominator
        )
    return metrics


def _make_report(state: _BuildState, status: str) -> BuildReport:
    return BuildReport(
        build_status=status,
        source_files_expected=len(state.selected),
        source_files_present=state.source_files_present,
        parse_failures=state.parse_failures,
        stale_observations=state.stale_observations,
        missing_observations=state.missing_observations,
        unpriceable_observations=state.unpriceable_observations,
        market_group_count=state.market_group_count,
        opportunity_row_count=state.opportunity_row_count,
        depth_sufficient_rate_by_notional=dict(state.depth_sufficient_rate_by_notional),
        coverage_start=state.coverage_start,
        coverage_end=state.coverage_end,
        dataset_row_counts=dict(state.dataset_row_counts),
        parse_failure_paths=tuple(sorted(state.parse_failure_paths)),
        failure_details=tuple(sorted(state.failure_details)),
        metadata_missing_market_ids=tuple(sorted(state.metadata_missing_market_ids)),
        fully_executable_rows=state.fully_executable_rows,
        invalid_opportunity_rows=state.invalid_opportunity_rows,
    )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        import shutil

        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _promote_derived_data(
    stage_root: Path,
    parquet_root: Path,
    duckdb_path: Path,
) -> None:
    parquet_root = Path(parquet_root)
    duckdb_path = Path(duckdb_path)
    parquet_root.parent.mkdir(parents=True, exist_ok=True)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    backup = parquet_root.with_name(f".{parquet_root.name}.previous")
    if backup.exists():
        _remove_path(backup)
    old_parquet = parquet_root.exists()
    temporary_db: Path | None = None
    try:
        if old_parquet:
            parquet_root.replace(backup)
        stage_root.replace(parquet_root)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".boros-duckdb-",
            suffix=".duckdb",
            dir=duckdb_path.parent,
        )
        os.close(fd)
        temporary_db = Path(temporary_name)
        temporary_db.unlink()
        build_duckdb_catalog(parquet_root, temporary_db)
        temporary_db.replace(duckdb_path)
        temporary_db = None
    except Exception:
        if temporary_db is not None and temporary_db.exists():
            _remove_path(temporary_db)
        if parquet_root.exists():
            _remove_path(parquet_root)
        if backup.exists():
            backup.replace(parquet_root)
        raise
    else:
        if backup.exists():
            _remove_path(backup)


def _write_failed_report(state: _BuildState, paths: BuildPaths, error: Exception) -> None:
    state.parse_failures = max(1, state.parse_failures)
    state.failure_details.append(str(error))
    try:
        write_report(_make_report(state, "failed"), paths.report_path)
    except OSError:
        pass


def run_build(
    paths: BuildPaths | None = None,
    *,
    workers: int = 12,
    refresh_manifest: bool = False,
    refresh_metadata: bool = False,
    manifest_fetcher: ManifestFetcher | None = None,
    archive_fetcher: Fetcher | None = None,
    metadata_requester: MetadataRequester | None = None,
    indicator_exporter: IndicatorExporter | None = None,
    indicator_request_delay_sec: float = 20.0,
) -> BuildResult:
    """Run the deterministic, offline-testable historical research build."""
    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        raise ValueError("workers must be greater than zero")
    paths = paths or BuildPaths()
    paths = BuildPaths(**{field: Path(getattr(paths, field)) for field in BuildPaths.__dataclass_fields__})
    state = _BuildState()
    stage_root: Path | None = None
    try:
        _download_and_validate_sources(
            state,
            raw_root=paths.raw_boros_dir,
            workers=workers,
            refresh=refresh_manifest,
            manifest_fetcher=manifest_fetcher,
            archive_fetcher=archive_fetcher,
        )
        markets, assets = _load_metadata(
            paths,
            refresh=refresh_metadata,
            requester=metadata_requester,
        )
        lightweight = _ingest_lightweight(
            state.selected,
            paths.raw_boros_dir,
            state.physical_targets,
        )
        book_rows = _book_rows(
            state.selected,
            paths.raw_boros_dir,
            markets,
            state.metadata_missing_market_ids,
            state.physical_targets,
        )
        if book_rows:
            state.coverage_start = min(int(row["grid_timestamp"]) for row in book_rows)
            state.coverage_end = max(int(row["grid_timestamp"]) for row in book_rows)
        state.stale_observations = sum(row["status"] == "stale" for row in book_rows)
        state.missing_observations = sum(row["status"] == "missing" for row in book_rows)

        prices = _materialize_prices(
            book_rows,
            markets,
            assets,
            raw_root=paths.raw_indicators_dir,
            exporter=indicator_exporter,
            request_delay_sec=indicator_request_delay_sec,
        )
        observations = _build_observations(book_rows, markets, assets, prices)
        state.unpriceable_observations = sum(
            observation.price_status != "ok"
            for values in observations.values()
            for observation in values
        )
        opportunity_rows = build_executable_opportunities(markets, observations)
        state.opportunity_row_count = len(opportunity_rows)
        state.fully_executable_rows = sum(
            bool(row["fully_executable"]) for row in opportunity_rows
        )
        state.invalid_opportunity_rows = state.opportunity_row_count - state.fully_executable_rows
        groups = build_market_groups(markets)
        state.market_group_count = sum(
            len({market.venue for market in members}) >= 2 for members in groups.values()
        )
        state.depth_sufficient_rate_by_notional = _depth_metrics(
            opportunity_rows,
            NOTIONALS_USD,
        )

        dataset_rows: dict[str, list[Mapping[str, Any]]] = {
            **lightweight,
            "order_books_5m": book_rows,
            "asset_prices": [
                {
                    "asset": row.asset,
                    "timestamp": row.timestamp,
                    "price_usd": row.price_usd,
                    "source_market_id": row.source_market_id,
                    "source_path": row.source_path,
                }
                for row in prices
            ],
            "markets": normalize_markets(markets),
            "assets": normalize_asset_rows(assets),
            "executable_opportunities": list(opportunity_rows),
        }
        state.dataset_row_counts = {
            name: len(rows) for name, rows in sorted(dataset_rows.items())
        }

        paths.parquet_dir.parent.mkdir(parents=True, exist_ok=True)
        stage_root = Path(
            tempfile.mkdtemp(prefix=".parquet-build-", dir=paths.parquet_dir.parent)
        )
        for dataset in (
            "market_data",
            "funding_rates",
            "settlements",
            "ohlcv_5m",
            "order_books_5m",
            "asset_prices",
            "markets",
            "assets",
            "executable_opportunities",
        ):
            write_dataset(dataset_rows[dataset], dataset, stage_root)

        _promote_derived_data(stage_root, paths.parquet_dir, paths.duckdb_path)
        stage_root = None
        report = _make_report(state, "ok")
        write_report(report, paths.report_path)
        return BuildResult(report, paths.parquet_dir, paths.duckdb_path)
    except Exception as exc:
        _write_failed_report(state, paths, exc)
        raise
    finally:
        if stage_root is not None and stage_root.exists():
            _remove_path(stage_root)
