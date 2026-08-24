import json

from boros_research.validation import BuildReport, render_report, write_report


def test_build_report_contains_required_metrics_and_is_deterministic(tmp_path):
    report = BuildReport(
        build_status="ok",
        source_files_expected=7,
        source_files_present=7,
        parse_failures=0,
        stale_observations=2,
        missing_observations=1,
        unpriceable_observations=3,
        market_group_count=4,
        opportunity_row_count=12,
        depth_sufficient_rate_by_notional={"2000": 0.5, "1000": None},
        coverage_start=1787529600,
        coverage_end=1787530200,
    )

    report_path = write_report(report, tmp_path / "build_report.json")
    payload = json.loads(report_path.read_text())

    assert payload == {
        "build_status": "ok",
        "coverage_end": 1787530200,
        "coverage_start": 1787529600,
        "depth_sufficient_rate_by_notional": {"1000": None, "2000": 0.5},
        "dataset_row_counts": {},
        "failure_details": [],
        "fully_executable_rows": 0,
        "invalid_opportunity_rows": 0,
        "market_group_count": 4,
        "metadata_missing_market_ids": [],
        "missing_observations": 1,
        "opportunity_row_count": 12,
        "parse_failure_paths": [],
        "parse_failures": 0,
        "source_files_expected": 7,
        "source_files_present": 7,
        "stale_observations": 2,
        "unpriceable_observations": 3,
    }
    text = render_report(report)
    assert "source_files_expected: 7" in text
    assert "opportunity_row_count: 12" in text
