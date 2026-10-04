"""Tests for the `smoker` command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from smoker_monitor import __version__
from smoker_monitor.cli import main

VALID = """
[eti_cloud]
email = "me@example.com"
password = "s3cret-pw"
api_key = "test-api-key"
app_id = "test-app-id"
referer = "https://cloud.etiltd.com/"

[polling]
interval_seconds = 30
"""


def write_config(tmp_path: Path) -> Path:
    """Write a valid config.toml into pytest's temporary folder."""
    path = tmp_path / "config.toml"
    path.write_text(VALID, encoding="utf-8")
    return path


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: smoker" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_snapshot_missing_config_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["snapshot", "--config", str(tmp_path / "nope.toml")])
    captured = capsys.readouterr()
    assert code == 1
    assert "config.example.toml" in captured.err
    assert "Traceback" not in captured.err


def test_snapshot_valid_config_reports_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert code == 0
    assert "Config OK" in captured.out
    assert "30s" in captured.out


def test_snapshot_never_prints_password(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert "s3cret-pw" not in captured.out
    assert "s3cret-pw" not in captured.err
