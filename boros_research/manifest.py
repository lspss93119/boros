from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .config import CROSSEX_VENUES
from .normalize import parse_market_slug


LIGHTWEIGHT_PREFIXES = (
    "market-data/",
    "settlement/",
    "underlying-apr/",
    "funding-rate/",
    "ohlcv/5m/",
    "ohlcv/1d/",
)

_COMBINED_BOOK_PATH_RE = re.compile(
    r"^order-book/(?P<market_slug>[^/]+)/combined_0\.0001/"
    r"(?P<month>[0-9]{4}-[0-9]{2})\.ndjson\.zip$"
)


def select_archive_files(
    files: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Select lightweight history and CrossEx-compatible combined books."""
    selected: list[Mapping[str, Any]] = []
    for entry in files:
        path = entry.get("path")
        if not isinstance(path, str):
            continue

        if path.startswith(LIGHTWEIGHT_PREFIXES):
            selected.append(entry)
            continue

        match = _COMBINED_BOOK_PATH_RE.fullmatch(path)
        if match is None:
            continue

        market = parse_market_slug(match.group("market_slug"))
        if market.venue in CROSSEX_VENUES:
            selected.append(entry)

    return selected
