"""Tests for mapping raw ETI Cloud data to a Snapshot.

Most tests use tests/fixtures/eti_idle.json: a real, redacted capture taken
with the kit switched off (air probe unplugged, Billows disconnected).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.domain.models import Gateway, Snapshot
from smoker_monitor.domain.units import f_to_c
from smoker_monitor.sources.eti_cloud import (
    LONG_AGO,
    parse_time,
    to_celsius,
    to_snapshot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "eti_idle.json"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def load_raw() -> dict[str, Any]:
    """Load the redacted real capture."""
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def idle_snapshot() -> Snapshot:
    """The Snapshot built from the real capture."""
    return to_snapshot(load_raw(), NOW)


def idle_gateway() -> Gateway:
    """The Gateway from the real capture (it is always present there)."""
    gateway = idle_snapshot().gateway
    assert gateway is not None
    return gateway


# --------------------------------------------------- the real capture ---


def test_snapshot_has_gateway_and_two_probes() -> None:
    snapshot = idle_snapshot()
    assert snapshot.taken_at == NOW
    assert snapshot.gateway is not None
    assert [p.serial for p in snapshot.probes] == ["PROBE-1", "PROBE-2"]


def test_gateway_details() -> None:
    gateway = idle_gateway()
    assert gateway.serial == "GATEWAY-1"
    assert gateway.label == "RFX GATEWAY"
    assert gateway.battery_pct == 70
    assert gateway.wifi_dbm == -52
    assert gateway.last_seen == datetime(2026, 9, 21, 11, 23, 7, 637000, tzinfo=UTC)


def test_unplugged_pit_probe_has_no_temperature() -> None:
    """Real data: status "NO PROBE" still carried a stale value of "154"."""
    gateway = idle_gateway()
    assert gateway.pit is not None
    assert gateway.pit.celsius is None


def test_disconnected_fan_is_mapped() -> None:
    """Real data: a disconnected Billows still reports set_temp 570."""
    gateway = idle_gateway()
    assert gateway.fan is not None
    assert gateway.fan.connected is False
    assert gateway.fan.set_temp_celsius == pytest.approx(f_to_c(570))
    assert gateway.pit_deviation() is None


def test_probe_sensors_converted_to_celsius_in_order() -> None:
    probe = idle_snapshot().probes[0]
    temps = [s.celsius for s in probe.sensors]
    expected_f = [96.0999984741211, 97.2999954223633, 99.9000015258789, 101.19999694824219]
    assert temps == pytest.approx([f_to_c(f) for f in expected_f])


def test_probe_core_is_coldest_sensor() -> None:
    probe = idle_snapshot().probes[0]
    assert probe.core_celsius() == pytest.approx(f_to_c(96.0999984741211))


def test_probe_details() -> None:
    first, second = idle_snapshot().probes
    assert first.battery_pct == 100
    assert second.battery_pct == 10
    assert second.last_seen == datetime(2026, 9, 5, 21, 14, 34, 639000, tzinfo=UTC)


def test_sensor_time_is_when_value_was_saved() -> None:
    sensor = idle_snapshot().probes[0].sensors[0]
    assert sensor.taken_at == datetime(2026, 9, 21, 11, 23, 35, 127000, tzinfo=UTC)


def test_old_capture_is_stale() -> None:
    assert idle_snapshot().is_stale(NOW, timedelta(minutes=5))


# ----------------------------------------------------- awkward data ---


def gateway_device(**changes: Any) -> dict[str, Any]:
    """A minimal raw Gateway device, with any fields overridden."""
    device: dict[str, Any] = {
        "serial": "G1",
        "device_name": "rfx gateway",
        "label": "RFX GATEWAY",
        "battery": 50,
        "wifi_strength": -60,
        "last_seen": "2026-10-04 11:59:00+00:00",
        "fan": None,
    }
    device.update(changes)
    return device


def channel(number: str, value: Any, status: str = "NORMAL") -> dict[str, Any]:
    """A minimal raw channel."""
    return {
        "number": number,
        "value": value,
        "units": "F",
        "status": status,
        "last_telemetry_saved": "2026-10-04 11:59:00+00:00",
    }


def test_pit_text_value_is_converted() -> None:
    raw = {"devices": [gateway_device()], "channels": {"G1": [channel("1", "230")]}}
    gateway = to_snapshot(raw, NOW).gateway
    assert gateway is not None and gateway.pit is not None
    assert gateway.pit.celsius == pytest.approx(110.0)


def test_connected_fan_gives_pit_deviation() -> None:
    fan = {"connected": True, "set_temp": 225, "state": 1}
    raw = {"devices": [gateway_device(fan=fan)], "channels": {"G1": [channel("1", 230)]}}
    gateway = to_snapshot(raw, NOW).gateway
    assert gateway is not None
    assert gateway.pit_deviation() == pytest.approx(f_to_c(230) - f_to_c(225))


def test_channels_are_put_in_number_order() -> None:
    probe = {"serial": "P1", "device_name": "rfx meat", "last_seen": "2026-10-04 11:59:00+00:00"}
    channels = [channel("2", 200), channel("1", 100), channel("3", 300)]
    raw = {"devices": [probe], "channels": {"P1": channels}}
    sensors = to_snapshot(raw, NOW).probes[0].sensors
    assert [s.celsius for s in sensors] == pytest.approx([f_to_c(100), f_to_c(200), f_to_c(300)])


def test_gateway_without_channels_has_no_pit() -> None:
    raw = {"devices": [gateway_device()], "channels": {}}
    gateway = to_snapshot(raw, NOW).gateway
    assert gateway is not None
    assert gateway.pit is None


def test_missing_last_seen_counts_as_stale() -> None:
    """Fail-safe: no time at all must never look fresh."""
    raw = {"devices": [gateway_device(last_seen=None)], "channels": {}}
    snapshot = to_snapshot(raw, NOW)
    assert snapshot.gateway is not None
    assert snapshot.gateway.last_seen == LONG_AGO
    assert snapshot.is_stale(NOW, timedelta(minutes=5))


def test_no_gateway_in_data() -> None:
    snapshot = to_snapshot({"devices": [], "channels": {}}, NOW)
    assert snapshot.gateway is None
    assert snapshot.probes == ()


def test_unknown_device_types_are_ignored() -> None:
    raw = {"devices": [{"serial": "X1", "device_name": "node wifi"}], "channels": {}}
    snapshot = to_snapshot(raw, NOW)
    assert snapshot.gateway is None
    assert snapshot.probes == ()


# ---------------------------------------------------------- helpers ---


@pytest.mark.parametrize(
    ("value", "units", "expected"),
    [
        (212, "F", 100.0),
        ("212", "F", 100.0),
        (100.0, "C", 100.0),
    ],
)
def test_to_celsius(value: Any, units: str, expected: float) -> None:
    assert to_celsius(value, units) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "abc", True])
def test_to_celsius_unreadable_gives_none(value: Any) -> None:
    assert to_celsius(value, "F") is None


def test_parse_time_formats() -> None:
    expected = datetime(2026, 9, 21, 11, 22, 22, 537000, tzinfo=UTC)
    assert parse_time("2026-09-21 11:22:22.537000+00:00") == expected
    assert parse_time("2026-09-21T11:22:22.537Z") == expected


@pytest.mark.parametrize("value", [None, "", "not a time", 12345])
def test_parse_time_bad_values_give_none(value: Any) -> None:
    assert parse_time(value) is None


def test_parse_time_without_timezone_assumes_utc() -> None:
    assert parse_time("2026-09-21 11:22:22") == datetime(2026, 9, 21, 11, 22, 22, tzinfo=UTC)
