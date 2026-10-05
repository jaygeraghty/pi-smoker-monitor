"""Tests for the alarm rules: what should be going off, given one Snapshot."""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smoker_monitor.domain.alarms import (
    NO_DEVICE,
    Alarm,
    AlarmKind,
    AlarmRules,
    evaluate,
    fresh_probes,
)
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
    last_seen: datetime = NOW,
    battery_pct: int | None = 80,
) -> Gateway:
    """A Gateway with the given pit temperature and pit alarm limits."""
    return Gateway(
        serial="G1",
        label="RFX GATEWAY",
        battery_pct=battery_pct,
        wifi_dbm=-50,
        last_seen=last_seen,
        pit=Reading(celsius=pit_celsius, taken_at=NOW),
        fan=None,
        pit_alarms=AlarmSettings(high=high, low=low),
    )


def probe(
    *sensor_celsius: float | None,
    high: AlarmLimit | None = None,
    low: AlarmLimit | None = None,
    serial: str = "P1",
    last_seen: datetime = NOW,
    battery_pct: int | None = 100,
) -> Probe:
    """A probe with the given sensor temperatures (its core is the lowest)."""
    return Probe(
        serial=serial,
        label="RFX MEAT",
        battery_pct=battery_pct,
        last_seen=last_seen,
        sensors=tuple(Reading(celsius=c, taken_at=NOW) for c in sensor_celsius),
        alarms=AlarmSettings(high=high, low=low),
    )


# A healthy Gateway with no pit alarms, for tests that are about probes.
CALM = gateway(110.0)


def check(
    g: Gateway | None = None,
    *probes: Probe,
    rules: AlarmRules = ALL_ON,
    custom: dict[str, AlarmSettings] | None = None,
    live: set[str] | None = None,
) -> tuple[Alarm, ...]:
    """Run evaluate on a Snapshot built from the given devices."""
    snapshot = Snapshot(taken_at=NOW, gateway=g, probes=probes)
    return evaluate(snapshot, NOW, rules, custom, live)


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
    """Only the missing-Gateway timeout, never a pit alarm."""
    assert kinds(check(None)) == [AlarmKind.GATEWAY_TIMEOUT]


# ---------------------------------------------------------------- probes ---


@pytest.mark.parametrize("core", [95.0, 96.5])
def test_probe_reaching_target_alarms(core: float) -> None:
    """Reaching the target includes being exactly equal to it."""
    alarms = check(CALM, probe(core, core + 2, high=limit(95.0)))
    assert alarms == (Alarm(AlarmKind.PROBE_HIGH, "P1", core, 95.0),)


def test_probe_just_under_target_is_fine() -> None:
    assert check(CALM, probe(94.9, 96.0, high=limit(95.0))) == ()


def test_probe_uses_its_core_the_coldest_sensor() -> None:
    """One sensor past the target isn't enough: the coldest point must reach it."""
    assert check(CALM, probe(90.0, 100.0, 101.0, high=limit(95.0))) == ()


def test_probe_low_alarm() -> None:
    alarms = check(CALM, probe(2.0, 3.0, low=limit(4.0)))
    assert kinds(alarms) == [AlarmKind.PROBE_LOW]


def test_each_probe_is_checked() -> None:
    done = probe(96.0, high=limit(95.0), serial="P1")
    not_yet = probe(80.0, high=limit(95.0), serial="P2")
    alarms = check(CALM, done, not_yet)
    assert [a.device for a in alarms] == ["P1"]


# ------------------------------------------------------------ ETI's flag ---


def test_eti_flag_alarms_even_when_comparison_says_no() -> None:
    """ETI may know something we don't (e.g. it saw a newer reading)."""
    alarms = check(CALM, probe(90.0, high=limit(95.0, alarming=True)))
    assert kinds(alarms) == [AlarmKind.PROBE_HIGH]


def test_switched_off_rule_ignores_eti_flag() -> None:
    rules = AlarmRules(pit_high=False)
    alarms = check(gateway(200.0, high=limit(170.0, alarming=True)), rules=rules)
    assert alarms == ()


def test_default_rules_ignore_probe_low() -> None:
    """probe_low is off by default in [alarms]."""
    alarms = check(CALM, probe(2.0, low=limit(4.0, alarming=True)), rules=AlarmRules())
    assert alarms == ()


def test_disabled_limit_never_alarms() -> None:
    assert check(gateway(200.0, high=limit(170.0, enabled=False))) == ()


def test_unreadable_limit_does_not_alarm_by_itself() -> None:
    """A garbled limit is a data problem, not a cooking one."""
    assert check(CALM, probe(96.0, high=limit(None))) == ()


def test_unreadable_limit_still_follows_eti_flag() -> None:
    alarms = check(CALM, probe(96.0, high=limit(None, alarming=True)))
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
    assert check(CALM, eti_says_done, custom=custom) == ()


def test_custom_limit_alarms_at_its_own_value() -> None:
    """Target moved down on the Pi: alarms before ETI would."""
    custom = {"P1": AlarmSettings(high=limit(92.0), low=None)}
    alarms = check(CALM, probe(93.0, high=limit(95.0)), custom=custom)
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
    assert check(CALM, probe(50.0, high=limit(95.0)), custom=custom) == ()


# -------------------------------------------------------------- timeouts ---

SIX_MINUTES_AGO = NOW - timedelta(minutes=6)


def test_quiet_gateway_alarms() -> None:
    alarms = check(gateway(110.0, last_seen=SIX_MINUTES_AGO))
    assert alarms == (Alarm(AlarmKind.GATEWAY_TIMEOUT, "G1", 6.0, 5.0),)


def test_gateway_exactly_on_its_timeout_is_still_fresh() -> None:
    assert check(gateway(110.0, last_seen=NOW - timedelta(minutes=5))) == ()


def test_missing_gateway_alarms() -> None:
    """Fail-safe: no Gateway in the data at all counts as gone quiet."""
    alarms = check(None)
    assert alarms == (Alarm(AlarmKind.GATEWAY_TIMEOUT, NO_DEVICE, None, 5.0),)


def test_gateway_timeout_can_be_switched_off() -> None:
    rules = AlarmRules(gateway_timeout=False)
    assert check(gateway(110.0, last_seen=SIX_MINUTES_AGO), rules=rules) == ()
    assert check(CALM, rules=rules) == ()


def test_gateway_timeout_uses_configured_minutes() -> None:
    rules = AlarmRules(gateway_timeout_minutes=10)
    assert check(gateway(110.0, last_seen=SIX_MINUTES_AGO), rules=rules) == ()


def test_quiet_gateway_ignores_its_old_pit_reading() -> None:
    """Old data can't be trusted, so only the timeout alarms, not the pit."""
    quiet_and_hot = gateway(200.0, high=limit(170.0, alarming=True), last_seen=SIX_MINUTES_AGO)
    assert kinds(check(quiet_and_hot)) == [AlarmKind.GATEWAY_TIMEOUT]


def test_live_probe_gone_quiet_alarms() -> None:
    """A probe seen during this cook that stops reporting: e.g. it died at 2am."""
    quiet = probe(70.0, last_seen=SIX_MINUTES_AGO)
    alarms = check(gateway(110.0), quiet, live={"P1"})
    assert alarms == (Alarm(AlarmKind.PROBE_TIMEOUT, "P1", 6.0, 5.0),)


def test_probe_never_live_is_ignored() -> None:
    """A probe left in a drawer for a month must not wake you."""
    in_drawer = probe(20.0, last_seen=NOW - timedelta(days=30), battery_pct=10)
    assert check(gateway(110.0), in_drawer, live=set()) == ()


def test_quiet_probe_ignores_its_old_alarm_flag() -> None:
    """A stale "alarming" flag from a probe's last cook must not wake you."""
    stale = probe(96.0, high=limit(95.0, alarming=True), last_seen=NOW - timedelta(days=30))
    assert check(gateway(110.0), stale) == ()


def test_probe_timeout_can_be_switched_off() -> None:
    quiet = probe(70.0, last_seen=SIX_MINUTES_AGO)
    rules = AlarmRules(probe_timeout=False)
    assert check(gateway(110.0), quiet, rules=rules, live={"P1"}) == ()


def test_fresh_probes_are_the_ones_reporting_now() -> None:
    reporting = probe(70.0, serial="P1")
    in_drawer = probe(20.0, serial="P2", last_seen=NOW - timedelta(days=30))
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=(reporting, in_drawer))
    assert fresh_probes(snapshot, NOW, AlarmRules()) == frozenset({"P1"})


# ------------------------------------------------------------- batteries ---


def test_low_gateway_battery_alarms() -> None:
    alarms = check(gateway(110.0, battery_pct=9))
    assert alarms == (Alarm(AlarmKind.GATEWAY_BATTERY, "G1", 9, 10),)


def test_low_probe_battery_alarms() -> None:
    alarms = check(gateway(110.0), probe(70.0, battery_pct=5))
    assert alarms == (Alarm(AlarmKind.PROBE_BATTERY, "P1", 5, 10),)


def test_battery_exactly_on_threshold_is_fine() -> None:
    """Alarm *below* the threshold: 10% with a 10% threshold doesn't alarm."""
    assert check(gateway(110.0, battery_pct=10), probe(70.0, battery_pct=10)) == ()


def test_battery_threshold_zero_switches_it_off() -> None:
    rules = AlarmRules(gateway_battery_pct=0, probe_battery_pct=0)
    assert check(gateway(110.0, battery_pct=1), probe(70.0, battery_pct=1), rules=rules) == ()


def test_unknown_battery_is_ignored() -> None:
    assert check(gateway(110.0, battery_pct=None), probe(70.0, battery_pct=None)) == ()


# --------------------------------------------------------- the real data ---


def test_idle_capture_only_reports_gateway_timeout() -> None:
    """Real data, kit switched off for weeks: the Gateway's silence is the only alarm.

    Its pit alarms are on and a probe was at 10% battery, but that data is
    old, so it must not set anything else off.
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    alarms = evaluate(to_snapshot(raw, NOW), NOW, AlarmRules())
    assert kinds(alarms) == [AlarmKind.GATEWAY_TIMEOUT]
    assert alarms[0].device == "GATEWAY-1"


def test_alarm_kinds_match_config_setting_names() -> None:
    """Each kind has an [alarms] setting of the same name, so they can't drift apart.

    Battery kinds are set by their percentage (e.g. probe_battery -> probe_battery_pct).
    """
    settings = {f.name for f in fields(AlarmRules)}
    for kind in AlarmKind:
        assert kind.value in settings or f"{kind.value}_pct" in settings, kind
