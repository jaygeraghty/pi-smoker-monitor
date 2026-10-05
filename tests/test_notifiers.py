"""Tests for the console notifier (what `smoker run` prints when alarms ring)."""

from __future__ import annotations

from datetime import UTC, datetime

from smoker_monitor.domain.alarm_state import AlarmState, silence_all, step
from smoker_monitor.domain.alarms import Alarm, AlarmKind
from smoker_monitor.notifiers.console import ConsoleNotifier

NOW = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
PIT_HOT = Alarm(AlarmKind.PIT_HIGH, "G1", 182.0, 170.0)
RINGING = step(step(AlarmState(), [PIT_HOT], NOW), [PIT_HOT], NOW)
QUIET = AlarmState()


def notifier() -> tuple[ConsoleNotifier, list[str]]:
    lines: list[str] = []
    return ConsoleNotifier(write=lines.append), lines


def test_nothing_ringing_prints_nothing() -> None:
    console, lines = notifier()
    console.update(QUIET, [])
    console.update(QUIET, [])
    assert lines == []


def test_ringing_prints_each_message_with_the_bell() -> None:
    console, lines = notifier()
    console.update(RINGING, ["Pit too hot"])
    assert lines[0] == "\a🔔 ALARM: Pit too hot"
    assert "smoker silence" in lines[1]


def test_keeps_nagging_every_poll_while_ringing() -> None:
    console, lines = notifier()
    console.update(RINGING, ["Pit too hot"])
    console.update(RINGING, ["Pit too hot"])
    assert sum("ALARM" in line for line in lines) == 2


def test_says_quiet_once_when_the_noise_stops() -> None:
    console, lines = notifier()
    console.update(RINGING, ["Pit too hot"])
    lines.clear()
    console.update(silence_all(RINGING), [])
    console.update(silence_all(RINGING), [])
    assert lines == ["🔕 Alarm quiet."]
