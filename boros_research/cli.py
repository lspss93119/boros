from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .benchmark import build_historical_benchmarks
from .alert_state import AlertStateStore
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
    collision_safe_target_paths,
    download_archive_file,
    load_cached_manifest,
    refresh_manifest,
)
from .crossex_client import CrossExClient, MONITORED_NOTIONALS, validate_evm_address
from .live_benchmark import HistoricalBenchmarkLookup
from .manifest import select_archive_files
from .monitor import LiveMonitor, render_cycle_summary, render_delivery_event
from .position_monitor import (
    PositionMonitor,
    render_position_cycle_summary,
)
from .position_state import PositionStateStore
from .position_telegram import render_position_delivery
from .telegram import TelegramClient, telegram_test_message
from .validation import render_report


_SUMMARY_DATASETS = (
    "market-data",
    "settlement",
    "underlying-apr / funding-rate",
    "ohlcv",
    "order-book",
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def load_project_environment(env_path: Path | None = None) -> None:
    """Load the repository-root environment without overriding the process."""
    load_dotenv(ENV_PATH if env_path is None else env_path, override=False)


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
    target_paths = collision_safe_target_paths(selected, raw_dir)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                download_archive_file,
                entry,
                raw_dir,
                fetcher,
                base_url,
                target_path=target_paths[str(entry["path"])],
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


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


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
    benchmark = subparsers.add_parser(
        "benchmark", help="calculate causal historical spread benchmarks and episodes"
    )
    benchmark.add_argument("--parquet-dir", type=Path, default=PARQUET_DIR)
    benchmark.add_argument("--duckdb-path", type=Path, default=DUCKDB_PATH)
    benchmark.add_argument(
        "--min-samples",
        type=_positive_int,
        default=50,
        help="minimum causal cohort sample count (default: 50)",
    )

    monitor = subparsers.add_parser(
        "monitor", help="read-only CrossEx live benchmark monitor"
    )
    monitor.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and print alerts without sending Telegram messages",
    )
    monitor.add_argument(
        "--once",
        action="store_true",
        help="run one polling cycle instead of the 60-second daemon",
    )
    monitor.add_argument(
        "--base-url",
        default=os.environ.get("CROSSEX_BASE_URL", "http://127.0.0.1:6688"),
        help="local CrossEx base URL (default: CROSSEX_BASE_URL or loopback:6688)",
    )
    monitor.add_argument("--token-file", type=Path, default=None)
    monitor.add_argument("--duckdb-path", type=Path, default=DUCKDB_PATH)
    monitor.add_argument(
        "--state-path", type=Path, default=Path("data/live_monitor.sqlite3")
    )
    monitor.add_argument(
        "--interval",
        type=_positive_int,
        default=60,
        help="poll interval seconds for daemon mode (default: 60)",
    )
    monitor.add_argument(
        "--min-samples",
        type=_positive_int,
        default=50,
        help="historical cohort minimum (default: 50)",
    )

    positions = subparsers.add_parser(
        "positions", help="read-only CrossEx open position monitor"
    )
    positions.add_argument(
        "--address",
        default=None,
        help="EVM strategy address (overrides BOROS_ADDRESS)",
    )
    positions.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and print position events without sending Telegram messages",
    )
    positions.add_argument(
        "--once",
        action="store_true",
        help="run one polling cycle instead of the 60-second daemon",
    )
    positions.add_argument(
        "--base-url",
        default=os.environ.get("CROSSEX_BASE_URL", "http://127.0.0.1:6688"),
        help="local CrossEx base URL (default: CROSSEX_BASE_URL or loopback:6688)",
    )
    positions.add_argument("--token-file", type=Path, default=None)
    positions.add_argument(
        "--state-path", type=Path, default=Path("data/position_monitor.sqlite3")
    )
    positions.add_argument(
        "--interval",
        type=_positive_int,
        default=60,
        help="poll interval seconds for daemon mode (default: 60)",
    )

    subparsers.add_parser(
        "telegram-test", help="send one harmless Telegram connectivity test message"
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


def _run_benchmark_command(args: argparse.Namespace) -> int:
    result = build_historical_benchmarks(
        database_path=args.duckdb_path,
        parquet_root=args.parquet_dir,
        min_samples=args.min_samples,
    )
    print(f"Benchmark rows: {result.benchmark_row_count}")
    print(f"Persistence episode rows: {result.episode_row_count}")
    print(f"Persistence summary rows: {result.summary_row_count}")
    print(f"DTE benchmark rows: {result.dte_benchmark_row_count}")
    print(f"Pair fallback rows: {result.pair_fallback_row_count}")
    print(f"Insufficient benchmark rows: {result.insufficient_benchmark_row_count}")
    fallback_rate = (
        "NULL" if result.fallback_rate is None else f"{result.fallback_rate:.6f}"
    )
    print(f"Pair fallback rate: {fallback_rate}")
    print(f"Elapsed seconds: {result.elapsed_seconds:.3f}")
    return 0


def _telegram_sender() -> TelegramClient | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return TelegramClient(token, chat_id)


def _run_monitor_command(args: argparse.Namespace) -> int:
    client = CrossExClient(base_url=args.base_url, token_file=args.token_file)
    benchmark = HistoricalBenchmarkLookup(
        args.duckdb_path,
        min_samples=args.min_samples,
    )
    # Dry runs deliberately use ephemeral state so a rehearsal cannot disarm
    # a production alert or count as six hours below a threshold.
    state = AlertStateStore(
        ":memory:" if args.dry_run else args.state_path,
        poll_interval_seconds=args.interval,
    )
    sender = _telegram_sender() if not args.dry_run else None
    try:
        monitor = LiveMonitor(
            client=client,
            benchmark=benchmark,
            state=state,
            telegram_send=None if sender is None else sender.send_message,
            monitored_notionals=MONITORED_NOTIONALS,
            poll_interval_seconds=args.interval,
        )
        if not args.dry_run and sender is None:
            print("Telegram credentials missing; opportunity alerts will not be sent.")
        if args.once:
            result = monitor.run_once(dry_run=args.dry_run)
            print(render_cycle_summary(result))
            for message in result.messages:
                print("\n" + message)
            for event in result.delivery_events:
                print(render_delivery_event(event))
            return 0
        monitor.run_forever(dry_run=args.dry_run)
        return 0
    finally:
        state.close()


def resolve_position_address(address: str | None) -> str:
    candidate = address or os.environ.get("BOROS_ADDRESS")
    if not candidate:
        raise ValueError("set BOROS_ADDRESS or pass --address for positions monitoring")
    return validate_evm_address(candidate)


def _run_positions_command(args: argparse.Namespace) -> int:
    address = resolve_position_address(args.address)
    client = CrossExClient(base_url=args.base_url, token_file=args.token_file)
    state = PositionStateStore(
        ":memory:" if args.dry_run else args.state_path,
        poll_interval_seconds=args.interval,
    )
    sender = _telegram_sender() if not args.dry_run else None
    try:
        monitor = PositionMonitor(
            client=client,
            state=state,
            address=address,
            telegram_send=None if sender is None else sender.send_message,
            poll_interval_seconds=args.interval,
        )
        if not args.dry_run and sender is None:
            print("Telegram credentials missing; position alerts will not be sent.")
        if args.once:
            result = monitor.run_once(dry_run=args.dry_run)
            print(render_position_cycle_summary(result))
            for message in result.messages:
                print("\n" + message)
            for delivery in result.delivery_events:
                print(render_position_delivery(delivery.event, delivered=delivery.delivered))
            for snapshot in result.snapshots:
                print("\n" + snapshot)
            return 0
        monitor.run_forever(dry_run=args.dry_run)
        return 0
    finally:
        state.close()


def _run_telegram_test_command() -> int:
    sender = _telegram_sender()
    if sender is None:
        print(
            "ERROR: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID locally; "
            "no credential was provided to this process.",
            file=sys.stderr,
        )
        return 1
    sender.send_message(telegram_test_message())
    print("Telegram test message sent successfully")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    load_project_environment()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "download":
            return _run_download_command(args)
        if args.command == "build":
            return _run_build_command(args)
        if args.command == "benchmark":
            return _run_benchmark_command(args)
        if args.command == "monitor":
            return _run_monitor_command(args)
        if args.command == "positions":
            return _run_positions_command(args)
        if args.command == "telegram-test":
            return _run_telegram_test_command()
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
