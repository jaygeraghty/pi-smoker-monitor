"""Tests for the status file that screens read (state/status.json).

The file describes the cook in three lists (pits, probes and sources), plus
the alarms. Each pit and probe carries an id, its source, and whether its data
is fresh, so screens never need the alarm rules.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarm_state import AlarmState, step
from smoker_monitor.domain.alarms import Alarm, AlarmKind, AlarmRules
from smoker_monitor.domain.models import (
    AlarmLimit,
    AlarmSettings,
    Fan,
    FanActivity,
    Gateway,
    Probe,
    Reading,
    RingColour,
    Snapshot,
    SourceHealth,
)
from smoker_monitor.service.status import (
    SCHEMA_VERSION,
    STATUS_FILE,
    build_status,
    limit_c,
    write_status,
)

NOW = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
RULES = AlarmRules()

PIT_LIMITS = AlarmSettings(high=AlarmLimit(True, 123.9), low=AlarmLimit(True, 96.1))
GATEWAY = Gateway(
    serial="G1",
    label="RFX GATEWAY",
    battery_pct=80,
    wifi_dbm=-55,
    last_seen=NOW,
    pit=Reading(110.0, NOW),
    fan=Fan(set_temp_celsius=110.0, state=1, connected=True, activity=FanActivity.BLOWING),
    pit_alarms=PIT_LIMITS,
    source="eti",
)
PROBE = Probe(
    serial="P1",
    label="RFX MEAT",
    battery_pct=90,
    last_seen=NOW,
    sensors=(Reading(68.0, NOW), Reading(None, NOW)),
    alarms=AlarmSettings(high=AlarmLimit(True, 95.0), low=None),
    name="white probe",
    rings=(RingColour.WHITE,),
    source="eti",
)
ETI = SourceHealth(key="eti", name="ETI Cloud", expects_gateway=True)
SNAPSHOT = Snapshot(taken_at=NOW, gateways=(GATEWAY,), probes=(PROBE,), sources=(ETI,))
PIT_HOT = Alarm(AlarmKind.PIT_HIGH, "eti:G1", 182.0, 170.0)


def status(snapshot: Snapshot = SNAPSHOT, state: AlarmState | None = None) -> dict[str, Any]:
    """build_status with the usual time and rules."""
    return build_status(snapshot, state or AlarmState(), NOW, RULES)


def ringing(alarm: Alarm) -> AlarmState:
    """An alarm state where `alarm` has been seen twice, so it's ringing."""
    state = step(AlarmState(), [alarm], NOW - timedelta(seconds=30))
    return step(state, [alarm], NOW)


def pit(snapshot: Snapshot = SNAPSHOT) -> dict[str, Any]:
    """The one pit in the status."""
    [only] = status(snapshot)["pits"]
    result: dict[str, Any] = only
    return result


def probe(item: Probe) -> dict[str, Any]:
    """The status of a single probe."""
    [only] = status(Snapshot(NOW, (GATEWAY,), (item,), (ETI,)))["probes"]
    result: dict[str, Any] = only
    return result


# --------------------------------------------------------------- top level ---


def test_top_level_when_all_is_well() -> None:
    result = status()
    assert result["schema"] == SCHEMA_VERSION == 2
    assert result["updated_at"] == "2026-10-05T23:00:00+00:00"
    assert result["sound_on"] is False
    assert result["sources"] == [{"name": "ETI Cloud", "ok": True, "error": None}]
    assert result["alarms"] == []


def test_failed_fetch_shows_in_sources() -> None:
    down = replace(ETI, error="ETI Cloud is down", failing_since=NOW)
    result = status(replace(SNAPSHOT, sources=(down,)))
    assert result["sources"] == [{"name": "ETI Cloud", "ok": False, "error": "ETI Cloud is down"}]


def test_every_source_is_listed() -> None:
    other = SourceHealth(key="other", name="Other", expects_gateway=False)
    names = [s["name"] for s in status(replace(SNAPSHOT, sources=(ETI, other)))["sources"]]
    assert names == ["ETI Cloud", "Other"]


def test_each_device_names_its_own_source() -> None:
    other = SourceHealth(key="other", name="Other", expects_gateway=False)
    theirs = replace(PROBE, source="other")
    snapshot = Snapshot(NOW, (GATEWAY,), (PROBE, theirs), (ETI, other))
    result = status(snapshot)
    assert [p["source"] for p in result["probes"]] == ["ETI Cloud", "Other"]
    assert [p["id"] for p in result["probes"]] == ["eti:P1", "other:P1"]


# --------------------------------------------------------------------- pit ---


def test_gateway_as_a_pit() -> None:
    assert pit() == {
        "id": "eti:G1",
        "source": "ETI Cloud",
        "kind": "gateway",
        "name": "RFX GATEWAY",
        "fresh": True,
        "last_seen": "2026-10-05T23:00:00+00:00",
        "temp_c": 110.0,
        "set_c": 110.0,
        "high_c": 123.9,
        "low_c": 96.1,
        "fan": "blowing",
        "fan_output_pct": None,
        "pellet_pct": None,
        "details": [
            {"label": "Battery", "value": "80%"},
            {"label": "Wi-Fi", "value": "-55 dBm"},
        ],
    }


def test_twin_gateways_get_numbered_names() -> None:
    twin = replace(GATEWAY, serial="G2")
    names = [p["name"] for p in status(Snapshot(NOW, (GATEWAY, twin), (), (ETI,)))["pits"]]
    assert names == ["RFX GATEWAY 1", "RFX GATEWAY 2"]


def test_no_gateway_means_no_pits() -> None:
    assert status(Snapshot(NOW, (), ()))["pits"] == []


def test_pit_without_a_fan() -> None:
    bare = Gateway("G1", None, None, None, NOW, None, None)
    result = pit(Snapshot(NOW, (bare,), ()))
    assert result["fan"] is None
    assert result["set_c"] is None
    assert result["temp_c"] is None
    assert result["high_c"] is None and result["low_c"] is None
    assert result["details"] == [
        {"label": "Battery", "value": "?"},
        {"label": "Wi-Fi", "value": "?"},
    ]


def test_disconnected_fan_has_no_set_temperature() -> None:
    """The Billows remembers its last set temperature; it means nothing unplugged."""
    unplugged = Fan(set_temp_celsius=298.9, state=1, connected=False)
    gateway = Gateway("G1", None, 80, -55, NOW, Reading(23.0, NOW), unplugged)
    result = pit(Snapshot(NOW, (gateway,), ()))
    assert result["fan"] == "not connected"
    assert result["set_c"] is None


def test_unknown_fan_state_is_shown_as_is() -> None:
    odd = Fan(set_temp_celsius=110.0, state=0, connected=True)
    gateway = Gateway("G1", None, 80, -55, NOW, Reading(110.0, NOW), odd)
    assert pit(Snapshot(NOW, (gateway,), ()))["fan"] == "state 0 (UNKNOWN)"


def test_stale_gateway_is_not_fresh() -> None:
    old = Gateway("G1", None, 80, -55, NOW - timedelta(minutes=6), None, None)
    assert pit(Snapshot(NOW, (old,), ()))["fresh"] is False


# ------------------------------------------------------------------ probes ---


def test_probe() -> None:
    assert probe(PROBE) == {
        "id": "eti:P1",
        "source": "ETI Cloud",
        "name": "white probe",
        "rings": ["white"],
        "fresh": True,
        "last_seen": "2026-10-05T23:00:00+00:00",
        "core_c": 68.0,
        "sensors_c": [68.0, None],
        "target_c": 95.0,
        "low_c": None,
        "battery_pct": 90,
    }


def test_probe_in_the_drawer_is_not_fresh() -> None:
    old = Probe("P2", "RFX MEAT", 100, NOW - timedelta(hours=9), (Reading(22.5, NOW),))
    assert probe(old)["fresh"] is False


def test_probe_uses_its_display_name() -> None:
    """Unnamed, no rings: falls back to the type, never the serial."""
    plain = Probe("P2", "RFX MEAT", 100, NOW, ())
    assert probe(plain)["name"] == "RFX MEAT"


def test_switched_off_target_is_none() -> None:
    off = Probe(
        "P2", "RFX MEAT", 100, NOW, (), alarms=AlarmSettings(high=AlarmLimit(False, 95.0), low=None)
    )
    assert probe(off)["target_c"] is None


# ------------------------------------------------------------------ alarms ---


def test_ringing_alarm_points_at_its_device() -> None:
    result = status(state=ringing(PIT_HOT))
    assert result["sound_on"] is True
    [alarm] = result["alarms"]
    assert alarm == {
        "kind": "pit_high",
        "device": "eti:G1",  # matches the pit's id
        "source": None,  # only set for alarms about a whole source
        "status": "ringing",
        "value": 182.0,
        "limit": 170.0,
        "since": "2026-10-05T22:59:30+00:00",
        "message": "Pit too hot: 182.0°C (limit 170.0°C)",
    }


def test_alarm_about_a_whole_source() -> None:
    """'No gateway found' isn't about any one device, but about ETI Cloud."""
    missing = Alarm(AlarmKind.GATEWAY_TIMEOUT, "eti", None, 5.0)
    [alarm] = status(Snapshot(NOW, (), (), (ETI,)), ringing(missing))["alarms"]
    assert alarm["device"] is None
    assert alarm["source"] == "ETI Cloud"
    assert alarm["message"] == "No gateway found from ETI Cloud"


# ----------------------------------------------------------------- helpers ---


def test_limit_c() -> None:
    assert limit_c(AlarmLimit(True, 95.0)) == 95.0
    assert limit_c(AlarmLimit(False, 95.0)) is None
    assert limit_c(AlarmLimit(True, None)) is None
    assert limit_c(None) is None


# ----------------------------------------------------------------- writing ---


def test_write_status_gives_valid_json_and_no_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "state" / STATUS_FILE  # the folder doesn't exist yet
    result = status(state=ringing(PIT_HOT))
    write_status(path, result)

    assert json.loads(path.read_text(encoding="utf-8")) == result
    assert [p.name for p in path.parent.iterdir()] == [STATUS_FILE]  # no .tmp left


def test_write_status_replaces_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / STATUS_FILE
    write_status(path, status(state=ringing(PIT_HOT)))
    write_status(path, status())
    assert json.loads(path.read_text(encoding="utf-8"))["sound_on"] is False
