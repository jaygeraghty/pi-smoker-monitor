"""Tests for the status file that screens read (state/status.json)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from smoker_monitor.domain.alarm_state import AlarmState, step
from smoker_monitor.domain.alarms import Alarm, AlarmKind
from smoker_monitor.domain.models import Fan, Gateway, Probe, Reading, Snapshot
from smoker_monitor.service.status import STATUS_FILE, build_status, write_status

NOW = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)

GATEWAY = Gateway(
    serial="G1",
    label="RFX GATEWAY",
    battery_pct=80,
    wifi_dbm=-55,
    last_seen=NOW,
    pit=Reading(110.0, NOW),
    fan=Fan(set_temp_celsius=110.0, state=1, connected=True),
)
PROBE = Probe(
    serial="P1",
    label="PROBE-1",
    battery_pct=90,
    last_seen=NOW,
    sensors=(Reading(68.0, NOW), Reading(None, NOW)),
)
SNAPSHOT = Snapshot(taken_at=NOW, gateway=GATEWAY, probes=(PROBE,))
PIT_HOT = Alarm(AlarmKind.PIT_HIGH, "G1", 182.0, 170.0)


def ringing_state() -> AlarmState:
    state = step(AlarmState(), [PIT_HOT], NOW - timedelta(seconds=30))
    return step(state, [PIT_HOT], NOW)


def test_quiet_status() -> None:
    status = build_status(SNAPSHOT, AlarmState(), NOW, None)
    assert status["updated_at"] == "2026-10-05T23:00:00+00:00"
    assert status["sound_on"] is False
    assert status["fetch_error"] is None
    assert status["alarms"] == []


def test_gateway_and_probe_readings() -> None:
    status = build_status(SNAPSHOT, AlarmState(), NOW, None)
    assert status["gateway"] == {
        "serial": "G1",
        "label": "RFX GATEWAY",
        "battery_pct": 80,
        "wifi_dbm": -55,
        "last_seen": "2026-10-05T23:00:00+00:00",
        "pit_c": 110.0,
        "fan": {"connected": True, "set_c": 110.0, "state": 1},
    }
    assert status["probes"] == [
        {
            "serial": "P1",
            "label": "PROBE-1",
            "battery_pct": 90,
            "last_seen": "2026-10-05T23:00:00+00:00",
            "core_c": 68.0,
            "sensors_c": [68.0, None],
        }
    ]


def test_no_gateway_and_no_fan() -> None:
    no_gateway = Snapshot(taken_at=NOW, gateway=None, probes=())
    assert build_status(no_gateway, AlarmState(), NOW, None)["gateway"] is None

    no_fan = Snapshot(
        taken_at=NOW, gateway=Gateway("G1", None, None, None, NOW, None, None), probes=()
    )
    gateway = build_status(no_fan, AlarmState(), NOW, None)["gateway"]
    assert gateway["fan"] is None
    assert gateway["pit_c"] is None


def test_ringing_alarm_is_described() -> None:
    status = build_status(SNAPSHOT, ringing_state(), NOW, "ETI Cloud is down")
    assert status["sound_on"] is True
    assert status["fetch_error"] == "ETI Cloud is down"
    [alarm] = status["alarms"]
    assert alarm["kind"] == "pit_high"
    assert alarm["device"] == "G1"
    assert alarm["status"] == "ringing"
    assert alarm["value"] == 182.0
    assert alarm["limit"] == 170.0
    assert alarm["since"] == "2026-10-05T22:59:30+00:00"
    assert isinstance(alarm["message"], str) and alarm["message"]


def test_write_status_gives_valid_json_and_no_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "state" / STATUS_FILE  # the folder doesn't exist yet
    status = build_status(SNAPSHOT, ringing_state(), NOW, None)
    write_status(path, status)

    assert json.loads(path.read_text(encoding="utf-8")) == status
    assert [p.name for p in path.parent.iterdir()] == [STATUS_FILE]  # no .tmp left


def test_write_status_replaces_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / STATUS_FILE
    write_status(path, build_status(SNAPSHOT, ringing_state(), NOW, None))
    write_status(path, build_status(SNAPSHOT, AlarmState(), NOW, None))
    assert json.loads(path.read_text(encoding="utf-8"))["sound_on"] is False
