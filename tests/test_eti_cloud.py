"""Tests for the ETI Cloud source: mapping raw data, and the fetch wrapper.

Most tests use tests/fixtures/eti_idle.json: a real, redacted capture taken
with the kit switched off (air probe unplugged, Billows disconnected).
No test here touches the network, except the one marked `live`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.config import EtiCloudSettings, load_config
from smoker_monitor.domain.models import NO_ALARMS, Gateway, Snapshot
from smoker_monitor.domain.units import f_to_c
from smoker_monitor.sources import eti_cloud
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.eti_cloud import (
    LONG_AGO,
    EtiCloudSource,
    fetch_channels,
    parse_time,
    to_alarm_limit,
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


def test_pit_alarms_come_from_eti() -> None:
    """Real data: the pit had both alarms switched on in the app (338°F / 86°F)."""
    alarms = idle_gateway().pit_alarms
    assert alarms.high is not None
    assert alarms.high.enabled is True
    assert alarms.high.celsius == pytest.approx(170.0)
    assert alarms.high.alarming is False
    assert alarms.low is not None
    assert alarms.low.enabled is True
    assert alarms.low.celsius == pytest.approx(30.0)
    assert alarms.low.alarming is False


def test_probe_alarms_come_from_eti() -> None:
    """Real data: the probe alarms existed (165°F / 32°F) but were switched off."""
    alarms = idle_snapshot().probes[0].alarms
    assert alarms.high is not None
    assert alarms.high.enabled is False
    assert alarms.high.celsius == pytest.approx(f_to_c(165))
    assert alarms.low is not None
    assert alarms.low.enabled is False
    assert alarms.low.celsius == pytest.approx(0.0)


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


def alarm(value: Any, enabled: Any = True, alarming: Any = False) -> dict[str, Any]:
    """A minimal raw alarm block, as found in a channel's alarm_high or alarm_low."""
    return {"enabled": enabled, "alarming": alarming, "value": value, "units": "F"}


def probe_with_channels(*channels: dict[str, Any]) -> dict[str, Any]:
    """Raw data holding one probe (P1) with the given channels."""
    probe = {"serial": "P1", "device_name": "rfx meat", "last_seen": "2026-10-04 11:59:00+00:00"}
    return {"devices": [probe], "channels": {"P1": list(channels)}}


def test_alarming_flag_is_read() -> None:
    raw = probe_with_channels({**channel("1", 230), "alarm_high": alarm(225, alarming=True)})
    high = to_snapshot(raw, NOW).probes[0].alarms.high
    assert high is not None
    assert high.alarming is True


def test_probe_alarms_fall_back_to_channel_one() -> None:
    """Data without a channel 0 (older captures) uses channel 1's alarms, in any order."""
    raw = probe_with_channels(
        {**channel("2", 100), "alarm_high": alarm(250)},
        {**channel("1", 100), "alarm_high": alarm(200)},
    )
    high = to_snapshot(raw, NOW).probes[0].alarms.high
    assert high is not None
    assert high.celsius == pytest.approx(f_to_c(200))


def whole_probe_channel(high: dict[str, Any]) -> dict[str, Any]:
    """A raw channel 0, as seen live: the app's alarm in °C, the reading in °F."""
    return {**channel("0", 75.9), "status": "HIGH", "alarm_high": high}


def test_probe_alarms_come_from_whole_probe_channel() -> None:
    """Real data: the app saves probe alarms on channel 0, not on the sensors."""
    app_alarm = {"enabled": True, "alarming": True, "value": 20, "units": "C"}
    raw = probe_with_channels(
        {**channel("1", 75.9), "alarm_high": alarm(165, enabled=False)},
        whole_probe_channel(app_alarm),
    )
    high = to_snapshot(raw, NOW).probes[0].alarms.high
    assert high is not None
    assert high.enabled is True
    assert high.alarming is True
    assert high.celsius == pytest.approx(20.0)


def test_whole_probe_channel_is_not_a_sensor() -> None:
    """Channel 0 summarises the sensors, so it must not be counted as a fifth one."""
    app_alarm = {"enabled": False, "alarming": False, "value": 0, "units": "C"}
    sensors = [channel(str(n), 100 + n) for n in range(1, 5)]
    raw = probe_with_channels(whole_probe_channel(app_alarm), *sensors)
    readings = to_snapshot(raw, NOW).probes[0].sensors
    assert [r.celsius for r in readings] == pytest.approx([f_to_c(100 + n) for n in range(1, 5)])


def test_channel_without_alarms_gives_none() -> None:
    raw = probe_with_channels(channel("1", 100))
    assert to_snapshot(raw, NOW).probes[0].alarms == NO_ALARMS


def test_probe_without_channels_has_no_alarms() -> None:
    assert to_snapshot(probe_with_channels(), NOW).probes[0].alarms == NO_ALARMS


def test_gateway_without_channels_has_no_pit_alarms() -> None:
    raw = {"devices": [gateway_device()], "channels": {}}
    gateway = to_snapshot(raw, NOW).gateway
    assert gateway is not None
    assert gateway.pit_alarms == NO_ALARMS


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


def test_alarm_limit_in_celsius() -> None:
    limit = to_alarm_limit({"enabled": True, "value": 95, "units": "C"})
    assert limit is not None
    assert limit.celsius == pytest.approx(95.0)


def test_unreadable_alarm_value_keeps_alarm_on() -> None:
    """Fail-safe: an alarm you switched on stays on, even if its limit is garbled."""
    limit = to_alarm_limit(alarm("abc"))
    assert limit is not None
    assert limit.enabled is True
    assert limit.celsius is None


@pytest.mark.parametrize("enabled", [None, "true", 1, False])
def test_alarm_only_enabled_by_real_true(enabled: Any) -> None:
    """Only a real true switches an alarm on; anything odd counts as off."""
    limit = to_alarm_limit(alarm(225, enabled=enabled))
    assert limit is not None
    assert limit.enabled is False


@pytest.mark.parametrize("raw", [None, "on", 225, []])
def test_alarm_limit_from_junk_is_none(raw: Any) -> None:
    assert to_alarm_limit(raw) is None


def test_parse_time_formats() -> None:
    expected = datetime(2026, 9, 21, 11, 22, 22, 537000, tzinfo=UTC)
    assert parse_time("2026-09-21 11:22:22.537000+00:00") == expected
    assert parse_time("2026-09-21T11:22:22.537Z") == expected


@pytest.mark.parametrize("value", [None, "", "not a time", 12345])
def test_parse_time_bad_values_give_none(value: Any) -> None:
    assert parse_time(value) is None


def test_parse_time_without_timezone_assumes_utc() -> None:
    assert parse_time("2026-09-21 11:22:22") == datetime(2026, 9, 21, 11, 22, 22, tzinfo=UTC)


# ------------------------------------------------------ EtiCloudSource ---

SETTINGS = EtiCloudSettings(
    email="me@example.com",
    password="s3cret-pw",
    api_key="test-api-key",
    app_id="test-app-id",
    referer="https://cloud.etiltd.com/",
)


async def test_source_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch() maps whatever fetch_raw downloads into a Snapshot."""

    async def fake_fetch_raw(settings: EtiCloudSettings) -> dict[str, Any]:
        return load_raw()

    monkeypatch.setattr(eti_cloud, "fetch_raw", fake_fetch_raw)
    snapshot = await EtiCloudSource(SETTINGS).fetch()
    assert snapshot.gateway is not None
    assert len(snapshot.probes) == 2


async def test_source_wraps_errors_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any failure becomes a SourceError, and the password is never echoed."""

    async def failing_fetch_raw(settings: EtiCloudSettings) -> dict[str, Any]:
        raise RuntimeError("login failed for password s3cret-pw")

    monkeypatch.setattr(eti_cloud, "fetch_raw", failing_fetch_raw)
    with pytest.raises(SourceError) as exc:
        await EtiCloudSource(SETTINGS).fetch()
    assert "login failed" in str(exc.value)
    assert "s3cret-pw" not in str(exc.value)


# ----------------------------------------------------- fetching channels ---


class MissingChannel(Exception):
    """Stands in for the library's ResourceNotFoundError."""


@dataclass
class FakeChannel:
    """Stands in for the library's channel object (fetch_channels calls asdict on it)."""

    number: str


class FakeCloud:
    """Stands in for ThermoworksCloud: knows which channel numbers each device has."""

    def __init__(self, channels: dict[str, set[str]]) -> None:
        self.channels = channels
        self.asked_for: list[str] = []

    async def get_device_channel(self, device_serial: str, channel: str) -> FakeChannel:
        self.asked_for.append(channel)
        if channel not in self.channels[device_serial]:
            raise MissingChannel(channel)
        return FakeChannel(number=channel)


@pytest.fixture
def fake_cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    """A FakeCloud with one probe (channels 0-4) and one Gateway (channel 1 only)."""
    monkeypatch.setattr(eti_cloud, "ResourceNotFoundError", MissingChannel)
    return FakeCloud({"P1": {"0", "1", "2", "3", "4"}, "G1": {"1"}})


async def test_fetch_channels_includes_whole_probe_channel(fake_cloud: FakeCloud) -> None:
    found = await fetch_channels(fake_cloud, "P1")
    assert [c["number"] for c in found] == ["0", "1", "2", "3", "4"]


async def test_fetch_channels_carries_on_without_channel_zero(fake_cloud: FakeCloud) -> None:
    """The Gateway has no channel 0; that must not stop channel 1 being fetched."""
    found = await fetch_channels(fake_cloud, "G1")
    assert [c["number"] for c in found] == ["1"]


async def test_fetch_channels_stops_at_first_missing(fake_cloud: FakeCloud) -> None:
    await fetch_channels(fake_cloud, "P1")
    assert fake_cloud.asked_for == ["0", "1", "2", "3", "4", "5"]


@pytest.mark.live
def test_live_fetch_from_eti_cloud() -> None:
    """Talks to the real ETI Cloud. Run with: SMOKER_LIVE_TESTS=1 uv run pytest"""
    config_path = Path("config.toml")
    if not config_path.exists():
        pytest.skip("needs config.toml with your ETI Cloud login")
    source = EtiCloudSource(load_config(config_path).eti_cloud)
    snapshot = asyncio.run(source.fetch())
    assert snapshot.gateway is not None
