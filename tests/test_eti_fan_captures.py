"""Tests against two real captures taken with the Billows connected and running.

- eti_fan_blowing.json: set to 570 °F, fan blowing, pit far below its low alarm.
- eti_fan_pulsing.json: set to 77 °F, fan pulsing, pit inside its limits.
- eti_fan_482f.json: five minutes later, set to 482 °F, fan blowing. Here the
  pit reading arrived as a number (73.4) rather than text ("73").

They confirm, on real data, that the fan's set temperature is °F, what the fan
state codes mean, and that the alarm rules give the right answer for each.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.domain.alarms import Alarm, AlarmKind, AlarmRules, evaluate
from smoker_monitor.domain.models import FanActivity, Gateway, RingColour, Snapshot
from smoker_monitor.sources.eti_cloud import to_snapshot

FIXTURES = Path(__file__).parent / "fixtures"

# Shortly after each capture was taken, so the Gateway counts as fresh.
BLOWING_AT = datetime(2026, 10, 5, 20, 57, 30, tzinfo=UTC)
PULSING_AT = datetime(2026, 10, 5, 20, 59, 45, tzinfo=UTC)
AT_482F = datetime(2026, 10, 5, 21, 2, 30, tzinfo=UTC)


def load(name: str, now: datetime) -> Snapshot:
    raw: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return to_snapshot(raw, now)


def gateway_of(snapshot: Snapshot) -> Gateway:
    [gateway] = snapshot.gateways
    return gateway


@pytest.fixture
def blowing() -> Snapshot:
    return load("eti_fan_blowing.json", BLOWING_AT)


@pytest.fixture
def pulsing() -> Snapshot:
    return load("eti_fan_pulsing.json", PULSING_AT)


# ------------------------------------------------------------------- fan ---


def test_blowing_capture_fan(blowing: Snapshot) -> None:
    """570 °F showed in the app as 298.9 °C: the set temperature is °F."""
    fan = gateway_of(blowing).fan
    assert fan is not None
    assert fan.connected
    assert fan.set_temp_celsius == pytest.approx(298.9, abs=0.05)
    assert fan.state == 1
    assert fan.activity is FanActivity.BLOWING


def test_pulsing_capture_fan(pulsing: Snapshot) -> None:
    fan = gateway_of(pulsing).fan
    assert fan is not None
    assert fan.set_temp_celsius == pytest.approx(25.0)
    assert fan.state == 2
    assert fan.activity is FanActivity.PULSING


# ------------------------------------------------------------------- pit ---


def test_blowing_capture_pit(blowing: Snapshot) -> None:
    gateway = gateway_of(blowing)
    assert gateway.get_pit_temp() == pytest.approx(22.8, abs=0.05)  # 73 °F
    alarms = gateway.pit_alarms
    assert alarms.high is not None and alarms.low is not None
    assert alarms.high.celsius == pytest.approx(312.8, abs=0.05)  # 595 °F
    assert alarms.low.celsius == pytest.approx(285.0, abs=0.05)  # 545 °F
    assert alarms.low.alarming  # ETI's flag: the pit is far too cold


def test_pulsing_capture_pit(pulsing: Snapshot) -> None:
    alarms = gateway_of(pulsing).pit_alarms
    assert alarms.high is not None and alarms.low is not None
    assert alarms.high.celsius == pytest.approx(38.9, abs=0.05)  # 102 °F
    assert alarms.low.celsius == pytest.approx(11.1, abs=0.05)  # 52 °F
    assert not alarms.high.alarming
    assert not alarms.low.alarming


def test_app_sets_pit_alarms_25f_either_side_of_target(
    blowing: Snapshot, pulsing: Snapshot
) -> None:
    """25 °F is 13.9 °C. Documents what the app does; our code doesn't rely on it."""
    for snapshot in (blowing, pulsing):
        gateway = gateway_of(snapshot)
        assert gateway.fan is not None and gateway.fan.set_temp_celsius is not None
        high, low = gateway.pit_alarms.high, gateway.pit_alarms.low
        assert high is not None and high.celsius is not None
        assert low is not None and low.celsius is not None
        target = gateway.fan.set_temp_celsius
        assert high.celsius - target == pytest.approx(13.9, abs=0.05)
        assert target - low.celsius == pytest.approx(13.9, abs=0.05)


# ---------------------------------------------------------------- alarms ---


def test_blowing_capture_raises_only_pit_too_cold(blowing: Snapshot) -> None:
    """The probes are hours old and weren't part of a cook, so they stay quiet."""
    alarms = evaluate(blowing, BLOWING_AT, AlarmRules())
    assert alarms == (Alarm(AlarmKind.PIT_LOW, "eti:GATEWAY-1", alarms[0].value, 285.0),)
    assert alarms[0].value == pytest.approx(22.8, abs=0.05)


def test_pulsing_capture_raises_nothing(pulsing: Snapshot) -> None:
    assert evaluate(pulsing, PULSING_AT, AlarmRules()) == ()


# ----------------------------------------------------------------- names ---


def test_probe_names_come_from_the_app(blowing: Snapshot) -> None:
    """The app saved "white probe " (with a trailing space) and "pink probe"."""
    assert [probe.name for probe in blowing.probes] == ["white probe", "pink probe"]
    assert [probe.rings for probe in blowing.probes] == [
        (RingColour.WHITE,),
        (RingColour.PINK,),
    ]


# ------------------------------------------------------------ 482 °F capture ---


def test_482f_capture() -> None:
    """Set to 482 °F (250 °C), pit cold: the app moved its alarms to 25 °F either side."""
    snapshot = load("eti_fan_482f.json", AT_482F)
    gateway = gateway_of(snapshot)
    fan = gateway.fan
    assert fan is not None and fan.activity is FanActivity.BLOWING
    assert fan.set_temp_celsius == pytest.approx(250.0)
    assert gateway.get_pit_temp() == pytest.approx(23.0, abs=0.05)  # 73.4 °F, a number
    alarms = gateway.pit_alarms
    assert alarms.high is not None and alarms.low is not None
    assert alarms.high.celsius == pytest.approx(263.9, abs=0.05)  # 507 °F
    assert alarms.low.celsius == pytest.approx(236.1, abs=0.05)  # 457 °F
    assert [a.kind for a in evaluate(snapshot, AT_482F, AlarmRules())] == [AlarmKind.PIT_LOW]
