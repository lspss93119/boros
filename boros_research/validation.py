from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuildReport:
    """Deterministic quality summary for one derived-data build."""

    build_status: str
    source_files_expected: int
    source_files_present: int
    parse_failures: int
    stale_observations: int
    missing_observations: int
    unpriceable_observations: int
    market_group_count: int
    opportunity_row_count: int
    depth_sufficient_rate_by_notional: dict[str, float | None]
    coverage_start: int | None
    coverage_end: int | None
    dataset_row_counts: dict[str, int] = field(default_factory=dict)
    parse_failure_paths: tuple[str, ...] = ()
    failure_details: tuple[str, ...] = ()
    metadata_missing_market_ids: tuple[int, ...] = ()
    fully_executable_rows: int = 0
    invalid_opportunity_rows: int = 0

    def __post_init__(self) -> None:
        if self.build_status not in {"ok", "failed"}:
            raise ValueError("build_status must be 'ok' or 'failed'")
        for name in (
            "source_files_expected",
            "source_files_present",
            "parse_failures",
            "stale_observations",
            "missing_observations",
            "unpriceable_observations",
            "market_group_count",
            "opportunity_row_count",
            "fully_executable_rows",
            "invalid_opportunity_rows",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source_files_present > self.source_files_expected:
            raise ValueError("source_files_present cannot exceed source_files_expected")
        for name in ("coverage_start", "coverage_end"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ValueError(f"{name} must be an integer or null")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parse_failure_paths"] = list(self.parse_failure_paths)
        payload["failure_details"] = list(self.failure_details)
        payload["metadata_missing_market_ids"] = list(self.metadata_missing_market_ids)
        payload["dataset_row_counts"] = {
            str(key): value
            for key, value in sorted(self.dataset_row_counts.items())
        }
        payload["depth_sufficient_rate_by_notional"] = {
            str(key): value
            for key, value in sorted(
                self.depth_sufficient_rate_by_notional.items(),
                key=lambda item: str(item[0]),
            )
        }
        return payload


def render_report(report: BuildReport) -> str:
    """Render the stable human-readable core metrics used by the CLI."""
    payload = report.to_dict()
    lines = [
        f"build_status: {payload['build_status']}",
        f"source_files_expected: {payload['source_files_expected']}",
        f"source_files_present: {payload['source_files_present']}",
        f"parse_failures: {payload['parse_failures']}",
        f"stale_observations: {payload['stale_observations']}",
        f"missing_observations: {payload['missing_observations']}",
        f"unpriceable_observations: {payload['unpriceable_observations']}",
        f"market_group_count: {payload['market_group_count']}",
        f"opportunity_row_count: {payload['opportunity_row_count']}",
        f"fully_executable_rows: {payload['fully_executable_rows']}",
        f"invalid_opportunity_rows: {payload['invalid_opportunity_rows']}",
        f"coverage_start: {payload['coverage_start']}",
        f"coverage_end: {payload['coverage_end']}",
        "depth_sufficient_rate_by_notional: "
        + json.dumps(payload["depth_sufficient_rate_by_notional"], sort_keys=True),
    ]
    return "\n".join(lines)


def write_report(report: BuildReport, path: Path) -> Path:
    """Write a deterministic JSON report using an atomic promotion."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with part.open("w", encoding="utf-8") as output:
            json.dump(report.to_dict(), output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        part.replace(target)
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise
    return target
