#!/usr/bin/env python3
"""Build compact JSON used by the interactive Boros APR website."""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROCESSED = ROOT / "processed"
SITE_DATA = ROOT / "site" / "data"


def f(value):
    if value in ("", None):
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def read_csv(name: str) -> list[dict]:
    with (PROCESSED / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def mean(values):
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return sum(vals) / len(vals) if vals else None


def stdev(values):
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.pstdev(vals) if len(vals) > 1 else None


def percentile(values, q):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def mode(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    counts = defaultdict(int)
    for value in vals:
        counts[value] += 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def corr(xs, ys):
    pairs = [
        (x, y)
        for x, y in zip(xs, ys)
        if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)
    ]
    if len(pairs) < 2:
        return None
    xmean = mean([x for x, _ in pairs])
    ymean = mean([y for _, y in pairs])
    num = sum((x - xmean) * (y - ymean) for x, y in pairs)
    denx = math.sqrt(sum((x - xmean) ** 2 for x, _ in pairs))
    deny = math.sqrt(sum((y - ymean) ** 2 for _, y in pairs))
    return num / denx / deny if denx and deny else None


def date_days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def compact_row(row: dict, settlement_samples: int | None = None) -> dict:
    implied = f(row["impliedApr"])
    realized_daily = f(row["realizedDailyApr"])
    realized_forward = f(row["realizedForwardApr"])
    forward_basis = f(row["forwardBasisApr"])
    daily_basis = f(row["dailyBasisApr"])
    return {
        "d": row["date"],
        "i": implied,
        "rd": realized_daily,
        "rf": realized_forward,
        "db": daily_basis,
        "fb": forward_basis,
        "s": f(row["settlementApr"]),
        "ls": f(row["latestSettlementApr"]),
        "n": int(float(row["samples"])) if row["samples"] else 0,
        "fd": int(float(row["forwardDaysUsed"])) if row["forwardDaysUsed"] else 0,
        "ss": settlement_samples,
    }


def build():
    comparison = read_csv("apr_comparison_daily.csv")
    ohlcv = read_csv("ohlcv_1d.csv")
    underlying = read_csv("underlying_daily_apr.csv")
    settlement_daily = read_csv("settlement_daily_apr.csv")
    source_date_max = max(row["date"] for row in comparison)
    settlement_samples_by_day = {
        (row["market"], row["date"]): int(float(row["samples"]))
        for row in settlement_daily
        if row.get("samples")
    }

    markets = {}
    series = defaultdict(list)
    for row in comparison:
        market = row["market"]
        if market not in markets:
            markets[market] = {
                "market": market,
                "marketId": row["market_id"],
                "exchange": row["exchange"],
                "asset": row["asset"],
                "symbol": row["symbol"],
                "maturity": row["maturity"],
            }
        series[market].append(compact_row(row, settlement_samples_by_day.get((market, row["date"]))))

    ohlcv_by_market = defaultdict(list)
    for row in ohlcv:
        ohlcv_by_market[row["market"]].append(
            {
                "d": row["date"],
                "o": f(row["open"]),
                "h": f(row["high"]),
                "l": f(row["low"]),
                "c": f(row["close"]),
                "v": f(row["volume"]),
            }
        )

    underlying_by_pair = defaultdict(list)
    for row in underlying:
        underlying_by_pair[f"{row['exchange']}|{row['asset']}"].append(
            {"d": row["date"], "r": f(row["realizedApr"])}
        )

    market_payload = []
    all_dates = []
    for market, meta in markets.items():
        if meta["maturity"] > source_date_max:
            continue
        rows = sorted(series[market], key=lambda r: r["d"])
        o_rows = sorted(ohlcv_by_market.get(market, []), key=lambda r: r["d"])
        fb = [r["fb"] for r in rows]
        db = [r["db"] for r in rows]
        implied = [r["i"] for r in rows]
        realized_forward = [r["rf"] for r in rows]
        latest = rows[-1]
        first = rows[0]
        settlement_frequency = mode([r["ss"] for r in rows])
        volumes = [r["v"] for r in o_rows]
        latest_volume = volumes[-1] if volumes else None
        maturity_days_left = date_days(source_date_max, meta["maturity"])
        summary = {
            **meta,
            "firstDate": first["d"],
            "lastDate": latest["d"],
            "rows": len(rows),
            "settlementSamplesPerDay": settlement_frequency,
            "matured": meta["maturity"] <= source_date_max,
            "daysToMaturity": maturity_days_left,
            "latestImplied": latest["i"],
            "latestRealizedDaily": latest["rd"],
            "latestRealizedForward": latest["rf"],
            "latestForwardBasis": latest["fb"],
            "avgImplied": mean(implied),
            "avgRealizedForward": mean(realized_forward),
            "avgForwardBasis": mean(fb),
            "medianForwardBasis": percentile(fb, 0.5),
            "p10ForwardBasis": percentile(fb, 0.1),
            "p90ForwardBasis": percentile(fb, 0.9),
            "basisVol": stdev(fb),
            "meanAbsForwardBasis": mean([abs(v) for v in fb if v is not None]),
            "positiveBasisShare": mean([1.0 if v and v > 0 else 0.0 for v in fb if v is not None]),
            "avgDailyBasis": mean(db),
            "corrImpliedRealizedDaily": corr(
                [r["i"] for r in rows],
                [r["rd"] for r in rows],
            ),
            "latestVolume": latest_volume,
            "avgVolume": mean(volumes),
            "totalVolume": sum(v for v in volumes if v is not None) if volumes else None,
        }
        all_dates.extend([first["d"], latest["d"]])
        market_payload.append({"summary": summary, "series": rows, "ohlcv": o_rows})

    payload = {
        "generatedAt": dt.datetime.now(tz=dt.UTC).isoformat(),
        "source": "https://historical-data.boros.finance/index.html",
        "dateMin": min(all_dates),
        "dateMax": max(all_dates),
        "markets": sorted(market_payload, key=lambda r: r["summary"]["market"]),
        "underlying": {
            key: sorted(rows, key=lambda r: r["d"])
            for key, rows in sorted(underlying_by_pair.items())
        },
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "boros_apr_site_data.json").write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"Wrote {SITE_DATA / 'boros_apr_site_data.json'} "
        f"({len(market_payload)} markets)"
    )


if __name__ == "__main__":
    build()
