"""Tests for the plain-text snapshot report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smoker_monitor.domain.alarms import NO_DEVICE, Alarm, AlarmKind, AlarmRules, evaluate
from smoker_monitor.domain.models import (
    NO_ALARMS,
    AlarmLimit,
    AlarmSettings,
    Fan,
    Gateway,
    Probe,
    Reading,
    Snapshot,
)
from smoker_monitor.report import (
    describe_alarm,
    format_active_alarms,
    format_age,
    format_alarms,
    format_minutes,
    format_snapshot,
    format_temp,
)
from smoker_monitor.sources.eti_cloud import to_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "eti_idle.json"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def idle_report() -> str:
    """The report for the real (redacted) capture."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return format_snapshot(to_snapshot(raw, NOW), NOW)


def live_gateway(pit_celsius: float, fan: Fan | None) -> Gateway:
    """A Gateway seen one minute before NOW."""
    seen = NOW - timedelta(minutes=1)
    return Gateway(
        serial="G1",
        label="RFX GATEWAY",
        battery_pct=80,
        wifi_dbm=-50,
        last_seen=seen,
        pit=Reading(celsius=pit_celsius, taken_at=seen),
        fan=fan,
    )


def test_idle_report_lists_every_device() -> None:
    report = idle_report()
    assert "GATEWAY-1" in report
    assert "PROBE-1" in report
    assert "PROBE-2" in report


def test_idle_report_explains_missing_pit_probe() -> None:
    assert "Pit:   no reading" in idle_report()


def test_idle_report_shows_fan_not_connected() -> None:
    assert "Fan:   not connected" in idle_report()


def test_idle_report_shows_probe_temps_in_celsius() -> None:
    # PROBE-1's coldest sensor was 96.1°F, which is 35.6°C.
    assert "Core:    35.6°C" in idle_report()


def test_idle_report_with_its_alarms_shows_gateway_silent() -> None:
    """Real data, kit off for weeks: the alarm block leads the report."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot = to_snapshot(raw, NOW)
    report = format_snapshot(snapshot, NOW, evaluate(snapshot, NOW, AlarmRules()))
    assert report.startswith("*** 1 ALARM ***\n  Gateway silent for ")


def test_no_alarms_means_no_alarm_block() -> None:
    snapshot = Snapshot(taken_at=NOW, gateway=live_gateway(107.0, None), probes=())
    assert "ALARM" not in format_snapshot(snapshot, NOW, ())


def test_connected_fan_shows_set_temp_and_deviation() -> None:
    fan = Fan(set_temp_celsius=107.0, state=1, connected=True)
    snapshot = Snapshot(taken_at=NOW, gateway=live_gateway(110.0, fan), probes=())
    report = format_snapshot(snapshot, NOW)
    assert "set to 107.0°C" in report
    assert "+3.0°C from the set temperature" in report


def test_missing_gateway_is_explained() -> None:
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=())
    report = format_snapshot(snapshot, NOW)
    assert "No RFX Gateway found" in report


def test_idle_report_shows_pit_alarms() -> None:
    """Shown even though the air probe is unplugged: you still want to see what's set."""
    assert "Alarm: high 170.0°C, low 30.0°C" in idle_report()


def test_idle_report_shows_probe_alarms_off() -> None:
    assert "Alarms:  off" in idle_report()


def test_format_alarms_none_set() -> None:
    assert format_alarms(NO_ALARMS) == "off"


def test_format_alarms_hides_switched_off_limits() -> None:
    alarms = AlarmSettings(
        high=AlarmLimit(enabled=False, celsius=74.0),
        low=AlarmLimit(enabled=True, celsius=30.0),
    )
    assert format_alarms(alarms) == "low 30.0°C"


def test_format_alarms_marks_alarming() -> None:
    alarms = AlarmSettings(high=AlarmLimit(enabled=True, celsius=95.0, alarming=True), low=None)
    assert format_alarms(alarms) == "high 95.0°C ALARMING"


def test_format_alarms_unreadable_limit_still_shown() -> None:
    alarms = AlarmSettings(high=AlarmLimit(enabled=True, celsius=None), low=None)
    assert format_alarms(alarms) == "high --"


@pytest.mark.parametrize(
    ("age", "words"),
    [
        (timedelta(seconds=-5), "just now"),
        (timedelta(seconds=45), "45 s ago"),
        (timedelta(minutes=5), "5 min ago"),
        (timedelta(hours=3), "3 h ago"),
        (timedelta(days=1), "1 day ago"),
        (timedelta(days=13), "13 days ago"),
    ],
)
def test_format_age(age: timedelta, words: str) -> None:
    assert format_age(age) == words


def test_format_temp() -> None:
    assert format_temp(107.25) == "107.2°C"
    assert format_temp(None) == "--"


# ---------------------------------------------------------- active alarms ---

WHITE_PROBE = Probe(
    serial="P1",
    label="RFX MEAT",
    battery_pct=100,
    last_seen=NOW,
    sensors=(Reading(celsius=96.0, taken_at=NOW),),
)
WITH_PROBE = Snapshot(taken_at=NOW, gateway=None, probes=(WHITE_PROBE,))


@pytest.mark.parametrize(
    ("alarm", "words"),
    [
        (Alarm(AlarmKind.PIT_HIGH, "G1", 182.0, 170.0), "Pit too hot: 182.0°C (limit 170.0°C)"),
        (Alarm(AlarmKind.PIT_LOW, "G1", 25.0, 30.0), "Pit too cold: 25.0°C (limit 30.0°C)"),
        (
            Alarm(AlarmKind.PROBE_HIGH, "P1", 95.2, 95.0),
            "RFX MEAT (P1) reached target: 95.2°C (target 95.0°C)",
        ),
        (Alarm(AlarmKind.PROBE_LOW, "P1", 2.0, 4.0), "RFX MEAT (P1) too cold: 2.0°C (limit 4.0°C)"),
        (
            Alarm(AlarmKind.GATEWAY_TIMEOUT, "G1", 12.7, 5.0),
            "Gateway silent for 12 min (limit 5 min)",
        ),
        (Alarm(AlarmKind.GATEWAY_TIMEOUT, NO_DEVICE, None, 5.0), "No RFX Gateway found"),
        (
            Alarm(AlarmKind.PROBE_TIMEOUT, "P1", 7.0, 5.0),
            "RFX MEAT (P1) silent for 7 min (limit 5 min)",
        ),
        (Alarm(AlarmKind.GATEWAY_BATTERY, "G1", 8, 10), "Gateway battery low: 8% (below 10%)"),
        (Alarm(AlarmKind.PROBE_BATTERY, "P1", 5, 10), "RFX MEAT (P1) battery low: 5% (below 10%)"),
        (
            Alarm(AlarmKind.ETI_UNREACHABLE, NO_DEVICE, 6.0, 5.0),
            "Can't reach ETI Cloud for 6 min (limit 5 min): readings are out of date",
        ),
    ],
)
def test_describe_alarm(alarm: Alarm, words: str) -> None:
    assert describe_alarm(alarm, WITH_PROBE) == words


def test_every_alarm_kind_has_wording() -> None:
    """A new AlarmKind without wording would fail here (and in mypy)."""
    for kind in AlarmKind:
        assert describe_alarm(Alarm(kind, "P1", 1.0, 2.0), WITH_PROBE)


def test_unreadable_limit_is_shown_as_dashes() -> None:
    alarm = Alarm(AlarmKind.PROBE_HIGH, "P1", 96.0, None)
    assert describe_alarm(alarm, WITH_PROBE) == "RFX MEAT (P1) reached target: 96.0°C (target --)"


def test_unknown_device_falls_back_to_serial() -> None:
    alarm = Alarm(AlarmKind.PROBE_HIGH, "P9", 96.0, 95.0)
    assert describe_alarm(alarm, WITH_PROBE).startswith("P9 reached target")


def test_alarm_heading_counts_alarms() -> None:
    one = [Alarm(AlarmKind.PIT_HIGH, "G1", 182.0, 170.0)]
    two = [*one, Alarm(AlarmKind.GATEWAY_BATTERY, "G1", 8, 10)]
    assert format_active_alarms(one, WITH_PROBE)[0] == "*** 1 ALARM ***"
    assert format_active_alarms(two, WITH_PROBE)[0] == "*** 2 ALARMS ***"


# ------------------------------------------------- stale data and durations ---


def alarming_probe(last_seen: datetime) -> Probe:
    """A probe whose ETI high alarm says ALARMING, last heard from at `last_seen`."""
    high = AlarmLimit(enabled=True, celsius=20.0, alarming=True)
    return Probe(
        serial="P1",
        label="RFX MEAT",
        battery_pct=100,
        last_seen=last_seen,
        sensors=(Reading(celsius=23.0, taken_at=last_seen),),
        alarms=AlarmSettings(high=high, low=None),
    )


def test_fresh_probe_shows_alarming() -> None:
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=(alarming_probe(NOW),))
    assert "Alarms:  high 20.0°C ALARMING" in format_snapshot(snapshot, NOW)


def test_stale_probe_does_not_show_frozen_alarming() -> None:
    """Real case: kit switched off mid-alarm, ETI still says ALARMING hours later."""
    hours_ago = NOW - timedelta(hours=5)
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=(alarming_probe(hours_ago),))
    report = format_snapshot(snapshot, NOW)
    assert "Alarms:  high 20.0°C (no recent data)" in report
    assert "ALARMING" not in report


def test_staleness_uses_configured_timeout() -> None:
    six_minutes_ago = NOW - timedelta(minutes=6)
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=(alarming_probe(six_minutes_ago),))
    assert "ALARMING" not in format_snapshot(snapshot, NOW)  # default 5 min timeout
    relaxed = AlarmRules(probe_timeout_minutes=10)
    assert "ALARMING" in format_snapshot(snapshot, NOW, (), relaxed)


def test_stale_gateway_pit_alarms_marked_old() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = format_snapshot(to_snapshot(raw, NOW), NOW)
    assert "Alarm: high 170.0°C, low 30.0°C (no recent data)" in report


def test_stale_device_with_no_alarms_still_says_off() -> None:
    assert format_alarms(NO_ALARMS, fresh=False) == "off"


@pytest.mark.parametrize(
    ("minutes", "words"),
    [
        (0.5, "0 min"),
        (12.7, "12 min"),
        (59.9, "59 min"),
        (60.0, "1 h"),
        (333.0, "5 h 33 min"),
        (None, "?"),
    ],
)
def test_format_minutes(minutes: float | None, words: str) -> None:
    assert format_minutes(minutes) == words
