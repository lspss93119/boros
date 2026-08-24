"""Build the compact, read-only Boros Market Radar payload."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import duckdb

from boros_research.crossex_client import (
    DEFAULT_CROSSEX_BASE_URL,
    MONITORED_NOTIONALS,
    CrossExClient,
    CrossExPair,
)
from boros_research.radar import build_radar_payload
from boros_research.radar_economics import ProxyEconomics, collect_asset_proxy


DEFAULT_DATABASE = Path("data/boros.duckdb")
DEFAULT_OUTPUT = Path("site/data/boros_market_radar.json")
MAX_PAYLOAD_BYTES = 1_048_576


class MarketRadarProxyError(RuntimeError):
    """The current CrossEx proxy data cannot be used for a Radar build."""


def _is_valid_proxy_pair(pair: CrossExPair) -> bool:
    return (
        not pair.reasons
        and pair.costs.total_usd is not None
        and math.isfinite(pair.costs.total_usd)
        and pair.costs.total_usd >= 0
        and pair.capital_usd is not None
        and math.isfinite(pair.capital_usd)
        and pair.capital_usd > 0
    )


def collect_proxies(
    client: CrossExClient,
) -> tuple[dict[int, dict[str, ProxyEconomics]], dict[str, object]]:
    """Fetch current conservative proxy economics exactly once per notional."""
    proxies_by_notional: dict[int, dict[str, ProxyEconomics]] = {}
    current_venues: set[str] = set()
    warnings: list[dict[str, object]] = []

    for notional_usd in MONITORED_NOTIONALS:
        try:
            response = client.fetch(notional_usd)
        except (RuntimeError, ValueError) as exc:
            raise MarketRadarProxyError(
                "Market Radar proxy unavailable: start the local CrossEx terminal/API and retry."
            ) from exc

        for warning in response.warnings:
            warnings.append(
                {
                    "notionalUsd": notional_usd,
                    "scope": "response",
                    "text": warning,
                }
            )
        for group in response.groups:
            for warning in group.warnings:
                warnings.append(
                    {
                        "notionalUsd": notional_usd,
                        "scope": "group",
                        "tokenId": group.token_id,
                        "text": warning,
                    }
                )

        valid_pairs = [
            pair
            for group in response.groups
            for pair in group.pairs
            if _is_valid_proxy_pair(pair)
        ]
        for pair in valid_pairs:
            current_venues.update(
                (pair.short_leg.crossex_venue, pair.long_leg.crossex_venue)
            )

        proxies_by_notional[notional_usd] = {}
        for asset in sorted({pair.base.upper() for pair in valid_pairs}):
            proxy = collect_asset_proxy(response, asset)
            if proxy is not None:
                proxies_by_notional[notional_usd][asset] = proxy

    scope_order = {"response": 0, "group": 1}
    warnings.sort(
        key=lambda item: (
            scope_order[str(item["scope"])],
            int(item["notionalUsd"]),
            int(item.get("tokenId", 0)),
            str(item["text"]),
        )
    )
    return proxies_by_notional, {
        "currentVenues": sorted(current_venues),
        "warnings": warnings,
    }


def build_payload(
    database_path: Path,
    client: CrossExClient,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a Radar payload without mutating the source database."""
    proxies_by_notional, diagnostics = collect_proxies(client)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        payload = build_radar_payload(
            connection,
            proxies_by_notional,
            generated_at=generated_at,
        )
    finally:
        connection.close()
    payload["proxyDiagnostics"] = diagnostics
    return payload


def write_payload(output_path: Path, payload: dict[str, Any]) -> int:
    """Encode and write a compact finite payload, rejecting oversized output."""
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    encoded_size = len(encoded.encode("utf-8"))
    if encoded_size >= MAX_PAYLOAD_BYTES:
        raise RuntimeError("Market Radar payload exceeds 1 MiB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    return encoded_size


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--crossex-base-url", default=DEFAULT_CROSSEX_BASE_URL)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        payload = build_payload(
            args.database,
            CrossExClient(args.crossex_base_url),
        )
    except MarketRadarProxyError:
        print(
            "Market Radar proxy unavailable: start the local CrossEx terminal/API and retry.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    write_payload(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
