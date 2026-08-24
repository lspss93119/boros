import json
from pathlib import Path

import pytest

from boros_research.orderbook import BookLevel, parse_combined_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "combined_orderbook.ndjson"


def fixture_records():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]


def test_combined_book_long_is_bid_and_short_is_ask():
    snapshot = parse_combined_snapshot(fixture_records()[0])

    assert snapshot.bids == (
        BookLevel(rate_apr=0.10, size_collateral=1000.0),
        BookLevel(rate_apr=0.09, size_collateral=2000.0),
    )
    assert snapshot.asks == (
        BookLevel(rate_apr=0.11, size_collateral=1000.0),
        BookLevel(rate_apr=0.12, size_collateral=3000.0),
    )


def test_unsorted_levels_are_sorted_by_side_semantics():
    snapshot = parse_combined_snapshot(fixture_records()[1])

    assert [level.rate_apr for level in snapshot.bids] == [0.10, 0.08]
    assert [level.rate_apr for level in snapshot.asks] == [0.115, 0.13]


def test_invalid_rates_and_sizes_are_discarded():
    snapshot = parse_combined_snapshot(
        {
            "timestamp": 1787443320,
            "blockNumber": None,
            "long": [
                {"rate": float("nan"), "size": 100},
                {"rate": float("inf"), "size": 100},
                {"rate": 0.10, "size": 0},
                {"rate": 0.09, "size": -1},
                {"rate": 0.08, "size": 100},
            ],
            "short": [
                {"rate": 0.11, "size": float("nan")},
                {"rate": 0.12, "size": float("inf")},
                {"rate": 0.13, "size": 100},
            ],
        }
    )

    assert snapshot.block_number is None
    assert snapshot.bids == (BookLevel(rate_apr=0.08, size_collateral=100.0),)
    assert snapshot.asks == (BookLevel(rate_apr=0.13, size_collateral=100.0),)


@pytest.mark.parametrize("side", ["long", "short"])
def test_non_array_side_raises_value_error(side):
    record = {
        "timestamp": 1787443320,
        "long": [],
        "short": [],
    }
    record[side] = {"rate": 0.1, "size": 1}

    with pytest.raises(ValueError, match=side):
        parse_combined_snapshot(record)


def test_non_object_level_raises_value_error():
    with pytest.raises(ValueError, match="long"):
        parse_combined_snapshot(
            {
                "timestamp": 1787443320,
                "long": [0.1],
                "short": [],
            }
        )
