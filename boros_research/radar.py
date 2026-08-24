"""Read-only historical aggregation for the Boros market radar."""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from typing import Any

import duckdb

from .config import CROSSEX_VENUES
from .radar_economics import ProxyEconomics, derive_viability_cutoff


RADAR_NOTIONALS = (10_000, 25_000, 50_000)
RADAR_WINDOW_DAYS = 90
MIN_DIRECTION_SAMPLES = 50
MIN_VENUE_COMPARISONS = 50
MIN_NET_PROFIT_USD = 50.0
MIN_HOLDING_RETURN = 0.01
PROXY_MODEL = "current-crossex-conservative-envelope-v1"

_WINDOW_SECONDS = RADAR_WINDOW_DAYS * 24 * 60 * 60


def _query_dicts(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[Any] | None = None,
) -> list[dict[str, Any]]:
    result = connection.execute(query, parameters or [])
    return [
        {column[0]: value for column, value in zip(result.description, record)}
        for record in result.fetchall()
    ]


def _create_identity_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Expose only opportunities whose two market identities are unambiguous."""
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW market_identity AS
        SELECT
            market_id,
            MIN(token_id) AS token_id,
            MIN(venue) AS venue,
            MIN(asset) AS asset,
            MIN(maturity) AS maturity
        FROM markets
        GROUP BY market_id
        HAVING COUNT(DISTINCT token_id) = 1
           AND COUNT(DISTINCT venue) = 1
           AND COUNT(DISTINCT asset) = 1
           AND COUNT(DISTINCT maturity) = 1
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW valid_opportunities AS
        SELECT o.*
        FROM executable_opportunities o
        JOIN market_identity sm
          ON sm.market_id = o.short_market_id
         AND sm.token_id = o.token_id
         AND sm.venue = o.short_venue
         AND sm.asset = o.asset
         AND sm.maturity = o.maturity
        JOIN market_identity lm
          ON lm.market_id = o.long_market_id
         AND lm.token_id = o.token_id
         AND lm.venue = o.long_venue
         AND lm.asset = o.asset
         AND lm.maturity = o.maturity
        """
    )


def _anchor_timestamp(connection: duckdb.DuckDBPyConnection) -> int | None:
    row = connection.execute(
        """
        SELECT MAX(timestamp)
        FROM valid_opportunities
        WHERE notional_usd IN (10000, 25000, 50000)
        """
    ).fetchone()
    return None if row[0] is None else int(row[0])


def _reference_spreads(
    connection: duckdb.DuckDBPyConnection, anchor: int
) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        WITH snapshot_best AS (
            SELECT
                asset,
                token_id,
                maturity,
                timestamp,
                notional_usd,
                MAX(executable_spread_apr) AS best_spread
            FROM valid_opportunities
            WHERE notional_usd IN (10000, 25000, 50000)
              AND timestamp >= ?
              AND fully_executable
              AND executable_spread_apr IS NOT NULL
              AND isfinite(executable_spread_apr)
              AND executable_spread_apr > 0
            GROUP BY asset, token_id, maturity, timestamp, notional_usd
        )
        SELECT
            asset,
            notional_usd,
            quantile_cont(best_spread, 0.95) AS reference_p95_spread_apr
        FROM snapshot_best
        GROUP BY asset, notional_usd
        ORDER BY asset, notional_usd
        """,
        [anchor - _WINDOW_SECONDS],
    )


def _proxy_payload(proxy: ProxyEconomics) -> dict[str, Any]:
    return {
        "costUsd": float(proxy.cost_usd),
        "capitalUsd": float(proxy.capital_usd),
        "asOfTimestamp": int(proxy.as_of_timestamp),
        "pairCount": int(proxy.pair_count),
    }


def _create_cutoff_relation(
    connection: duckdb.DuckDBPyConnection,
    references: list[dict[str, Any]],
    proxies_by_notional: dict[int, dict[str, ProxyEconomics]],
) -> dict[tuple[str, int], dict[str, Any]]:
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE radar_cutoffs (
            asset VARCHAR,
            notional_usd DOUBLE,
            cutoff_days INTEGER,
            reference_p95_spread_apr DOUBLE,
            proxy_cost_usd DOUBLE,
            proxy_capital_usd DOUBLE,
            proxy_as_of_timestamp BIGINT,
            proxy_pair_count BIGINT
        )
        """
    )
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    relation_rows: list[tuple[Any, ...]] = []
    for reference in references:
        asset = str(reference["asset"])
        notional = int(reference["notional_usd"])
        spread = float(reference["reference_p95_spread_apr"])
        proxy = proxies_by_notional.get(notional, {}).get(asset)
        cutoff = None
        if proxy is not None:
            try:
                cutoff = derive_viability_cutoff(
                    notional_usd=notional,
                    reference_p95_spread_apr=spread,
                    proxy=proxy,
                    min_net_profit_usd=MIN_NET_PROFIT_USD,
                    min_holding_return=MIN_HOLDING_RETURN,
                )
            except ValueError:
                cutoff = None
        if cutoff is None:
            rows[(asset, notional)] = {
                "asset": asset,
                "notionalUsd": notional,
                "available": False,
                "unavailableReason": "proxy-unavailable",
                "dteCutoffDays": None,
                "referenceP95SpreadApr": None,
                "proxy": None,
                "normal": None,
                "burst": None,
                "highVenue": None,
                "lowVenue": None,
                "highVenueComparisonCount": None,
                "lowVenueComparisonCount": None,
            }
            relation_rows.append((asset, notional, None, None, None, None, None, None))
            continue
        rows[(asset, notional)] = {
            "asset": asset,
            "notionalUsd": notional,
            "available": True,
            "unavailableReason": None,
            "dteCutoffDays": int(cutoff.cutoff_days),
            "referenceP95SpreadApr": spread,
            "proxy": _proxy_payload(proxy),
            "normal": None,
            "burst": None,
            "highVenue": None,
            "lowVenue": None,
            "highVenueComparisonCount": None,
            "lowVenueComparisonCount": None,
        }
        relation_rows.append(
            (
                asset,
                notional,
                int(cutoff.cutoff_days),
                spread,
                float(proxy.cost_usd),
                float(proxy.capital_usd),
                int(proxy.as_of_timestamp),
                int(proxy.pair_count),
            )
        )
    if relation_rows:
        connection.executemany(
            "INSERT INTO radar_cutoffs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", relation_rows
        )
    return rows


def _create_eligible_view(
    connection: duckdb.DuckDBPyConnection,
    anchor: int,
    supported_venues: Collection[str] | None = None,
) -> None:
    venue_universe = (
        CROSSEX_VENUES if supported_venues is None else frozenset(supported_venues)
    )
    venue_filter = "FALSE"
    if venue_universe:
        supported_venues_sql = ", ".join(
            "'" + venue.replace("'", "''") + "'" for venue in sorted(venue_universe)
        )
        venue_filter = (
            f"o.short_venue IN ({supported_venues_sql}) "
            f"AND o.long_venue IN ({supported_venues_sql})"
        )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW radar_eligible AS
        SELECT o.*, c.cutoff_days
        FROM valid_opportunities o
        JOIN radar_cutoffs c
          ON c.asset = o.asset
         AND c.notional_usd = o.notional_usd
        WHERE c.cutoff_days IS NOT NULL
          AND o.timestamp >= {anchor - _WINDOW_SECONDS}
          AND o.fully_executable
          AND o.executable_spread_apr IS NOT NULL
          AND isfinite(o.executable_spread_apr)
          AND o.dte_days >= c.cutoff_days
          AND {venue_filter}
        """
    )


def _drilldown_maturity(
    connection: duckdb.DuckDBPyConnection,
    *,
    asset: str,
    notional: int,
    short_venue: str,
    long_venue: str,
    anchor: int,
) -> str | None:
    anchor_date = dt.datetime.fromtimestamp(anchor, tz=dt.timezone.utc).date()
    row = connection.execute(
        """
        SELECT maturity
        FROM radar_eligible
        WHERE asset = ?
          AND notional_usd = ?
          AND short_venue = ?
          AND long_venue = ?
        GROUP BY maturity
        ORDER BY
            CASE WHEN maturity >= ? THEN 0 ELSE 1 END,
            CASE WHEN maturity >= ? THEN maturity END ASC,
            CASE WHEN maturity < ? THEN maturity END DESC
        LIMIT 1
        """,
        [
            asset,
            notional,
            short_venue,
            long_venue,
            anchor_date,
            anchor_date,
            anchor_date,
        ],
    ).fetchone()
    return None if row is None else row[0].isoformat()


def _direction_stats(
    connection: duckdb.DuckDBPyConnection,
    anchor: int,
) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        SELECT
            asset,
            notional_usd,
            short_venue,
            long_venue,
            COUNT(*) AS sample_count,
            MEDIAN(executable_spread_apr) AS median_spread_apr,
            quantile_cont(executable_spread_apr, 0.95) AS p95_spread_apr
        FROM radar_eligible
        GROUP BY asset, notional_usd, short_venue, long_venue
        HAVING COUNT(*) >= ?
        """,
        [MIN_DIRECTION_SAMPLES],
    )


def _direction_payload(
    connection: duckdb.DuckDBPyConnection,
    direction: dict[str, Any],
    metric: str,
    anchor: int,
) -> dict[str, Any]:
    return {
        "shortVenue": str(direction["short_venue"]),
        "longVenue": str(direction["long_venue"]),
        metric: float(
            direction["median_spread_apr"]
            if metric == "medianSpreadApr"
            else direction["p95_spread_apr"]
        ),
        "sampleCount": int(direction["sample_count"]),
        "drilldownMaturity": _drilldown_maturity(
            connection,
            asset=str(direction["asset"]),
            notional=int(direction["notional_usd"]),
            short_venue=str(direction["short_venue"]),
            long_venue=str(direction["long_venue"]),
            anchor=anchor,
        ),
    }


def _apply_direction_statistics(
    connection: duckdb.DuckDBPyConnection,
    rows: dict[tuple[str, int], dict[str, Any]],
    anchor: int,
) -> None:
    directions = _direction_stats(connection, anchor)
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for direction in directions:
        by_key.setdefault(
            (str(direction["asset"]), int(direction["notional_usd"])), []
        ).append(direction)
    for key, candidates in by_key.items():
        normal = sorted(
            candidates,
            key=lambda item: (
                -float(item["median_spread_apr"]),
                str(item["short_venue"]),
                str(item["long_venue"]),
            ),
        )[0]
        burst = sorted(
            candidates,
            key=lambda item: (
                -float(item["p95_spread_apr"]),
                str(item["short_venue"]),
                str(item["long_venue"]),
            ),
        )[0]
        rows[key]["normal"] = _direction_payload(
            connection, normal, "medianSpreadApr", anchor
        )
        rows[key]["burst"] = _direction_payload(
            connection, burst, "p95SpreadApr", anchor
        )


def _venue_winners(
    connection: duckdb.DuckDBPyConnection, side: str
) -> list[dict[str, Any]]:
    if side == "high":
        venue_column = "short_venue"
        market_column = "short_market_id"
        rate_column = "short_bid_vwap_apr"
        ordering = "DESC"
    else:
        venue_column = "long_venue"
        market_column = "long_market_id"
        rate_column = "long_ask_vwap_apr"
        ordering = "ASC"
    return _query_dicts(
        connection,
        f"""
        WITH venue_quotes AS (
            SELECT
                asset, token_id, maturity, timestamp, notional_usd,
                {venue_column} AS venue,
                MIN({rate_column}) AS rate
            FROM radar_eligible
            WHERE {rate_column} IS NOT NULL
              AND isfinite({rate_column})
            GROUP BY asset, token_id, maturity, timestamp, notional_usd, {venue_column}
            HAVING COUNT(DISTINCT {market_column}) = 1
               AND MIN({rate_column}) = MAX({rate_column})
        ), ranked AS (
            SELECT
                *,
                COUNT(*) OVER comparison_window AS venue_count,
                ROW_NUMBER() OVER comparison_window AS rank,
                LEAD(rate) OVER comparison_window AS next_rate
            FROM venue_quotes
            WINDOW comparison_window AS (
                PARTITION BY asset, token_id, maturity, timestamp, notional_usd
                ORDER BY rate {ordering}, venue
            )
        ), group_results AS (
            SELECT
                asset, notional_usd, token_id, maturity, timestamp,
                MAX(venue_count) AS venue_count,
                MAX(CASE WHEN rank = 1 AND (next_rate IS NULL OR rate <> next_rate) THEN venue END)
                    AS winner
            FROM ranked
            GROUP BY asset, notional_usd, token_id, maturity, timestamp
            HAVING MAX(venue_count) >= 2
        ), comparison_counts AS (
            SELECT asset, notional_usd, COUNT(*) AS comparison_count
            FROM group_results
            GROUP BY asset, notional_usd
        ), win_counts AS (
            SELECT asset, notional_usd, winner, COUNT(*) AS win_count
            FROM group_results
            WHERE winner IS NOT NULL
            GROUP BY asset, notional_usd, winner
        ), best_win_counts AS (
            SELECT asset, notional_usd, MAX(win_count) AS max_win_count
            FROM win_counts
            GROUP BY asset, notional_usd
        ), winner_choices AS (
            SELECT
                wins.asset,
                wins.notional_usd,
                CASE WHEN COUNT(*) = 1 THEN MIN(wins.winner) END AS venue
            FROM win_counts wins
            JOIN best_win_counts best
              ON best.asset = wins.asset
             AND best.notional_usd = wins.notional_usd
             AND best.max_win_count = wins.win_count
            GROUP BY wins.asset, wins.notional_usd
        )
        SELECT
            counts.asset,
            counts.notional_usd,
            counts.comparison_count,
            CASE
                WHEN counts.comparison_count < {MIN_VENUE_COMPARISONS} THEN NULL
                ELSE choices.venue
            END AS venue
        FROM comparison_counts counts
        LEFT JOIN winner_choices choices
          ON choices.asset = counts.asset
         AND choices.notional_usd = counts.notional_usd
        """,
    )


def _apply_venue_winners(
    connection: duckdb.DuckDBPyConnection,
    rows: dict[tuple[str, int], dict[str, Any]],
) -> None:
    for side, venue_field, count_field in (
        ("high", "highVenue", "highVenueComparisonCount"),
        ("low", "lowVenue", "lowVenueComparisonCount"),
    ):
        for result in _venue_winners(connection, side):
            key = (str(result["asset"]), int(result["notional_usd"]))
            if key not in rows:
                continue
            rows[key][venue_field] = result["venue"]
            rows[key][count_field] = int(result["comparison_count"])


def build_radar_payload(
    connection: duckdb.DuckDBPyConnection,
    proxies_by_notional: dict[int, dict[str, ProxyEconomics]],
    *,
    generated_at: str | None = None,
    supported_venues: Collection[str] | None = None,
) -> dict[str, Any]:
    """Build the compact Task 3 radar payload without mutating production data."""
    _create_identity_views(connection)
    anchor = _anchor_timestamp(connection)
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "generatedAt": (
            generated_at
            if generated_at is not None
            else (
                dt.datetime.fromtimestamp(anchor, tz=dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if anchor is not None
                else "1970-01-01T00:00:00Z"
            )
        ),
        "historicalMaxTimestamp": anchor,
        "windowDays": RADAR_WINDOW_DAYS,
        "notionals": list(RADAR_NOTIONALS),
        "viability": {
            "minNetProfitUsd": MIN_NET_PROFIT_USD,
            "minHoldingReturn": MIN_HOLDING_RETURN,
            "proxyModel": PROXY_MODEL,
        },
        "rows": [],
    }
    if anchor is None:
        return payload

    references = _reference_spreads(connection, anchor)
    rows = _create_cutoff_relation(connection, references, proxies_by_notional)
    _create_eligible_view(connection, anchor, supported_venues)
    _apply_direction_statistics(connection, rows, anchor)
    _apply_venue_winners(connection, rows)
    payload["rows"] = [rows[key] for key in sorted(rows)]
    return payload
