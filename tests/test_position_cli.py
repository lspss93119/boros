from __future__ import annotations

import os
from pathlib import Path

import pytest

import boros_research.cli as cli


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
