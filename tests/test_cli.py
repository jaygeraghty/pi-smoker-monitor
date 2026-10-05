"""Tests for the `smoker` command line.

The snapshot tests swap the real ETI Cloud source for a fake one (using
pytest's monkeypatch), so they never touch the network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smoker_monitor import __version__, cli
from smoker_monitor.cli import main
from smoker_monitor.config import EtiCloudSettings
from smoker_monitor.domain.models import Gateway, Snapshot
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.eti_cloud import to_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "eti_idle.json"

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


class FakeSource:
    """Stands in for EtiCloudSource: returns the real capture, no network.

    That capture is weeks old, so it always raises a "Gateway silent" alarm.
    """

    def __init__(self, settings: EtiCloudSettings) -> None:
        self.settings = settings

    async def fetch(self) -> Snapshot:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return to_snapshot(raw, datetime.now(UTC))


class FreshSource(FakeSource):
    """Stands in for EtiCloudSource when everything is fine: a Gateway seen just now."""

    async def fetch(self) -> Snapshot:
        now = datetime.now(UTC)
        gateway = Gateway(
            serial="G1",
            label="RFX GATEWAY",
            battery_pct=80,
            wifi_dbm=-50,
            last_seen=now,
            pit=None,
            fan=None,
        )
        return Snapshot(taken_at=now, gateway=gateway, probes=())


class FailingSource(FakeSource):
    """Stands in for EtiCloudSource when ETI Cloud can't be reached."""

    async def fetch(self) -> Snapshot:
        raise SourceError("ETI Cloud is unreachable")


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


def test_snapshot_prints_readings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "EtiCloudSource", FakeSource)
    main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert "PROBE-1" in captured.out
    assert "°C" in captured.out


def test_snapshot_with_alarms_shows_them_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old capture means the Gateway has been silent: an alarm, exit code 2."""
    monkeypatch.setattr(cli, "EtiCloudSource", FakeSource)
    code = main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out.startswith("*** 1 ALARM ***")
    assert "Gateway silent for" in captured.out


def test_snapshot_all_fine_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "EtiCloudSource", FreshSource)
    code = main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert code == 0
    assert "ALARM" not in captured.out


def test_snapshot_uses_alarm_settings_from_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching the Gateway timeout off in [alarms] silences the old capture."""
    monkeypatch.setattr(cli, "EtiCloudSource", FakeSource)
    path = write_config(tmp_path)
    path.write_text(VALID + "\n[alarms]\ngateway_timeout = false\n", encoding="utf-8")
    code = main(["snapshot", "--config", str(path)])
    assert code == 0
    assert "ALARM" not in capsys.readouterr().out


def test_snapshot_source_error_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "EtiCloudSource", FailingSource)
    code = main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert code == 1
    assert "ETI Cloud is unreachable" in captured.err
    assert "Traceback" not in captured.err


def test_snapshot_never_prints_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "EtiCloudSource", FakeSource)
    main(["snapshot", "--config", str(write_config(tmp_path))])
    captured = capsys.readouterr()
    assert "s3cret-pw" not in captured.out
    assert "s3cret-pw" not in captured.err
