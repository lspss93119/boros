#!/usr/bin/env python3
"""Download, clean, and visualize Pendle Boros APR data.

The source publishes zipped NDJSON files. This script keeps raw ZIPs intact,
creates tidy CSVs, and builds a standalone HTML dashboard focused on implied
APR versus realized exchange funding APR.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import html
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


BASE_URL = "https://historical-data.boros.finance"
USER_AGENT = "Mozilla/5.0 boros-apr-pipeline/1.0"
DEFAULT_DATASETS = ("market-data", "settlement", "underlying-apr", "ohlcv/1d")
ALL_NON_ORDERBOOK_DATASETS = (
    "market-data",
    "settlement",
    "underlying-apr",
    "ohlcv",
    "market-trades",
)

MARKET_RE = re.compile(
    r"^(?P<market_id>\d+)-(?P<venue>[^-]+)-(?P<symbol>.+)-(?P<maturity>\d{2}[A-Z]{3}\d{4})$"
)

EXCHANGE_NAME = {
    "BINANCE": "Binance",
    "HYPERLIQUID": "Hyperliquid",
    "OKX": "OKX",
    "BYBIT": "Bybit",
    "GATE": "Gate",
    "KUCOIN": "KuCoin",
    "LIGHTER": "Lighter",
}

ASSET_ALIASES = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "HYPEUSDT": "HYPE",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "SOLUSDT": "SOL",
    "CLUSDT": "CLOIL",
    "BZUSDT": "BRENTOIL",
    "XAUUSDT": "XAU",
    "XAGUSDT": "XAG",
    "CL": "CLOIL",
    "CLOIL": "CLOIL",
    "BRENTOIL": "BRENTOIL",
    "GOLD": "XAU",
    "SILVER": "XAG",
    "SP500": "S&P500",
    "XYZ100": "XYZ100",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--raw-dir", default="raw_boros", help="Raw ZIP directory")
        p.add_argument("--processed-dir", default="processed", help="CSV output directory")
        p.add_argument("--viz-dir", default="visualizations", help="HTML output directory")

    p_download = sub.add_parser("download", help="Download source ZIP files")
    add_common(p_download)
    p_download.add_argument("--refresh-manifest", action="store_true")
    p_download.add_argument(
        "--include",
        action="append",
        help=(
            "Dataset prefix to include. Repeatable. Defaults to APR-relevant "
            "datasets: market-data, settlement, underlying-apr, ohlcv/1d"
        ),
    )
    p_download.add_argument(
        "--include-trades",
        action="store_true",
        help="Also download market-trades files",
    )
    p_download.add_argument(
        "--include-orderbook",
        action="store_true",
        help="Also download order-book files, roughly 150 MB compressed",
    )
    p_download.add_argument("--workers", type=int, default=12)

    p_clean = sub.add_parser("clean", help="Clean raw ZIP files into CSVs")
    add_common(p_clean)

    p_viz = sub.add_parser("visualize", help="Build standalone HTML dashboard")
    add_common(p_viz)

    p_all = sub.add_parser("all", help="Download, clean, and visualize")
    add_common(p_all)
    p_all.add_argument("--refresh-manifest", action="store_true")
    p_all.add_argument("--include", action="append")
    p_all.add_argument("--include-trades", action="store_true")
    p_all.add_argument("--include-orderbook", action="store_true")
    p_all.add_argument("--workers", type=int, default=12)

    args = parser.parse_args()
    if args.command is None:
        args.command = "all"
    return args


def utc_date_from_ts(ts: int | float) -> str:
    return dt.datetime.fromtimestamp(float(ts), tz=dt.UTC).date().isoformat()


def iso_from_ts(ts: int | float) -> str:
    return dt.datetime.fromtimestamp(float(ts), tz=dt.UTC).isoformat()


def parse_maturity(value: str) -> str:
    return dt.datetime.strptime(value, "%d%b%Y").date().isoformat()


def asset_from_symbol(symbol: str) -> str:
    token = symbol[3:] if symbol.startswith("xyz") else symbol
    token = token.upper()
    if token.endswith("USDT"):
        return ASSET_ALIASES.get(token, token.removesuffix("USDT"))
    return ASSET_ALIASES.get(token, token)


def market_meta(market: str) -> dict[str, str]:
    match = MARKET_RE.match(market)
    if not match:
        return {
            "market_id": "",
            "venue": "",
            "exchange": "",
            "symbol": "",
            "asset": "",
            "maturity": "",
            "market": market,
        }
    venue = match.group("venue")
    symbol = match.group("symbol")
    maturity_raw = match.group("maturity")
    return {
        "market_id": match.group("market_id"),
        "venue": venue,
        "exchange": EXCHANGE_NAME.get(venue, venue.title()),
        "symbol": symbol,
        "asset": asset_from_symbol(symbol),
        "maturity": parse_maturity(maturity_raw),
        "market": market,
    }


def ensure_dirs(*paths: str | Path) -> None:
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def fetch_json(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_or_download_manifest(raw_dir: Path, refresh: bool = False) -> list[dict]:
    ensure_dirs(raw_dir)
    manifest_path = raw_dir / "files.json"
    if refresh or not manifest_path.exists():
        files = fetch_json(f"{BASE_URL}/files.json")
        manifest_path.write_text(json.dumps(files, indent=2), encoding="utf-8")
    else:
        files = json.loads(manifest_path.read_text(encoding="utf-8"))
    return files


def selected_prefixes(args: argparse.Namespace) -> tuple[str, ...]:
    if getattr(args, "include", None):
        prefixes = tuple(args.include)
    else:
        prefixes = DEFAULT_DATASETS
    if getattr(args, "include_trades", False):
        prefixes = prefixes + ("market-trades",)
    if getattr(args, "include_orderbook", False):
        prefixes = prefixes + ("order-book",)
    return prefixes


def matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def download_one(file_info: dict, raw_dir: Path) -> tuple[str, str]:
    rel = file_info["path"]
    size = int(file_info["size"])
    dest = raw_dir / rel
    if dest.exists() and dest.stat().st_size == size:
        return rel, "skipped"
    ensure_dirs(dest.parent)
    url = f"{BASE_URL}/{urllib.parse.quote(rel)}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as response:
            tmp.write_bytes(response.read())
        if tmp.stat().st_size != size:
            raise RuntimeError(f"size mismatch: expected {size}, got {tmp.stat().st_size}")
        tmp.replace(dest)
        return rel, "downloaded"
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def download(args: argparse.Namespace) -> None:
    raw_dir = Path(args.raw_dir)
    files = load_or_download_manifest(raw_dir, getattr(args, "refresh_manifest", False))
    prefixes = selected_prefixes(args)
    wanted = [f for f in files if matches_prefix(f["path"], prefixes)]
    total_size = sum(int(f["size"]) for f in wanted)
    print(
        f"Downloading {len(wanted)} files ({total_size / 1024 / 1024:.1f} MB) "
        f"for prefixes: {', '.join(prefixes)}"
    )

    counts = defaultdict(int)
    errors: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(download_one, f, raw_dir) for f in wanted]
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                _, status = fut.result()
                counts[status] += 1
            except Exception as exc:
                errors.append((repr(exc), ""))
                counts["failed"] += 1
            if i % 100 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)} done: {dict(counts)}")
    if errors:
        raise RuntimeError(f"{len(errors)} downloads failed; first error: {errors[0][0]}")


def iter_ndjson_zip(path: Path):
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            with zf.open(name) as fh:
                for raw_line in fh:
                    line = raw_line.decode("utf-8").strip()
                    if line:
                        yield json.loads(line)


def raw_files(raw_dir: Path, prefix: str) -> list[Path]:
    root = raw_dir / prefix
    if not root.exists():
        return []
    return sorted(root.rglob("*.ndjson.zip"))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_market_data(raw_dir: Path, processed_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in raw_files(raw_dir, "market-data"):
        market = path.parts[-2]
        meta = market_meta(market)
        for rec in iter_ndjson_zip(path):
            timestamp = int(rec["timestamp"])
            rows.append(
                {
                    **meta,
                    "timestamp": timestamp,
                    "datetime": rec.get("datetime") or iso_from_ts(timestamp),
                    "date": utc_date_from_ts(timestamp),
                    "blockNumber": rec.get("blockNumber"),
                    "midApr": rec.get("midApr"),
                    "bestBid": rec.get("bestBid"),
                    "bestAsk": rec.get("bestAsk"),
                    "ammImpliedApr": rec.get("ammImpliedApr"),
                    "markApr": rec.get("markApr"),
                    "notionalOI": rec.get("notionalOI"),
                    "lastTradedApr": rec.get("lastTradedApr"),
                    "latestSettlementApr": rec.get("latestSettlementApr"),
                    "source_path": str(path.relative_to(raw_dir)),
                }
            )
    fields = [
        "market",
        "market_id",
        "venue",
        "exchange",
        "symbol",
        "asset",
        "maturity",
        "timestamp",
        "datetime",
        "date",
        "blockNumber",
        "midApr",
        "bestBid",
        "bestAsk",
        "ammImpliedApr",
        "markApr",
        "notionalOI",
        "lastTradedApr",
        "latestSettlementApr",
        "source_path",
    ]
    write_csv(processed_dir / "market_data.csv", rows, fields)
    return rows


def clean_underlying(raw_dir: Path, processed_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in raw_files(raw_dir, "underlying-apr"):
        stem = path.name.removesuffix(".ndjson.zip")
        exchange, asset = stem.split("-", 1)
        asset = ASSET_ALIASES.get(asset.upper(), asset.upper())
        for rec in iter_ndjson_zip(path):
            timestamp = int(rec["timestamp"])
            rows.append(
                {
                    "exchange": exchange,
                    "asset": asset,
                    "timestamp": timestamp,
                    "datetime": rec.get("datetime") or iso_from_ts(timestamp),
                    "date": utc_date_from_ts(timestamp),
                    "annualizedFundingRate": rec.get("annualizedFundingRate"),
                    "source_path": str(path.relative_to(raw_dir)),
                }
            )
    fields = [
        "exchange",
        "asset",
        "timestamp",
        "datetime",
        "date",
        "annualizedFundingRate",
        "source_path",
    ]
    write_csv(processed_dir / "underlying_apr.csv", rows, fields)
    return rows


def clean_settlement(raw_dir: Path, processed_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in raw_files(raw_dir, "settlement"):
        market = path.parts[-2]
        meta = market_meta(market)
        for rec in iter_ndjson_zip(path):
            timestamp = int(rec["timestamp"])
            rows.append(
                {
                    **meta,
                    "timestamp": timestamp,
                    "datetime": rec.get("datetime") or iso_from_ts(timestamp),
                    "date": utc_date_from_ts(timestamp),
                    "blockNumber": rec.get("blockNumber"),
                    "settlementApr": rec.get("settlementApr"),
                    "txHash": rec.get("txHash"),
                    "source_path": str(path.relative_to(raw_dir)),
                }
            )
    fields = [
        "market",
        "market_id",
        "venue",
        "exchange",
        "symbol",
        "asset",
        "maturity",
        "timestamp",
        "datetime",
        "date",
        "blockNumber",
        "settlementApr",
        "txHash",
        "source_path",
    ]
    write_csv(processed_dir / "settlement.csv", rows, fields)
    return rows


def clean_ohlcv_1d(raw_dir: Path, processed_dir: Path) -> list[dict]:
    rows: list[dict] = []
    root = raw_dir / "ohlcv" / "1d"
    for path in sorted(root.rglob("*.ndjson.zip")) if root.exists() else []:
        market = path.parts[-2]
        meta = market_meta(market)
        for rec in iter_ndjson_zip(path):
            timestamp = int(rec["periodStartTimestamp"])
            rows.append(
                {
                    **meta,
                    "periodStartTimestamp": timestamp,
                    "datetime": rec.get("datetime") or iso_from_ts(timestamp),
                    "date": utc_date_from_ts(timestamp),
                    "open": rec.get("open"),
                    "high": rec.get("high"),
                    "low": rec.get("low"),
                    "close": rec.get("close"),
                    "volume": rec.get("volume"),
                    "source_path": str(path.relative_to(raw_dir)),
                }
            )
    fields = [
        "market",
        "market_id",
        "venue",
        "exchange",
        "symbol",
        "asset",
        "maturity",
        "periodStartTimestamp",
        "datetime",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source_path",
    ]
    write_csv(processed_dir / "ohlcv_1d.csv", rows, fields)
    return rows


def aggregate_market_daily(market_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in market_rows:
        grouped[(row["market"], row["date"])].append(row)

    output: list[dict] = []
    for (market, date), rows in sorted(grouped.items()):
        first = rows[0]
        output.append(
            {
                "market": market,
                "market_id": first["market_id"],
                "exchange": first["exchange"],
                "asset": first["asset"],
                "symbol": first["symbol"],
                "maturity": first["maturity"],
                "date": date,
                "samples": len(rows),
                "midApr": mean([to_float(r.get("midApr")) for r in rows]),
                "markApr": mean([to_float(r.get("markApr")) for r in rows]),
                "ammImpliedApr": mean([to_float(r.get("ammImpliedApr")) for r in rows]),
                "latestSettlementApr": mean([to_float(r.get("latestSettlementApr")) for r in rows]),
                "notionalOI": mean([to_float(r.get("notionalOI")) for r in rows]),
            }
        )
    return output


def aggregate_underlying_daily(underlying_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in underlying_rows:
        rate = to_float(row.get("annualizedFundingRate"))
        if rate is not None:
            grouped[(row["exchange"], row["asset"], row["date"])].append(rate)
    output = []
    for (exchange, asset, date), values in sorted(grouped.items()):
        output.append(
            {
                "exchange": exchange,
                "asset": asset,
                "date": date,
                "samples": len(values),
                "realizedApr": mean(values),
            }
        )
    return output


def aggregate_settlement_daily(settlement_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in settlement_rows:
        grouped[(row["market"], row["date"])].append(row)
    output = []
    for (market, date), rows in sorted(grouped.items()):
        first = rows[-1]
        output.append(
            {
                "market": market,
                "market_id": first["market_id"],
                "exchange": first["exchange"],
                "asset": first["asset"],
                "symbol": first["symbol"],
                "maturity": first["maturity"],
                "date": date,
                "samples": len(rows),
                "settlementApr": mean([to_float(r.get("settlementApr")) for r in rows]),
            }
        )
    return output


def build_forward_index(underlying_daily: list[dict]):
    by_pair: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for row in underlying_daily:
        rate = to_float(row.get("realizedApr"))
        if rate is not None:
            by_pair[(row["exchange"], row["asset"])].append((row["date"], rate))

    index = {}
    for key, values in by_pair.items():
        values = sorted(values)
        dates = [d for d, _ in values]
        prefix = [0.0]
        for _, rate in values:
            prefix.append(prefix[-1] + rate)
        index[key] = (dates, prefix)
    return index


def forward_average(index, key: tuple[str, str], start_date: str, end_date: str) -> tuple[float | None, int]:
    if key not in index:
        return None, 0
    dates, prefix = index[key]
    left = bisect.bisect_left(dates, start_date)
    right = bisect.bisect_right(dates, end_date)
    count = right - left
    if count <= 0:
        return None, 0
    return (prefix[right] - prefix[left]) / count, count


def build_comparison(
    market_daily: list[dict],
    underlying_daily: list[dict],
    settlement_daily: list[dict],
) -> list[dict]:
    realized_by_day = {
        (r["exchange"], r["asset"], r["date"]): r for r in underlying_daily
    }
    settlement_by_day = {(r["market"], r["date"]): r for r in settlement_daily}
    forward_idx = build_forward_index(underlying_daily)

    out: list[dict] = []
    for row in market_daily:
        implied = to_float(row.get("midApr"))
        if implied is None:
            implied = to_float(row.get("markApr"))
        key = (row["exchange"], row["asset"])
        realized_row = realized_by_day.get((row["exchange"], row["asset"], row["date"]))
        daily_realized = to_float(realized_row.get("realizedApr")) if realized_row else None
        end_date = min(row["maturity"], dt.date.today().isoformat())
        forward_realized, forward_days = forward_average(forward_idx, key, row["date"], end_date)
        settlement_row = settlement_by_day.get((row["market"], row["date"]))
        settlement = to_float(settlement_row.get("settlementApr")) if settlement_row else None
        out.append(
            {
                "market": row["market"],
                "market_id": row["market_id"],
                "exchange": row["exchange"],
                "asset": row["asset"],
                "symbol": row["symbol"],
                "maturity": row["maturity"],
                "date": row["date"],
                "samples": row["samples"],
                "impliedApr": implied,
                "markApr": row.get("markApr"),
                "ammImpliedApr": row.get("ammImpliedApr"),
                "latestSettlementApr": row.get("latestSettlementApr"),
                "settlementApr": settlement,
                "realizedDailyApr": daily_realized,
                "realizedForwardApr": forward_realized,
                "forwardDaysUsed": forward_days,
                "dailyBasisApr": implied - daily_realized if implied is not None and daily_realized is not None else None,
                "forwardBasisApr": implied - forward_realized if implied is not None and forward_realized is not None else None,
            }
        )
    return out


def clean(args: argparse.Namespace) -> None:
    raw_dir = Path(args.raw_dir)
    processed_dir = Path(args.processed_dir)
    ensure_dirs(processed_dir)

    print("Cleaning market-data...")
    market_rows = clean_market_data(raw_dir, processed_dir)
    print(f"  {len(market_rows):,} market rows")

    print("Cleaning underlying-apr...")
    underlying_rows = clean_underlying(raw_dir, processed_dir)
    print(f"  {len(underlying_rows):,} underlying rows")

    print("Cleaning settlement...")
    settlement_rows = clean_settlement(raw_dir, processed_dir)
    print(f"  {len(settlement_rows):,} settlement rows")

    print("Cleaning ohlcv/1d...")
    ohlcv_rows = clean_ohlcv_1d(raw_dir, processed_dir)
    print(f"  {len(ohlcv_rows):,} daily OHLCV rows")

    market_daily = aggregate_market_daily(market_rows)
    underlying_daily = aggregate_underlying_daily(underlying_rows)
    settlement_daily = aggregate_settlement_daily(settlement_rows)
    comparison = build_comparison(market_daily, underlying_daily, settlement_daily)

    write_csv(
        processed_dir / "market_daily_apr.csv",
        market_daily,
        [
            "market",
            "market_id",
            "exchange",
            "asset",
            "symbol",
            "maturity",
            "date",
            "samples",
            "midApr",
            "markApr",
            "ammImpliedApr",
            "latestSettlementApr",
            "notionalOI",
        ],
    )
    write_csv(
        processed_dir / "underlying_daily_apr.csv",
        underlying_daily,
        ["exchange", "asset", "date", "samples", "realizedApr"],
    )
    write_csv(
        processed_dir / "settlement_daily_apr.csv",
        settlement_daily,
        [
            "market",
            "market_id",
            "exchange",
            "asset",
            "symbol",
            "maturity",
            "date",
            "samples",
            "settlementApr",
        ],
    )
    write_csv(
        processed_dir / "apr_comparison_daily.csv",
        comparison,
        [
            "market",
            "market_id",
            "exchange",
            "asset",
            "symbol",
            "maturity",
            "date",
            "samples",
            "impliedApr",
            "markApr",
            "ammImpliedApr",
            "latestSettlementApr",
            "settlementApr",
            "realizedDailyApr",
            "realizedForwardApr",
            "forwardDaysUsed",
            "dailyBasisApr",
            "forwardBasisApr",
        ],
    )
    print(f"Wrote processed CSVs to {processed_dir}")


def corr(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 2:
        return None
    xvals, yvals = zip(*pairs)
    xmean = statistics.mean(xvals)
    ymean = statistics.mean(yvals)
    num = sum((x - xmean) * (y - ymean) for x, y in pairs)
    den_x = math.sqrt(sum((x - xmean) ** 2 for x in xvals))
    den_y = math.sqrt(sum((y - ymean) ** 2 for y in yvals))
    if den_x == 0 or den_y == 0:
        return None
    return num / den_x / den_y


def summarize_comparison(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("realizedForwardApr") not in ("", None):
            grouped[row["market"]].append(row)

    summaries: list[dict] = []
    for market, vals in grouped.items():
        vals = sorted(vals, key=lambda r: r["date"])
        implied = [float(r["impliedApr"]) for r in vals if r["impliedApr"] not in ("", None)]
        realized_fwd = [
            float(r["realizedForwardApr"])
            for r in vals
            if r["realizedForwardApr"] not in ("", None)
        ]
        basis = [
            float(r["forwardBasisApr"])
            for r in vals
            if r["forwardBasisApr"] not in ("", None)
        ]
        daily_pairs = [
            (float(r["impliedApr"]), float(r["realizedDailyApr"]))
            for r in vals
            if r["impliedApr"] not in ("", None) and r["realizedDailyApr"] not in ("", None)
        ]
        first = vals[0]
        last = vals[-1]
        summaries.append(
            {
                "market": market,
                "exchange": first["exchange"],
                "asset": first["asset"],
                "symbol": first["symbol"],
                "maturity": first["maturity"],
                "rows": len(vals),
                "firstDate": first["date"],
                "lastDate": last["date"],
                "avgImpliedPct": 100 * mean(implied),
                "avgRealizedForwardPct": 100 * mean(realized_fwd),
                "avgBasisBps": 10000 * mean(basis),
                "meanAbsBasisBps": 10000 * mean([abs(b) for b in basis]),
                "lastImpliedPct": 100 * float(last["impliedApr"]) if last["impliedApr"] not in ("", None) else None,
                "lastRealizedForwardPct": (
                    100 * float(last["realizedForwardApr"])
                    if last["realizedForwardApr"] not in ("", None)
                    else None
                ),
                "corrDaily": corr(
                    [p[0] for p in daily_pairs],
                    [p[1] for p in daily_pairs],
                ),
            }
        )
    return sorted(summaries, key=lambda r: abs(r["avgBasisBps"] or 0), reverse=True)


def pct(value: str | float | None) -> float | None:
    f = to_float(value)
    return None if f is None else f * 100


def bps(value: str | float | None) -> float | None:
    f = to_float(value)
    return None if f is None else f * 10000


def build_dashboard_payload(comparison: list[dict]) -> dict:
    rows = [r for r in comparison if r.get("realizedForwardApr") not in ("", None)]
    summaries = summarize_comparison(rows)

    series_by_market = {}
    for row in rows:
        series_by_market.setdefault(row["market"], []).append(
            {
                "date": row["date"],
                "implied": pct(row.get("impliedApr")),
                "realizedDaily": pct(row.get("realizedDailyApr")),
                "realizedForward": pct(row.get("realizedForwardApr")),
                "basisBps": bps(row.get("forwardBasisApr")),
            }
        )
    for market in series_by_market:
        series_by_market[market].sort(key=lambda r: r["date"])

    heat = defaultdict(list)
    for s in summaries:
        heat[(s["exchange"], s["asset"])].append(s["avgBasisBps"])
    heatmap = [
        {"exchange": k[0], "asset": k[1], "avgBasisBps": mean(v), "markets": len(v)}
        for k, v in heat.items()
    ]
    heatmap.sort(key=lambda r: (r["exchange"], r["asset"]))

    dates = [r["date"] for r in rows]
    return {
        "generatedAt": dt.datetime.now(tz=dt.UTC).isoformat(),
        "dateMin": min(dates) if dates else None,
        "dateMax": max(dates) if dates else None,
        "rowCount": len(rows),
        "marketCount": len(series_by_market),
        "summaries": summaries,
        "seriesByMarket": series_by_market,
        "heatmap": heatmap,
    }


def dashboard_html(payload: dict) -> str:
    data = json.dumps(payload, allow_nan=False)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Boros Implied vs Realized APR</title>
<style>
:root {{
  --bg: #f7f7f4;
  --ink: #17201b;
  --muted: #5d665f;
  --line: #d9ddd5;
  --panel: #ffffff;
  --a: #0f766e;
  --b: #b42318;
  --c: #8a5a00;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}}
header {{
  padding: 24px clamp(16px, 4vw, 44px) 16px;
  border-bottom: 1px solid var(--line);
  background: #fbfbf8;
}}
h1 {{ margin: 0 0 6px; font-size: clamp(24px, 3vw, 40px); letter-spacing: 0; }}
p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
main {{ padding: 18px clamp(16px, 4vw, 44px) 42px; }}
.kpis {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 18px; }}
.kpi, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
.kpi {{ padding: 14px; }}
.kpi b {{ display: block; font-size: 22px; margin-bottom: 2px; }}
.kpi span {{ color: var(--muted); font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: 1.25fr 0.75fr; gap: 14px; align-items: start; }}
.panel {{ padding: 14px; min-width: 0; }}
.panel h2 {{ font-size: 16px; margin: 0 0 10px; }}
.controls {{ display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }}
select, input {{
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fff;
  color: var(--ink);
}}
canvas {{ width: 100%; height: 360px; display: block; }}
.legend {{ display: flex; gap: 14px; flex-wrap: wrap; color: var(--muted); font-size: 13px; }}
.dot {{ width: 10px; height: 10px; display: inline-block; border-radius: 50%; margin-right: 5px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--muted); font-weight: 650; }}
.tables {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }}
.heat td {{ font-variant-numeric: tabular-nums; }}
.nowrap {{ white-space: nowrap; }}
@media (max-width: 900px) {{
  .kpis, .grid, .tables {{ grid-template-columns: 1fr; }}
  canvas {{ height: 300px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Boros Implied vs Realized APR</h1>
  <p>Daily Boros implied APR compared with realized exchange funding APR. Forward realized APR averages actual exchange funding from each Boros date through maturity or the latest available funding date.</p>
</header>
<main>
  <section class="kpis" id="kpis"></section>
  <section class="grid">
    <div class="panel">
      <h2>Market Time Series</h2>
      <div class="controls"><select id="marketSelect"></select></div>
      <canvas id="lineChart" width="1000" height="420"></canvas>
      <div class="legend">
        <span><i class="dot" style="background:#0f766e"></i>Implied APR</span>
        <span><i class="dot" style="background:#b42318"></i>Daily realized APR</span>
        <span><i class="dot" style="background:#8a5a00"></i>Forward realized APR</span>
      </div>
    </div>
    <div class="panel">
      <h2>Average Implied vs Forward Realized</h2>
      <canvas id="scatterChart" width="700" height="420"></canvas>
      <p id="scatterNote" style="font-size:13px; margin-top:8px;"></p>
    </div>
  </section>
  <section class="tables">
    <div class="panel">
      <h2>Largest Average Forward Basis</h2>
      <table id="basisTable"></table>
    </div>
    <div class="panel">
      <h2>Average Basis by Exchange and Asset</h2>
      <table class="heat" id="heatTable"></table>
    </div>
  </section>
</main>
<script>
const DATA = {data};
const fmtPct = v => v == null ? "" : v.toFixed(2) + "%";
const fmtBps = v => v == null ? "" : v.toFixed(0);
const css = getComputedStyle(document.documentElement);
const colors = {{
  implied: css.getPropertyValue("--a").trim(),
  daily: css.getPropertyValue("--b").trim(),
  forward: css.getPropertyValue("--c").trim(),
  ink: css.getPropertyValue("--ink").trim(),
  muted: css.getPropertyValue("--muted").trim(),
  line: css.getPropertyValue("--line").trim()
}};

function finite(values) {{ return values.filter(v => v != null && Number.isFinite(v)); }}
function domain(values, pad = 0.08) {{
  const vals = finite(values);
  if (!vals.length) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) {{ lo -= 1; hi += 1; }}
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}}
function clear(ctx, w, h) {{ ctx.clearRect(0, 0, w, h); }}
function axes(ctx, x0, y0, w, h, xLabel, yLabel) {{
  ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0, y0 + h); ctx.lineTo(x0 + w, y0 + h); ctx.stroke();
  ctx.fillStyle = colors.muted; ctx.font = "12px system-ui";
  ctx.fillText(yLabel, x0, y0 - 8);
  ctx.textAlign = "right"; ctx.fillText(xLabel, x0 + w, y0 + h + 28); ctx.textAlign = "left";
}}
function drawLineChart() {{
  const canvas = document.getElementById("lineChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  clear(ctx, w, h);
  const market = document.getElementById("marketSelect").value;
  const rows = DATA.seriesByMarket[market] || [];
  const x0 = 58, y0 = 18, cw = w - 78, ch = h - 70;
  const ys = rows.flatMap(r => [r.implied, r.realizedDaily, r.realizedForward]);
  const [yMin, yMax] = domain(ys);
  const x = i => x0 + (rows.length <= 1 ? 0 : i * cw / (rows.length - 1));
  const y = v => y0 + ch - ((v - yMin) / (yMax - yMin)) * ch;
  axes(ctx, x0, y0, cw, ch, "date", "APR");
  ctx.fillStyle = colors.muted; ctx.font = "12px system-ui";
  for (let t = 0; t <= 4; t++) {{
    const value = yMin + (yMax - yMin) * t / 4;
    const yy = y(value);
    ctx.strokeStyle = colors.line; ctx.beginPath(); ctx.moveTo(x0, yy); ctx.lineTo(x0 + cw, yy); ctx.stroke();
    ctx.fillText(fmtPct(value), 8, yy + 4);
  }}
  if (rows.length) {{
    ctx.fillText(rows[0].date, x0, y0 + ch + 20);
    ctx.textAlign = "right"; ctx.fillText(rows[rows.length - 1].date, x0 + cw, y0 + ch + 20); ctx.textAlign = "left";
  }}
  function plot(key, color) {{
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    let started = false;
    rows.forEach((r, i) => {{
      const v = r[key];
      if (v == null || !Number.isFinite(v)) {{ started = false; return; }}
      if (!started) {{ ctx.moveTo(x(i), y(v)); started = true; }} else {{ ctx.lineTo(x(i), y(v)); }}
    }});
    ctx.stroke();
  }}
  plot("implied", colors.implied);
  plot("realizedDaily", colors.daily);
  plot("realizedForward", colors.forward);
}}
function drawScatter() {{
  const canvas = document.getElementById("scatterChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  clear(ctx, w, h);
  const rows = DATA.summaries.filter(r => r.avgImpliedPct != null && r.avgRealizedForwardPct != null);
  const x0 = 54, y0 = 18, cw = w - 74, ch = h - 70;
  const [lo, hi] = domain(rows.flatMap(r => [r.avgImpliedPct, r.avgRealizedForwardPct]));
  const x = v => x0 + ((v - lo) / (hi - lo)) * cw;
  const y = v => y0 + ch - ((v - lo) / (hi - lo)) * ch;
  axes(ctx, x0, y0, cw, ch, "forward realized APR", "implied APR");
  ctx.strokeStyle = colors.line; ctx.beginPath(); ctx.moveTo(x(lo), y(lo)); ctx.lineTo(x(hi), y(hi)); ctx.stroke();
  ctx.fillStyle = colors.implied;
  rows.forEach(r => {{
    ctx.globalAlpha = 0.68;
    ctx.beginPath(); ctx.arc(x(r.avgRealizedForwardPct), y(r.avgImpliedPct), 3.5, 0, Math.PI * 2); ctx.fill();
  }});
  ctx.globalAlpha = 1;
  ctx.fillStyle = colors.muted; ctx.font = "12px system-ui";
  for (let t = 0; t <= 4; t++) {{
    const value = lo + (hi - lo) * t / 4;
    ctx.fillText(fmtPct(value), x(value) - 14, y0 + ch + 20);
    ctx.fillText(fmtPct(value), 6, y(value) + 4);
  }}
  document.getElementById("scatterNote").textContent = "Each point is one Boros market. Points above the diagonal had higher average implied APR than subsequently realized exchange funding.";
}}
function renderKpis() {{
  const abs = finite(DATA.summaries.map(s => s.meanAbsBasisBps));
  const avgAbs = abs.length ? abs.reduce((a,b) => a + b, 0) / abs.length : null;
  document.getElementById("kpis").innerHTML = [
    ["Markets", DATA.marketCount],
    ["Comparison rows", DATA.rowCount.toLocaleString()],
    ["Date range", `${{DATA.dateMin || ""}} to ${{DATA.dateMax || ""}}`],
    ["Avg abs basis", avgAbs == null ? "" : fmtBps(avgAbs) + " bps"]
  ].map(([label, value]) => `<div class="kpi"><b>${{value}}</b><span>${{label}}</span></div>`).join("");
}}
function renderTables() {{
  const rows = DATA.summaries.slice(0, 24);
  document.getElementById("basisTable").innerHTML = `
    <thead><tr><th>Market</th><th>Avg implied</th><th>Avg realized</th><th>Basis bps</th></tr></thead>
    <tbody>${{rows.map(r => `<tr><td class="nowrap">${{r.market}}</td><td>${{fmtPct(r.avgImpliedPct)}}</td><td>${{fmtPct(r.avgRealizedForwardPct)}}</td><td>${{fmtBps(r.avgBasisBps)}}</td></tr>`).join("")}}</tbody>`;
  const maxAbs = Math.max(...finite(DATA.heatmap.map(r => Math.abs(r.avgBasisBps))), 1);
  document.getElementById("heatTable").innerHTML = `
    <thead><tr><th>Exchange / Asset</th><th>Markets</th><th>Avg basis bps</th></tr></thead>
    <tbody>${{DATA.heatmap.map(r => {{
      const a = Math.min(0.82, Math.abs(r.avgBasisBps) / maxAbs);
      const bg = r.avgBasisBps >= 0 ? `rgba(15,118,110,${{a}})` : `rgba(180,35,24,${{a}})`;
      return `<tr><td>${{r.exchange}} / ${{r.asset}}</td><td>${{r.markets}}</td><td style="background:${{bg}}">${{fmtBps(r.avgBasisBps)}}</td></tr>`;
    }}).join("")}}</tbody>`;
}}
function initSelect() {{
  const select = document.getElementById("marketSelect");
  const options = DATA.summaries
    .slice()
    .sort((a, b) => (b.rows || 0) - (a.rows || 0))
    .map(s => `<option value="${{s.market}}">${{s.market}} (${{s.firstDate}} to ${{s.lastDate}})</option>`);
  select.innerHTML = options.join("");
  select.addEventListener("change", drawLineChart);
}}
renderKpis();
initSelect();
renderTables();
drawLineChart();
drawScatter();
</script>
</body>
</html>
"""


def visualize(args: argparse.Namespace) -> None:
    processed_dir = Path(args.processed_dir)
    viz_dir = Path(args.viz_dir)
    ensure_dirs(viz_dir)
    comparison_path = processed_dir / "apr_comparison_daily.csv"
    if not comparison_path.exists():
        raise FileNotFoundError(f"Missing {comparison_path}; run clean first")
    comparison = read_csv(comparison_path)
    payload = build_dashboard_payload(comparison)
    out = viz_dir / "boros_apr_dashboard.html"
    out.write_text(dashboard_html(payload), encoding="utf-8")
    print(f"Wrote {out}")


def main() -> int:
    args = parse_args()
    try:
        if args.command == "download":
            download(args)
        elif args.command == "clean":
            clean(args)
        elif args.command == "visualize":
            visualize(args)
        elif args.command == "all":
            download(args)
            clean(args)
            visualize(args)
        else:
            raise ValueError(args.command)
    except (urllib.error.URLError, RuntimeError, FileNotFoundError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
