"""Tests for the monitor loop behind `smoker run`.

Everything the Monitor talks to is faked: the source hands out prepared
Snapshots (or errors), the clock is set by hand, sleeping is instant, and the
notifier just records what it was told. So each test can play out a cook,
poll by poll, in a fraction of a second.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smoker_monitor.domain.alarm_state import AlarmState, AlarmStatus
from smoker_monitor.domain.alarms import AlarmKind, AlarmRules
from smoker_monitor.domain.models import (
    AlarmLimit,
    AlarmSettings,
    Gateway,
    Probe,
    Reading,
    Snapshot,
)
from smoker_monitor.service.monitor import (
    SILENCE_FILE,
    Monitor,
    request_silence,
    summary,
)
from smoker_monitor.service.status import STATUS_FILE
from smoker_monitor.sources.base import SourceError

START = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
POLL = timedelta(seconds=30)
PIT_LIMITS = AlarmSettings(high=AlarmLimit(enabled=True, celsius=170.0), low=None)


# ------------------------------------------------------------------ fakes ---


class Clock:
    """A clock the test moves forward by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class ScriptedSource:
    """Hands out the prepared results in order: a Snapshot, or an error to raise."""

    def __init__(self, *results: Snapshot | Exception) -> None:
        self.results = list(results)
        self.closed = False

    async def fetch(self) -> Snapshot:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


class RecordingNotifier:
    """Remembers every update, so tests can see what would have rung."""

    def __init__(self) -> None:
        self.updates: list[tuple[bool, list[str]]] = []

    def update(self, state: AlarmState, messages: list[str]) -> None:
        self.updates.append((state.sound_on, messages))

    @property
    def sound_on(self) -> bool:
        return self.updates[-1][0]


class StopLoop(Exception):
    """Raised by a fake sleep to end run_forever in a test."""


async def no_sleep(seconds: float) -> None:
    """Sleeping in tests takes no time."""


# ---------------------------------------------------------------- helpers ---


def gateway(seen: datetime, pit: float = 110.0) -> Gateway:
    return Gateway(
        serial="G1",
        label="RFX GATEWAY",
        battery_pct=80,
        wifi_dbm=-50,
        last_seen=seen,
        pit=Reading(pit, seen),
        fan=None,
        pit_alarms=PIT_LIMITS,
    )


def probe(seen: datetime, core: float = 60.0) -> Probe:
    return Probe(
        serial="P1",
        label="PROBE-1",
        battery_pct=90,
        last_seen=seen,
        sensors=(Reading(core, seen), Reading(core + 5, seen)),
    )


def snapshot(at: datetime, pit: float = 110.0, probes: tuple[Probe, ...] = ()) -> Snapshot:
    return Snapshot(taken_at=at, gateway=gateway(at, pit), probes=probes)


def make_monitor(
    tmp_path: Path, *results: Snapshot | Exception
) -> tuple[Monitor, Clock, RecordingNotifier]:
    clock = Clock()
    notifier = RecordingNotifier()
    monitor = Monitor(
        source=ScriptedSource(*results),
        rules=AlarmRules(),
        notifier=notifier,
        state_dir=tmp_path,
        clock=clock,
        sleep=no_sleep,
    )
    return monitor, clock, notifier


async def poll_times(monitor: Monitor, clock: Clock, count: int) -> None:
    """Poll `count` times, 30 seconds apart, starting at the clock's current time."""
    for _ in range(count):
        await monitor.poll_once()
        clock.now += POLL


def read_status(tmp_path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((tmp_path / STATUS_FILE).read_text(encoding="utf-8"))
    return data


def statuses(monitor: Monitor) -> dict[AlarmKind, AlarmStatus]:
    return {t.alarm.kind: t.status for t in monitor.state.tracked.values()}


# ------------------------------------------------------------- all is well ---


async def test_calm_cook_makes_no_noise_and_writes_status(tmp_path: Path) -> None:
    monitor, _, notifier = make_monitor(tmp_path, snapshot(START))
    await monitor.poll_once()

    assert not notifier.sound_on
    status = read_status(tmp_path)
    assert status["sound_on"] is False
    assert status["alarms"] == []
    assert status["fetch_error"] is None


# ---------------------------------------------------------------- ringing ---


async def test_hot_pit_rings_on_the_second_poll(tmp_path: Path) -> None:
    monitor, clock, notifier = make_monitor(
        tmp_path, snapshot(START, pit=182.0), snapshot(START + POLL, pit=182.0)
    )
    await monitor.poll_once()
    assert not notifier.sound_on  # seen once: only pending

    clock.now += POLL
    await monitor.poll_once()
    assert notifier.sound_on
    assert len(notifier.updates[-1][1]) == 1  # one message for one ringing alarm
    assert read_status(tmp_path)["sound_on"] is True


# -------------------------------------------------------------- silencing ---


async def test_silence_request_quietens_ringing_alarm_between_polls(tmp_path: Path) -> None:
    monitor, clock, notifier = make_monitor(
        tmp_path, snapshot(START, pit=182.0), snapshot(START + POLL, pit=182.0)
    )
    await poll_times(monitor, clock, 2)
    assert notifier.sound_on

    request_silence(tmp_path)
    await monitor.wait_for_next_poll(30)

    assert not notifier.sound_on
    assert statuses(monitor) == {AlarmKind.PIT_HIGH: AlarmStatus.SILENCED}
    assert not (tmp_path / SILENCE_FILE).exists()  # the request is used up
    assert read_status(tmp_path)["sound_on"] is False


async def test_wait_without_silence_request_changes_nothing(tmp_path: Path) -> None:
    monitor, clock, notifier = make_monitor(
        tmp_path, snapshot(START, pit=182.0), snapshot(START + POLL, pit=182.0)
    )
    await poll_times(monitor, clock, 2)
    updates_before = len(notifier.updates)

    await monitor.wait_for_next_poll(30)

    assert len(notifier.updates) == updates_before
    assert notifier.sound_on


async def test_wait_checks_for_silence_every_second(tmp_path: Path) -> None:
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monitor, _, _ = make_monitor(tmp_path)
    monitor.sleep = record_sleep
    await monitor.wait_for_next_poll(2.5)
    assert slept == [1.0, 1.0, 0.5]


# ----------------------------------------------------------- fetch errors ---


async def test_failed_fetch_reuses_last_good_data(tmp_path: Path) -> None:
    good = snapshot(START)
    monitor, clock, notifier = make_monitor(tmp_path, good, SourceError("ETI Cloud is down"))
    await poll_times(monitor, clock, 2)

    assert monitor.snapshot is good
    assert monitor.fetch_error == "ETI Cloud is down"
    assert read_status(tmp_path)["fetch_error"] == "ETI Cloud is down"
    assert not notifier.sound_on  # the old data is still fresh enough


async def test_fetch_working_again_clears_the_error(tmp_path: Path) -> None:
    monitor, clock, _ = make_monitor(
        tmp_path, snapshot(START), SourceError("down"), snapshot(START + 2 * POLL)
    )
    await poll_times(monitor, clock, 3)
    assert monitor.fetch_error is None


async def test_long_outage_rings_cant_reach_eti_not_gateway_silent(tmp_path: Path) -> None:
    """After 5 minutes of failed fetches: "Can't reach ETI Cloud", not "Gateway silent"."""
    outage = [SourceError("down")] * 13
    monitor, clock, notifier = make_monitor(tmp_path, snapshot(START), *outage)
    await poll_times(monitor, clock, 14)  # 6.5 minutes in total

    assert statuses(monitor) == {AlarmKind.ETI_UNREACHABLE: AlarmStatus.RINGING}
    assert notifier.sound_on


async def test_short_outage_makes_no_noise(tmp_path: Path) -> None:
    outage = [SourceError("down")] * 6
    monitor, clock, notifier = make_monitor(tmp_path, snapshot(START), *outage)
    await poll_times(monitor, clock, 7)  # failing for 2.5 minutes

    assert monitor.state.tracked == {}
    assert not notifier.sound_on


async def test_outage_ending_lets_cant_reach_alarm_clear(tmp_path: Path) -> None:
    outage = [SourceError("down")] * 13
    back = [snapshot(START + n * POLL) for n in range(14, 16)]
    monitor, clock, _ = make_monitor(tmp_path, snapshot(START), *outage, *back)
    await poll_times(monitor, clock, 16)

    assert monitor.failing_since is None
    assert monitor.state.tracked == {}


async def test_never_any_data_rings_cant_reach_eti(tmp_path: Path) -> None:
    monitor, clock, notifier = make_monitor(tmp_path, *[SourceError("down")] * 13)
    await poll_times(monitor, clock, 13)  # last poll 6 minutes after the first

    assert statuses(monitor) == {AlarmKind.ETI_UNREACHABLE: AlarmStatus.RINGING}
    assert notifier.sound_on
    assert read_status(tmp_path)["gateway"] is None


# ------------------------------------------------------------- live probes ---


async def test_probe_that_goes_quiet_mid_cook_rings(tmp_path: Path) -> None:
    later = START + timedelta(minutes=6)
    monitor, clock, notifier = make_monitor(
        tmp_path,
        snapshot(START, probes=(probe(START),)),
        snapshot(later, probes=(probe(START),)),
        snapshot(later + POLL, probes=(probe(START),)),
    )
    await monitor.poll_once()
    assert monitor.live_probes == {"P1"}

    clock.now = later
    await poll_times(monitor, clock, 2)
    assert statuses(monitor) == {AlarmKind.PROBE_TIMEOUT: AlarmStatus.RINGING}
    assert notifier.sound_on


async def test_probe_left_in_the_drawer_is_ignored(tmp_path: Path) -> None:
    """A probe that was already quiet when the monitor started never alarms."""
    old = START - timedelta(days=3)
    monitor, clock, notifier = make_monitor(
        tmp_path,
        snapshot(START, probes=(probe(old),)),
        snapshot(START + POLL, probes=(probe(old),)),
    )
    await poll_times(monitor, clock, 2)

    assert monitor.live_probes == set()
    assert monitor.state.tracked == {}
    assert not notifier.sound_on


# ------------------------------------------------------------ run_forever ---


async def test_run_forever_survives_an_unexpected_error(tmp_path: Path) -> None:
    sleeps = 0

    async def stop_after_two_polls(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise StopLoop

    monitor, _, notifier = make_monitor(tmp_path, RuntimeError("bug!"), snapshot(START))
    monitor.sleep = stop_after_two_polls
    with pytest.raises(StopLoop):
        await monitor.run_forever(1)

    assert len(notifier.updates) == 1  # the second poll still happened
    assert (tmp_path / STATUS_FILE).exists()


async def test_run_forever_ignores_silence_request_from_before_it_started(
    tmp_path: Path,
) -> None:
    async def stop(seconds: float) -> None:
        raise StopLoop

    request_silence(tmp_path)  # left over from last night
    monitor, _, _ = make_monitor(tmp_path, snapshot(START))
    monitor.sleep = stop
    with pytest.raises(StopLoop):
        await monitor.run_forever(30)

    assert not (tmp_path / SILENCE_FILE).exists()


async def test_run_forever_closes_the_source_when_stopped(tmp_path: Path) -> None:
    async def stop(seconds: float) -> None:
        raise StopLoop

    source = ScriptedSource(snapshot(START))
    monitor, _, _ = make_monitor(tmp_path)
    monitor.source = source
    monitor.sleep = stop
    with pytest.raises(StopLoop):
        await monitor.run_forever(30)
    assert source.closed


# ---------------------------------------------------------------- summary ---


async def test_summary_line(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(
        tmp_path, snapshot(START, pit=110.0, probes=(probe(START, core=68.2),))
    )
    await monitor.poll_once()
    line = summary(monitor.snapshot or snapshot(START), monitor.state, "down")
    assert (
        line == "pit 110.0°C | P1 68.2°C | 0 ringing, 0 silenced | ETI unreachable, using last data"
    )
