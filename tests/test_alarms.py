"""Tests for the alarm rules: what should be going off, given one Snapshot."""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smoker_monitor.domain.alarms import Alarm, AlarmKind, AlarmRules, evaluate
from smoker_monitor.domain.models import (
    AlarmLimit,
    AlarmSettings,
    Gateway,
    Probe,
    Reading,
    Snapshot,
)
from smoker_monitor.sources.eti_cloud import to_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "eti_idle.json"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

# Every temperature alarm switched on, so each test only has to think about limits.
ALL_ON = AlarmRules(probe_low=True)


def limit(celsius: float | None, enabled: bool = True, alarming: bool = False) -> AlarmLimit:
    """Shorthand for one alarm limit."""
    return AlarmLimit(enabled=enabled, celsius=celsius, alarming=alarming)


def gateway(
    pit_celsius: float | None,
    high: AlarmLimit | None = None,
    low: AlarmLimit | None = None,
) -> Gateway:
    """A Gateway with the given pit temperature and pit alarm limits."""
    return Gateway(
        serial="G1",
        label="RFX GATEWAY",
        battery_pct=80,
        wifi_dbm=-50,
        last_seen=NOW,
        pit=Reading(celsius=pit_celsius, taken_at=NOW),
        fan=None,
        pit_alarms=AlarmSettings(high=high, low=low),
    )


def probe(
    *sensor_celsius: float | None,
    high: AlarmLimit | None = None,
    low: AlarmLimit | None = None,
    serial: str = "P1",
) -> Probe:
    """A probe with the given sensor temperatures (its core is the lowest)."""
    return Probe(
        serial=serial,
        label="RFX MEAT",
        battery_pct=100,
        last_seen=NOW,
        sensors=tuple(Reading(celsius=c, taken_at=NOW) for c in sensor_celsius),
        alarms=AlarmSettings(high=high, low=low),
    )


def check(
    g: Gateway | None = None,
    *probes: Probe,
    rules: AlarmRules = ALL_ON,
    custom: dict[str, AlarmSettings] | None = None,
) -> tuple[Alarm, ...]:
    """Run evaluate on a Snapshot built from the given devices."""
    snapshot = Snapshot(taken_at=NOW, gateway=g, probes=probes)
    return evaluate(snapshot, NOW, rules, custom)


def kinds(alarms: tuple[Alarm, ...]) -> list[AlarmKind]:
    return [a.kind for a in alarms]


# ------------------------------------------------------------------- pit ---


def test_pit_over_high_limit_alarms() -> None:
    alarms = check(gateway(175.0, high=limit(170.0)))
    assert alarms == (Alarm(AlarmKind.PIT_HIGH, "G1", 175.0, 170.0),)


def test_pit_under_low_limit_alarms() -> None:
    """The fire is going out."""
    alarms = check(gateway(25.0, low=limit(30.0)))
    assert alarms == (Alarm(AlarmKind.PIT_LOW, "G1", 25.0, 30.0),)


def test_pit_within_limits_is_fine() -> None:
    assert check(gateway(110.0, high=limit(170.0), low=limit(30.0))) == ()


def test_no_gateway_means_no_pit_alarms() -> None:
    assert check(None) == ()


# ---------------------------------------------------------------- probes ---


@pytest.mark.parametrize("core", [95.0, 96.5])
def test_probe_reaching_target_alarms(core: float) -> None:
    """Reaching the target includes being exactly equal to it."""
    alarms = check(None, probe(core, core + 2, high=limit(95.0)))
    assert alarms == (Alarm(AlarmKind.PROBE_HIGH, "P1", core, 95.0),)


def test_probe_just_under_target_is_fine() -> None:
    assert check(None, probe(94.9, 96.0, high=limit(95.0))) == ()


def test_probe_uses_its_core_the_coldest_sensor() -> None:
    """One sensor past the target isn't enough: the coldest point must reach it."""
    assert check(None, probe(90.0, 100.0, 101.0, high=limit(95.0))) == ()


def test_probe_low_alarm() -> None:
    alarms = check(None, probe(2.0, 3.0, low=limit(4.0)))
    assert kinds(alarms) == [AlarmKind.PROBE_LOW]


def test_each_probe_is_checked() -> None:
    done = probe(96.0, high=limit(95.0), serial="P1")
    not_yet = probe(80.0, high=limit(95.0), serial="P2")
    alarms = check(None, done, not_yet)
    assert [a.device for a in alarms] == ["P1"]


# ------------------------------------------------------------ ETI's flag ---


def test_eti_flag_alarms_even_when_comparison_says_no() -> None:
    """ETI may know something we don't (e.g. it saw a newer reading)."""
    alarms = check(None, probe(90.0, high=limit(95.0, alarming=True)))
    assert kinds(alarms) == [AlarmKind.PROBE_HIGH]


def test_switched_off_rule_ignores_eti_flag() -> None:
    rules = AlarmRules(pit_high=False)
    alarms = check(gateway(200.0, high=limit(170.0, alarming=True)), rules=rules)
    assert alarms == ()


def test_default_rules_ignore_probe_low() -> None:
    """probe_low is off by default in [alarms]."""
    alarms = check(None, probe(2.0, low=limit(4.0, alarming=True)), rules=AlarmRules())
    assert alarms == ()


def test_disabled_limit_never_alarms() -> None:
    assert check(gateway(200.0, high=limit(170.0, enabled=False))) == ()


def test_unreadable_limit_does_not_alarm_by_itself() -> None:
    """A garbled limit is a data problem, not a cooking one."""
    assert check(None, probe(96.0, high=limit(None))) == ()


def test_unreadable_limit_still_follows_eti_flag() -> None:
    alarms = check(None, probe(96.0, high=limit(None, alarming=True)))
    assert alarms == (Alarm(AlarmKind.PROBE_HIGH, "P1", 96.0, None),)


def test_no_temperature_means_no_temperature_alarm() -> None:
    """Missing data is the job of the timeout rules, not these."""
    alarms = check(gateway(None, low=limit(30.0)), probe(None, None, high=limit(95.0)))
    assert alarms == ()


# --------------------------------------------------------- custom limits ---


def test_custom_limit_replaces_etis() -> None:
    """Target moved up on the Pi: ETI's 95°C (and its flag) no longer count."""
    eti_says_done = probe(97.0, high=limit(95.0, alarming=True))
    custom = {"P1": AlarmSettings(high=limit(100.0), low=None)}
    assert check(None, eti_says_done, custom=custom) == ()


def test_custom_limit_alarms_at_its_own_value() -> None:
    """Target moved down on the Pi: alarms before ETI would."""
    custom = {"P1": AlarmSettings(high=limit(92.0), low=None)}
    alarms = check(None, probe(93.0, high=limit(95.0)), custom=custom)
    assert alarms == (Alarm(AlarmKind.PROBE_HIGH, "P1", 93.0, 92.0),)


def test_custom_disabled_limit_silences_etis() -> None:
    custom = {"G1": AlarmSettings(high=limit(None, enabled=False), low=None)}
    alarms = check(gateway(200.0, high=limit(170.0, alarming=True)), custom=custom)
    assert alarms == ()


def test_custom_limit_only_replaces_its_own_side() -> None:
    """A custom high leaves ETI's low in charge."""
    custom = {"G1": AlarmSettings(high=limit(250.0), low=None)}
    alarms = check(gateway(25.0, high=limit(170.0), low=limit(30.0)), custom=custom)
    assert kinds(alarms) == [AlarmKind.PIT_LOW]


def test_custom_limits_for_other_devices_are_ignored() -> None:
    custom = {"SOMEONE-ELSE": AlarmSettings(high=limit(1.0), low=None)}
    assert check(None, probe(50.0, high=limit(95.0)), custom=custom) == ()


# --------------------------------------------------------- the real data ---


def test_idle_capture_has_no_alarms() -> None:
    """Real data, kit switched off: pit alarms on but no air probe, probe alarms off."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert evaluate(to_snapshot(raw, NOW), NOW, AlarmRules()) == ()


def test_alarm_kinds_match_config_switch_names() -> None:
    """Each kind has an [alarms] switch of the same name, so they can't drift apart."""
    switches = {f.name for f in fields(AlarmRules)}
    assert {kind.value for kind in AlarmKind} <= switches
