from __future__ import annotations

from pathlib import Path

import pytest

import boros_research.cli as cli
from boros_research.monitor import MonitorCycleResult
from boros_research.position_models import PositionsSnapshot
from boros_research.position_monitor import PositionCycleResult


ADDRESS_FROM_ENV = "0x1234567890abcdef1234567890abcdef12345678"
ADDRESS_OVERRIDE = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"


def write_env(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


def test_positions_uses_boros_address_from_dotenv(tmp_path, monkeypatch):
    env_path = write_env(tmp_path / ".env", f"BOROS_ADDRESS={ADDRESS_FROM_ENV}\n")
    captured = {}

    def capture(args):
        captured["address"] = args.address
        return 0

    monkeypatch.setattr(cli, "ENV_PATH", env_path)
    monkeypatch.setattr(cli, "_run_positions_command", capture)
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("BOROS_ADDRESS", raising=False)
        assert cli.main(["positions", "--once"]) == 0

    assert captured["address"] is None
    assert cli.resolve_position_address(None) == ADDRESS_FROM_ENV


def test_cli_address_overrides_environment_and_never_writes_dotenv(tmp_path, monkeypatch):
    env_path = write_env(tmp_path / ".env", f"BOROS_ADDRESS={ADDRESS_FROM_ENV}\n")
    before = env_path.read_text(encoding="utf-8")
    captured = {}

    def capture(args):
        captured["resolved"] = cli.resolve_position_address(args.address)
        return 0

    monkeypatch.setattr(cli, "ENV_PATH", env_path)
    monkeypatch.setattr(cli, "_run_positions_command", capture)
    monkeypatch.setenv("BOROS_ADDRESS", ADDRESS_FROM_ENV)

    assert cli.main(["positions", "--address", ADDRESS_OVERRIDE, "--once"]) == 0

    assert captured["resolved"] == ADDRESS_OVERRIDE
    assert env_path.read_text(encoding="utf-8") == before


def test_missing_position_address_fails_clearly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("BOROS_ADDRESS", raising=False)

    assert cli.main(["positions", "--once"]) == 1
    assert "BOROS_ADDRESS" in capsys.readouterr().err


def test_positions_parser_has_approved_defaults(monkeypatch):
    monkeypatch.delenv("CROSSEX_BASE_URL", raising=False)
    args = cli.build_parser().parse_args(["positions"])

    assert args.address is None
    assert args.dry_run is False
    assert args.once is False
    assert args.state_path == Path("data/position_monitor.sqlite3")
    assert args.interval == 60
    assert args.base_url == "http://127.0.0.1:6688"


def test_resolve_position_address_rejects_empty_values(monkeypatch):
    monkeypatch.delenv("BOROS_ADDRESS", raising=False)

    with pytest.raises(ValueError, match="BOROS_ADDRESS"):
        cli.resolve_position_address(None)


def test_positions_dry_run_uses_ephemeral_state_and_preserves_both_sqlite_files(tmp_path, monkeypatch):
    p2_path = tmp_path / "position_monitor.sqlite3"
    p1_path = tmp_path / "live_monitor.sqlite3"
    p1_path.write_bytes(b"P1 sentinel")
    p2_path.write_bytes(b"P2 sentinel")
    before_p1 = p1_path.read_bytes()
    before_p2 = p2_path.read_bytes()

    class FakeClient:
        def fetch_strategy(self, _address):
            return ()

        def fetch_positions(self):
            return PositionsSnapshot((), 1_788_100_000, ())

    monkeypatch.setattr(cli, "CrossExClient", lambda **_kwargs: FakeClient())
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    args = cli.build_parser().parse_args(
        [
            "positions",
            "--address",
            ADDRESS_FROM_ENV,
            "--dry-run",
            "--once",
            "--state-path",
            str(p2_path),
        ]
    )

    assert cli._run_positions_command(args) == 0
    assert p1_path.read_bytes() == before_p1
    assert p2_path.read_bytes() == before_p2


def _p1_cycle() -> MonitorCycleResult:
    return MonitorCycleResult(
        group_counts={},
        unavailable_notionals=(),
        mapped_opportunity_count=0,
        benchmarkable_opportunity_count=0,
        normal_candidate_count=0,
        urgent_candidate_count=0,
        skipped_reasons=(),
        warnings=(),
        messages=(),
        sizes_by_opportunity=(),
        historical_max_timestamp=1_000,
        benchmark_age_seconds=1,
    )


def _p2_cycle() -> PositionCycleResult:
    return PositionCycleResult(
        strategy_success=True,
        auxiliary_success=True,
        primary_unknown=False,
        strategy_count=0,
        exposure_group_count=0,
        events=(),
        messages=(),
        snapshots=(),
        warnings=(),
        delivery_events=(),
        telegram_sent_count=0,
    )


def test_dashboard_observers_publish_to_requested_paths_and_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 1_234)

    p1_observer = cli._p1_dashboard_observer(tmp_path, 37)
    p1_observer(_p1_cycle(), None)
    p2_observer = cli._p2_dashboard_observer(tmp_path, 41)
    p2_observer(_p2_cycle(), None)

    from boros_research.dashboard_snapshot import read_snapshot

    p1 = read_snapshot(tmp_path / "p1_latest.json", "p1")
    p2 = read_snapshot(tmp_path / "p2_latest.json", "p2")
    assert p1 is not None and p1["cycleTimestamp"] == 1_234
    assert p1["pollIntervalSeconds"] == 37
    assert p2 is not None and p2["cycleTimestamp"] == 1_234
    assert p2["pollIntervalSeconds"] == 41


def test_monitor_dry_run_does_not_install_dashboard_observer_or_write_snapshot(
    tmp_path, monkeypatch
):
    sentinel = tmp_path / "p1_latest.json"
    sentinel.write_bytes(b"p1 sentinel")
    monkeypatch.setattr(cli, "P1_DASHBOARD_SNAPSHOT", sentinel)

    captured = {}

    class FakeClient:
        pass

    class FakeBenchmark:
        pass

    class FakeMonitor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_once(self, *, dry_run):
            return _p1_cycle()

    monkeypatch.setattr(cli, "CrossExClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(cli, "HistoricalBenchmarkLookup", lambda *_args, **_kwargs: FakeBenchmark())
    monkeypatch.setattr(cli, "LiveMonitor", FakeMonitor)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    args = cli.build_parser().parse_args(["monitor", "--dry-run", "--once"])
    assert cli._run_monitor_command(args) == 0

    assert captured["cycle_observer"] is None
    assert sentinel.read_bytes() == b"p1 sentinel"


def test_positions_dry_run_does_not_install_dashboard_observer_or_write_snapshot(
    tmp_path, monkeypatch
):
    sentinel = tmp_path / "p2_latest.json"
    sentinel.write_bytes(b"p2 sentinel")
    monkeypatch.setattr(cli, "P2_DASHBOARD_SNAPSHOT", sentinel)
    captured = {}

    class FakeClient:
        pass

    class FakeMonitor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_once(self, *, dry_run):
            return _p2_cycle()

    monkeypatch.setattr(cli, "CrossExClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(cli, "PositionMonitor", FakeMonitor)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    args = cli.build_parser().parse_args(
        ["positions", "--address", ADDRESS_FROM_ENV, "--dry-run", "--once"]
    )
    assert cli._run_positions_command(args) == 0

    assert captured["cycle_observer"] is None
    assert sentinel.read_bytes() == b"p2 sentinel"


def test_production_monitor_wires_snapshot_observer_with_command_interval(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "p1_latest.json"
    monkeypatch.setattr(cli, "P1_DASHBOARD_SNAPSHOT", snapshot)
    captured = {}

    class FakeClient:
        pass

    class FakeBenchmark:
        pass

    class FakeMonitor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_once(self, *, dry_run):
            captured["cycle_observer"](_p1_cycle(), None)
            return _p1_cycle()

    monkeypatch.setattr(cli, "CrossExClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(cli, "HistoricalBenchmarkLookup", lambda *_args, **_kwargs: FakeBenchmark())
    monkeypatch.setattr(cli, "LiveMonitor", FakeMonitor)
    monkeypatch.setattr(cli.time, "time", lambda: 2_000)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    args = cli.build_parser().parse_args(["monitor", "--once", "--interval", "37"])
    assert cli._run_monitor_command(args) == 0

    assert captured["cycle_observer"] is not None
    from boros_research.dashboard_snapshot import read_snapshot

    published = read_snapshot(snapshot, "p1")
    assert published is not None
    assert published["pollIntervalSeconds"] == 37
