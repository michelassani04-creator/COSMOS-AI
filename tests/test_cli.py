from pathlib import Path

from cosmos_ai.cli import main

CONFIG = str(Path(__file__).resolve().parents[1] / "config.yaml")


def test_check_command(capsys):
    assert main(["--config", CONFIG, "check"]) == 0
    out = capsys.readouterr().out
    assert "Mode : manual" in out and "gemini" in out


def test_demo_command_waits_for_approval(capsys):
    assert main(["--config", CONFIG, "demo"]) == 0
    out = capsys.readouterr().out
    assert "pending_approval" in out


def test_missing_config_returns_error_code(capsys):
    assert main(["--config", "absent.yaml", "check"]) == 2
