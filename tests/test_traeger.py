"""Tests for the Traeger source: turning status messages into a Snapshot.

The messages here follow the shape described in sources/traeger.py. Once a
real capture (scripts/capture_traeger.py) is in tests/fixtures, tests against
it will be added too.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.config import TraegerSettings
from smoker_monitor.domain.alarms import AlarmRules, evaluate
from smoker_monitor.domain.models import GrillMode, Snapshot
from smoker_monitor.sources import traeger
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.traeger import (
    TraegerSource,
    friendly_name,
    percent,
    status_time,
    to_celsius,
    to_snapshot,
)
from smoker_monitor.sources.traeger_cloud import GrillMessage, TraegerCloud

SETTINGS = TraegerSettings("grill@example.com", "grill-pw", "client", poll_seconds=30)
RECEIVED = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
STARTED = datetime(2026, 10, 30, 17, 0, tzinfo=UTC)
GRILLS = [{"thingName": "GRILL-1", "friendlyName": "  My   Traeger ", "deviceTypeId": "2104"}]

COOKING: dict[str, Any] = {
    "status": {
        "grill": 225,
        "set": 225,
        "units": 1,
        "system_status": 6,
        "connected": True,
        "pellet_level": 70,
        "acc": [
            {
                "type": "probe",
                "uuid": "p1",
                "con": 1,
                "probe": {"get_temp": 140, "set_temp": 203, "alarm_fired": False},
            },
            {"type": "probe", "uuid": "p2", "con": 0, "probe": {"get_temp": 70}},
        ],
        "ambient": 50,
        "keepwarm": 0,
        "smoke": 1,
    },
    "features": {"pellet_sensor_connected": 1},
    "settings": {"rssi": -61},
}


def message(payload: dict[str, Any], thing: str = "GRILL-1") -> GrillMessage:
    return GrillMessage(thing=thing, payload=payload, received_at=RECEIVED.timestamp())


def cooking_snapshot(payload: dict[str, Any] = COOKING) -> Any:
    return to_snapshot(GRILLS, {"GRILL-1": message(payload)}, STARTED)


# ---------------------------------------------------------------- mapping ---


def test_cooking_grill() -> None:
    [grill] = cooking_snapshot().grills
    assert grill.id == "traeger:GRILL-1"
    assert grill.label == "Pro 575"
    assert grill.name == "My Traeger"
    assert grill.mode is GrillMode.COOKING
    assert grill.connected
    assert grill.celsius() == 107.2  # 225 °F
    assert grill.set_temp_celsius == 107.2
    assert grill.pellet_pct == 70
    assert grill.last_seen == RECEIVED


def test_only_plugged_in_probes_are_included() -> None:
    [probe] = cooking_snapshot().probes
    assert probe.id == "traeger:GRILL-1-p1"
    assert probe.core_celsius() == 60.0  # 140 °F
    assert probe.alarms.high is not None
    assert probe.alarms.high.celsius == 95.0  # its target, 203 °F
    assert probe.battery_pct is None  # wired


def test_celsius_grill_is_not_converted() -> None:
    status = {**COOKING["status"], "units": 0, "grill": 110, "set": 110}
    [grill] = cooking_snapshot({"status": status}).grills
    assert (grill.celsius(), grill.set_temp_celsius) == (110.0, 110.0)


def test_older_built_in_probe() -> None:
    status = {"grill": 225, "units": 1, "probe": 150, "probe_set": 0, "probe_con": 1}
    [probe] = cooking_snapshot({"status": status}).probes
    assert probe.id == "traeger:GRILL-1-probe"
    assert probe.alarms.high is None  # no target set


def test_fired_probe_alarm_is_marked_alarming() -> None:
    acc = [
        {
            "type": "probe",
            "uuid": "p1",
            "con": 1,
            "probe": {"get_temp": 205, "set_temp": 203, "alarm_fired": True},
        }
    ]
    [probe] = cooking_snapshot({"status": {**COOKING["status"], "acc": acc}}).probes
    assert probe.alarms.high is not None and probe.alarms.high.alarming


def test_grill_not_heard_from_is_offline() -> None:
    [grill] = to_snapshot(GRILLS, {}, STARTED).grills
    assert grill.mode is GrillMode.OFFLINE
    assert not grill.connected
    assert grill.temp is None
    assert grill.last_seen == STARTED


@pytest.mark.parametrize(
    ("code", "mode"),
    [
        (2, GrillMode.SLEEPING),
        (4, GrillMode.IGNITING),
        (5, GrillMode.PREHEATING),
        (7, GrillMode.COOKING),
        (8, GrillMode.COOLING_DOWN),
        (9, GrillMode.SHUT_DOWN),
        (99, GrillMode.OFFLINE),
        (42, GrillMode.UNKNOWN),
        ("6", GrillMode.UNKNOWN),
    ],
)
def test_grill_modes(code: object, mode: GrillMode) -> None:
    [grill] = cooking_snapshot({"status": {"system_status": code}}).grills
    assert grill.mode is mode


def test_messy_message_doesnt_crash() -> None:
    [grill] = cooking_snapshot({"status": "nonsense"}).grills
    assert grill.mode is GrillMode.UNKNOWN
    assert grill.celsius() is None
    assert cooking_snapshot({"status": {"acc": "nonsense"}}).probes == ()


def test_set_temperature_zero_means_none() -> None:
    [grill] = cooking_snapshot({"status": {"set": 0, "units": 1}}).grills
    assert grill.set_temp_celsius is None


@pytest.mark.parametrize(
    ("value", "result"), [(225, 107.2), ("225", None), (True, None), (None, None)]
)
def test_to_celsius(value: object, result: float | None) -> None:
    assert to_celsius(value, in_fahrenheit=True) == result


@pytest.mark.parametrize(
    ("value", "result"), [(70, 70), (0, 0), (-1, None), (101, None), ("70", None)]
)
def test_percent(value: object, result: int | None) -> None:
    assert percent(value) == result


def test_friendly_name_is_tidied() -> None:
    assert friendly_name({"friendlyName": "x" * 30}) == "x" * 20
    assert friendly_name({"friendlyName": "   "}) is None
    assert friendly_name({}) is None


# ------------------------------------------------------------- the source ---


class FakeCloud(TraegerCloud):
    """Stands in for the real cloud: no network, prepared messages."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        fail: Exception | None = None,
        delay: float = 0,
        listen_failures: int = 0,
    ) -> None:
        super().__init__(SETTINGS)
        self.messages = messages
        self.fail = fail
        self.delay = delay  # seconds before the first message arrives
        self.listen_failures = listen_failures  # connections that fail before one works
        self.listens = 0
        self.closed = False

    async def grills(self) -> list[dict[str, Any]]:
        if self.fail is not None:
            raise self.fail
        return GRILLS

    async def listen(
        self,
        things: list[str],
        on_message: Callable[[GrillMessage], None],
        seconds: float | None = None,
    ) -> None:
        self.listens += 1
        if self.listens <= self.listen_failures:
            raise SourceError("Lost the connection to Traeger (test)")
        await asyncio.sleep(self.delay)
        for payload in self.messages:
            on_message(message(payload))
        await asyncio.Event().wait()  # then stays connected

    async def close(self) -> None:
        self.closed = True


async def test_source_returns_the_latest_status() -> None:
    cloud = FakeCloud([{"status": {"grill": 100, "units": 1}}, COOKING])
    source = TraegerSource(SETTINGS, cloud)
    await source.fetch()  # starts listening
    await asyncio.sleep(0)  # let the listener hand over its messages
    [grill] = (await source.fetch()).grills
    assert grill.mode is GrillMode.COOKING
    await source.close()
    assert cloud.closed


async def test_first_fetch_waits_for_the_first_message() -> None:
    """Like `smoker snapshot`: one fetch straight after connecting."""
    source = TraegerSource(SETTINGS, FakeCloud([COOKING], delay=0.05))
    [grill] = (await source.fetch()).grills
    assert grill.mode is GrillMode.COOKING
    await source.close()


async def test_silent_grill_is_offline_after_a_short_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(traeger, "FIRST_MESSAGE_SECONDS", 0.05)
    source = TraegerSource(SETTINGS, FakeCloud([]))
    [grill] = (await source.fetch()).grills
    assert grill.mode is GrillMode.OFFLINE
    await source.close()


async def test_silent_grill_only_delays_the_first_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grill that never answers mustn't slow down every later poll."""
    monkeypatch.setattr(traeger, "FIRST_MESSAGE_SECONDS", 0.2)
    source = TraegerSource(SETTINGS, FakeCloud([]))
    await source.fetch()  # waits once
    loop = asyncio.get_running_loop()
    started = loop.time()
    await source.fetch()
    await source.fetch()
    assert loop.time() - started < 0.1
    await source.close()


async def test_failing_connection_keeps_failing_until_a_message_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login works but the live connection doesn't: every poll must fail, so
    "Can't reach Traeger" can build up its minutes, not flip back and forth."""
    monkeypatch.setattr(traeger, "RECONNECT_SECONDS", 0.01)
    cloud = FakeCloud([COOKING], listen_failures=3)
    source = TraegerSource(SETTINGS, cloud)
    with pytest.raises(SourceError, match="Lost the connection"):
        await source.fetch()
    await asyncio.sleep(0.005)  # between retries
    with pytest.raises(SourceError, match="Lost the connection"):
        await source.fetch()
    await asyncio.sleep(0.1)  # the fourth attempt works and a message arrives
    [grill] = (await source.fetch()).grills
    assert grill.mode is GrillMode.COOKING
    assert cloud.listens == 4
    await source.close()


async def test_source_login_failure_is_a_source_error() -> None:
    source = TraegerSource(SETTINGS, FakeCloud([], fail=SourceError("Traeger login failed")))
    with pytest.raises(SourceError, match="login failed"):
        await source.fetch()
    await source.close()


async def test_source_bug_is_a_source_error() -> None:
    source = TraegerSource(SETTINGS, FakeCloud([], fail=RuntimeError("bug")))
    with pytest.raises(SourceError, match="unexpectedly"):
        await source.fetch()
    await source.close()


def test_source_identity() -> None:
    source = TraegerSource(SETTINGS, FakeCloud([]))
    assert (source.key, source.name, source.expects_gateway, source.poll_seconds) == (
        "traeger",
        "Traeger",
        False,
        30,
    )


# ---------------------------------------------------------- real capture ---

FIXTURE = Path(__file__).parent / "fixtures" / "traeger_off.json"


def real_capture() -> Snapshot:
    """The real (redacted) capture: a Pro 575, switched off since 5 September."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    latest = {
        m["topic_thing"]: GrillMessage(m["topic_thing"], m["payload"], RECEIVED.timestamp())
        for m in raw["messages"]
    }
    return to_snapshot(raw["grills"], latest, STARTED)


def test_real_capture_grill() -> None:
    [grill] = real_capture().grills
    assert grill.id == "traeger:GRILL-1"
    assert (grill.label, grill.name) == ("Pro 575", "My Traeger")
    assert grill.mode is GrillMode.OFFLINE
    assert not grill.connected
    assert grill.celsius() == 16.0  # units 0: already °C
    assert grill.set_temp_celsius == 75.0
    assert grill.pellet_pct == 30


def test_real_capture_is_dated_by_the_grill_not_by_arrival() -> None:
    """The cloud resent a status from 5 September; it must not look fresh."""
    [grill] = real_capture().grills
    assert grill.last_seen == datetime(2026, 9, 5, 18, 55, 14, tzinfo=UTC)


def test_real_capture_has_no_probe_plugged_in() -> None:
    assert real_capture().probes == ()


def test_real_capture_raises_no_alarms() -> None:
    """Off for weeks, pellets at 30%: nothing to wake anyone for."""
    snapshot = real_capture()
    assert evaluate(snapshot, RECEIVED, AlarmRules()) == ()


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        (RECEIVED.timestamp() - 60, RECEIVED - timedelta(seconds=60)),
        (RECEIVED.timestamp() + 3600, RECEIVED),  # from the future: capped
        (RECEIVED.timestamp() + 120, RECEIVED),  # a clock running a bit fast: capped
        (None, RECEIVED),
        ("1788634514", RECEIVED),
    ],
)
def test_status_time(stamp: object, expected: datetime) -> None:
    assert status_time({"time": stamp}, RECEIVED.timestamp()) == expected


def test_older_probe_alarm_flag_is_read() -> None:
    status = {"units": 0, "probe": 96, "probe_set": 95, "probe_con": 1, "probe_alarm_fired": 1}
    [probe] = cooking_snapshot({"status": status}).probes
    assert probe.alarms.high is not None and probe.alarms.high.alarming


# ----------------------------------------------------------------- extras ---


def test_cooking_grill_extras() -> None:
    [grill] = cooking_snapshot().grills
    assert grill.ambient_celsius == 10.0  # 50 °F
    assert grill.wifi_dbm == -61
    assert grill.super_smoke and not grill.keep_warm


def test_pellets_ignored_without_a_pellet_sensor() -> None:
    """Without the sensor, pellet_level could read 0 and set off "pellets low"."""
    no_sensor = {**COOKING, "features": {"pellet_sensor_connected": 0}}
    [grill] = cooking_snapshot(no_sensor).grills
    assert grill.pellet_pct is None
    no_features = {"status": COOKING["status"]}
    [grill] = cooking_snapshot(no_features).grills
    assert grill.pellet_pct is None


def test_real_capture_extras() -> None:
    [grill] = real_capture().grills
    assert grill.ambient_celsius == 18.0
    assert grill.wifi_dbm == -78
    assert not grill.keep_warm and not grill.super_smoke
