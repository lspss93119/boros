import json
from pathlib import Path

from boros_research.orderbook import parse_combined_snapshot
from boros_research.timeline import align_snapshots_to_grid


FIXTURE = Path(__file__).parent / "fixtures" / "combined_orderbook.ndjson"
BASE_TIMESTAMP = 1787443200


def fixture_snapshots():
    return [
        parse_combined_snapshot(json.loads(line))
        for line in FIXTURE.read_text().splitlines()
        if line
    ]


def snapshot_at(timestamp):
    return parse_combined_snapshot(
        {
            "timestamp": timestamp,
            "blockNumber": 1,
            "long": [{"rate": 0.10, "size": 1000}],
            "short": [{"rate": 0.11, "size": 1000}],
        }
    )


def test_alignment_uses_newest_prior_snapshot_only():
    observations = align_snapshots_to_grid(
        fixture_snapshots(),
        start_timestamp=BASE_TIMESTAMP + 300,
        end_timestamp=BASE_TIMESTAMP + 1200,
    )

    assert [observation.grid_timestamp for observation in observations] == [
        BASE_TIMESTAMP + 300,
        BASE_TIMESTAMP + 600,
        BASE_TIMESTAMP + 900,
        BASE_TIMESTAMP + 1200,
    ]
    assert observations[0].snapshot_timestamp == BASE_TIMESTAMP + 120
    assert observations[0].snapshot_age_sec == 180
    assert observations[1].snapshot_timestamp == BASE_TIMESTAMP + 120
    assert observations[1].snapshot_age_sec == 480
    assert observations[2].snapshot_timestamp == BASE_TIMESTAMP + 780
    assert observations[2].snapshot_age_sec == 120
    assert observations[3].snapshot_timestamp == BASE_TIMESTAMP + 780
    assert observations[3].snapshot_age_sec == 420
    assert all(observation.status == "ok" for observation in observations)


def test_future_snapshot_is_not_used_and_missing_has_no_book():
    observation = align_snapshots_to_grid(
        [snapshot_at(BASE_TIMESTAMP + 120)],
        start_timestamp=BASE_TIMESTAMP,
        end_timestamp=BASE_TIMESTAMP,
    )[0]

    assert observation.status == "missing"
    assert observation.snapshot_timestamp is None
    assert observation.snapshot_age_sec is None
    assert observation.block_number is None
    assert observation.bids is None
    assert observation.asks is None


def test_stale_snapshot_keeps_provenance_but_no_executable_book():
    observation = align_snapshots_to_grid(
        [snapshot_at(BASE_TIMESTAMP + 120)],
        start_timestamp=BASE_TIMESTAMP + 1200,
        end_timestamp=BASE_TIMESTAMP + 1200,
    )[0]

    assert observation.status == "stale"
    assert observation.snapshot_timestamp == BASE_TIMESTAMP + 120
    assert observation.snapshot_age_sec == 1080
    assert observation.block_number == 1
    assert observation.bids is None
    assert observation.asks is None


def test_freshness_boundary_age_900_is_valid_and_901_is_stale():
    valid = align_snapshots_to_grid(
        [snapshot_at(BASE_TIMESTAMP)],
        start_timestamp=BASE_TIMESTAMP + 900,
        end_timestamp=BASE_TIMESTAMP + 900,
    )[0]
    stale = align_snapshots_to_grid(
        [snapshot_at(BASE_TIMESTAMP - 1)],
        start_timestamp=BASE_TIMESTAMP + 900,
        end_timestamp=BASE_TIMESTAMP + 900,
    )[0]

    assert valid.status == "ok"
    assert valid.snapshot_age_sec == 900
    assert valid.bids is not None
    assert valid.asks is not None
    assert stale.status == "stale"
    assert stale.snapshot_age_sec == 901
    assert stale.bids is None
    assert stale.asks is None
