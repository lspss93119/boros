from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .build import BuildPaths, run_build
from .config import (
    DUCKDB_PATH,
    HISTORICAL_BASE_URL,
    PARQUET_DIR,
    RAW_API_DIR,
    RAW_BOROS_DIR,
    RAW_INDICATORS_DIR,
)
from .download import (
    DownloadResult,
    Fetcher,
    download_archive_file,
    load_cached_manifest,
    refresh_manifest,
)
from .manifest import select_archive_files
from .validation import render_report


_SUMMARY_DATASETS = (
    "market-data",
    "settlement",
    "underlying-apr / funding-rate",
    "ohlcv",
    "order-book",
)


class DownloadBatchError(RuntimeError):
    def __init__(self, failures: list[tuple[str, Exception]]):
        self.failures = failures
        super().__init__(f"{len(failures)} archive download(s) failed")


def _dataset_label(path: str) -> str:
    if path.startswith("market-data/"):
        return "market-data"
    if path.startswith("settlement/"):
        return "settlement"
    if path.startswith(("underlying-apr/", "funding-rate/")):
        return "underlying-apr / funding-rate"
    if path.startswith("ohlcv/"):
        return "ohlcv"
    if path.startswith("order-book/"):
        return "order-book"
    return "other"


def _entry_size(entry: Mapping[str, Any]) -> int:
    size = entry.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError(f"invalid manifest size for {entry.get('path')!r}")
    return size


def render_selection_summary(selected: Sequence[Mapping[str, Any]]) -> str:
    """Render the deterministic plan summary printed before archive downloads."""
    totals: dict[str, tuple[int, int]] = {name: (0, 0) for name in _SUMMARY_DATASETS}
    other_count = 0
    other_bytes = 0
    total_bytes = 0
    for entry in selected:
        path = entry.get("path")
        if not isinstance(path, str):
            raise ValueError("selected manifest entry path must be a string")
        size = _entry_size(entry)
        total_bytes += size
        label = _dataset_label(path)
        if label in totals:
            count, current_bytes = totals[label]
            totals[label] = (count + 1, current_bytes + size)
        else:
            other_count += 1
            other_bytes += size

    lines = [
        f"Selected file count: {len(selected)}",
        f"Total compressed bytes: {total_bytes}",
        "Dataset summary:",
    ]
    lines.extend(
        f"  {name}: {totals[name][0]} files, {totals[name][1]} bytes"
        for name in _SUMMARY_DATASETS
    )
    if other_count:
        lines.append(f"  other: {other_count} files, {other_bytes} bytes")
    return "\n".join(lines)


def download_selected_files(
    selected: Sequence[Mapping[str, Any]],
    raw_dir: Path = RAW_BOROS_DIR,
    workers: int = 12,
    fetcher: Fetcher | None = None,
    base_url: str = HISTORICAL_BASE_URL,
) -> list[DownloadResult]:
    if workers <= 0:
        raise ValueError("workers must be greater than zero")

    failures: list[tuple[str, Exception]] = []
    results: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                download_archive_file,
                entry,
                raw_dir,
                fetcher,
                base_url,
            ): entry
            for entry in selected
        }
        for future in as_completed(futures):
            entry = futures[future]
            path = str(entry.get("path", "<unknown>"))
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append((path, exc))

    if failures:
        failures.sort(key=lambda item: item[0])
        raise DownloadBatchError(failures)
    return sorted(results, key=lambda result: result.path)


def _positive_workers(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("workers must be an integer") from exc
    if workers <= 0:
        raise argparse.ArgumentTypeError("workers must be greater than zero")
    return workers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boros historical research tools")
    subparsers = parser.add_subparsers(dest="command")

    download = subparsers.add_parser(
        "download", help="select and download raw Boros research archives"
    )
    download.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="refresh raw_boros/files.json from the official archive",
    )
    download.add_argument(
        "--workers",
        type=_positive_workers,
        default=12,
        help="bounded concurrent archive downloads (default: 12)",
    )
    download.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_BOROS_DIR,
        help="raw archive cache directory (default: raw_boros)",
    )

    build = subparsers.add_parser(
        "build", help="build normalized Parquet and DuckDB research outputs"
    )
    build.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="refresh raw_boros/files.json before selecting archives",
    )
    build.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="refresh raw_api/markets.json and raw_api/assets.json",
    )
    build.add_argument(
        "--workers",
        type=_positive_workers,
        default=12,
        help="bounded concurrent archive downloads (default: 12)",
    )
    build.add_argument("--raw-dir", type=Path, default=RAW_BOROS_DIR)
    build.add_argument("--raw-api-dir", type=Path, default=RAW_API_DIR)
    build.add_argument(
        "--raw-indicators-dir", type=Path, default=RAW_INDICATORS_DIR
    )
    build.add_argument("--parquet-dir", type=Path, default=PARQUET_DIR)
    build.add_argument("--duckdb-path", type=Path, default=DUCKDB_PATH)
    build.add_argument(
        "--report-path", type=Path, default=Path("data/build_report.json")
    )
    return parser


def _run_download_command(args: argparse.Namespace) -> int:
    if args.refresh_manifest:
        files = refresh_manifest(args.raw_dir)
    else:
        files = load_cached_manifest(args.raw_dir)
    selected = select_archive_files(files)
    print(render_selection_summary(selected))
    download_selected_files(
        selected,
        raw_dir=args.raw_dir,
        workers=args.workers,
    )
    print("All selected files are downloaded or already complete.")
    return 0


def _run_build_command(args: argparse.Namespace) -> int:
    paths = BuildPaths(
        raw_boros_dir=args.raw_dir,
        raw_api_dir=args.raw_api_dir,
        raw_indicators_dir=args.raw_indicators_dir,
        parquet_dir=args.parquet_dir,
        duckdb_path=args.duckdb_path,
        report_path=args.report_path,
    )
    result = run_build(
        paths,
        workers=args.workers,
        refresh_manifest=args.refresh_manifest,
        refresh_metadata=args.refresh_metadata,
    )
    print(render_report(result.report))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "download":
            return _run_download_command(args)
        if args.command == "build":
            return _run_build_command(args)
        parser.error(f"unknown command: {args.command}")
    except DownloadBatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        for path, error in exc.failures:
            print(f"  failed path: {path}: {error}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
