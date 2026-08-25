from __future__ import annotations

import os
from pathlib import Path

import pytest

import boros_research.cli as cli
from boros_research.position_models import PositionsSnapshot


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
