from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import boros_research.cli as cli


def write_env(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


def test_repository_env_loads_before_cli_defaults(tmp_path, monkeypatch):
    env_path = write_env(
        tmp_path / ".env",
        "CROSSEX_BASE_URL=http://127.0.0.1:7777\n",
    )
    captured = {}

    def capture(args):
        captured["base_url"] = args.base_url
        return 0

    monkeypatch.setattr(cli, "ENV_PATH", env_path)
    monkeypatch.setattr(cli, "_run_monitor_command", capture)

    with patch.dict(os.environ, {}, clear=True):
        assert cli.main(["monitor"]) == 0

    assert captured["base_url"] == "http://127.0.0.1:7777"


def test_process_environment_wins_over_dotenv(tmp_path):
    env_path = write_env(
        tmp_path / ".env",
        "CROSSEX_BASE_URL=http://from-dotenv:7777\n",
    )

    with patch.dict(
        os.environ,
        {"CROSSEX_BASE_URL": "http://from-shell:8888"},
        clear=True,
    ):
        cli.load_project_environment(env_path)
        args = cli.build_parser().parse_args(["monitor"])

    assert args.base_url == "http://from-shell:8888"


def test_missing_dotenv_is_harmless(tmp_path):
    with patch.dict(os.environ, {}, clear=True):
        cli.load_project_environment(tmp_path / "missing.env")

        assert "TELEGRAM_BOT_TOKEN" not in os.environ
        assert "TELEGRAM_CHAT_ID" not in os.environ


def test_telegram_sender_sees_dotenv_credentials(tmp_path):
    env_path = write_env(
        tmp_path / ".env",
        "TELEGRAM_BOT_TOKEN=fake-token\nTELEGRAM_CHAT_ID=fake-chat\n",
    )

    with patch.dict(os.environ, {}, clear=True):
        cli.load_project_environment(env_path)
        sender = cli._telegram_sender()

    assert sender is not None
    assert sender._bot_token == "fake-token"
    assert sender._chat_id == "fake-chat"


def test_normal_cli_output_does_not_print_dotenv_secrets(tmp_path, monkeypatch, capsys):
    env_path = write_env(
        tmp_path / ".env",
        "TELEGRAM_BOT_TOKEN=secret-token-for-test\nTELEGRAM_CHAT_ID=secret-chat-for-test\n",
    )
    monkeypatch.setattr(cli, "ENV_PATH", env_path)

    with patch.dict(os.environ, {}, clear=True):
        assert cli.main(["--help"]) == 0

    output = capsys.readouterr()
    assert "secret-token-for-test" not in output.out
    assert "secret-chat-for-test" not in output.out
    assert "secret-token-for-test" not in output.err
    assert "secret-chat-for-test" not in output.err
