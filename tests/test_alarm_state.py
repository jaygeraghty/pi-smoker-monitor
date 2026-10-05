"""Tests for alarm memory: confirming, clearing and silencing alarms across polls.

Each test plays out a short story, one poll at a time. A "poll" is a list of
the alarms evaluate() found that time ([] means nothing wrong).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from smoker_monitor.domain.alarm_state import (
    CLEAR_POLLS,
    CONFIRM_POLLS,
    AlarmState,
    AlarmStatus,
    key_of,
    silence,
    silence_all,
    step,
)
from smoker_monitor.domain.alarms import Alarm, AlarmKind

START = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
POLL = timedelta(seconds=30)

PIT_HOT = Alarm(AlarmKind.PIT_HIGH, "G1", 182.0, 170.0)
BRISKET_DONE = Alarm(AlarmKind.PROBE_HIGH, "P1", 95.1, 95.0)


def run(*polls: Sequence[Alarm], state: AlarmState | None = None) -> AlarmState:
    """Feed a series of polls through step(), 30 seconds apart."""
    state = state or AlarmState()
    for number, alarms in enumerate(polls):
        state = step(state, alarms, START + number * POLL)
    return state


def status_of(state: AlarmState, alarm: Alarm) -> AlarmStatus | None:
    """The alarm's status, or None if it isn't being watched (all clear)."""
    found = state.tracked.get(key_of(alarm))
    return None if found is None else found.status


def test_settings_match_what_was_agreed() -> None:
    assert CONFIRM_POLLS == 2
    assert CLEAR_POLLS == 2


def test_nothing_wrong_is_quiet() -> None:
    state = run([], [], [])
    assert state.tracked == {}
    assert not state.sound_on


# ------------------------------------------------------------- confirming ---


def test_first_sighting_is_only_pending() -> None:
    state = run([PIT_HOT])
    assert status_of(state, PIT_HOT) is AlarmStatus.PENDING
    assert not state.sound_on


def test_second_sighting_rings() -> None:
    state = run([PIT_HOT], [PIT_HOT])
    assert status_of(state, PIT_HOT) is AlarmStatus.RINGING
    assert state.sound_on
    assert [t.alarm for t in state.ringing] == [PIT_HOT]


def test_single_glitch_never_rings() -> None:
    """Seen once, then gone: forgotten without ever making a sound."""
    state = run([PIT_HOT], [], [])
    assert status_of(state, PIT_HOT) is None
    assert not state.sound_on


def test_wobbling_reading_still_rings() -> None:
    """94.9, 95.0, 94.9, 95.0...: a missed poll in between doesn't reset the count."""
    state = run([BRISKET_DONE], [], [BRISKET_DONE])
    assert status_of(state, BRISKET_DONE) is AlarmStatus.RINGING


# --------------------------------------------------------------- clearing ---


def test_ringing_survives_one_missed_poll() -> None:
    state = run([BRISKET_DONE], [BRISKET_DONE], [])
    assert status_of(state, BRISKET_DONE) is AlarmStatus.RINGING
    assert state.sound_on


def test_wobbling_while_ringing_keeps_ringing() -> None:
    state = run([BRISKET_DONE], [BRISKET_DONE], [], [BRISKET_DONE], [], [BRISKET_DONE])
    assert status_of(state, BRISKET_DONE) is AlarmStatus.RINGING


def test_problem_that_sorts_itself_out_stops_on_its_own() -> None:
    """The pit dipped, then recovered: gone for 2 polls in a row ends the alarm."""
    state = run([PIT_HOT], [PIT_HOT], [], [])
    assert status_of(state, PIT_HOT) is None
    assert not state.sound_on


def test_cleared_alarm_must_be_confirmed_again() -> None:
    state = run([PIT_HOT], [PIT_HOT], [], [], [PIT_HOT])
    assert status_of(state, PIT_HOT) is AlarmStatus.PENDING


# -------------------------------------------------------------- silencing ---


def test_silence_stops_the_noise() -> None:
    state = silence_all(run([PIT_HOT], [PIT_HOT]))
    assert status_of(state, PIT_HOT) is AlarmStatus.SILENCED
    assert not state.sound_on
    assert [t.alarm for t in state.silenced] == [PIT_HOT]


def test_silenced_alarm_stays_quiet_while_the_problem_lasts() -> None:
    state = silence_all(run([PIT_HOT], [PIT_HOT]))
    state = run([PIT_HOT], [PIT_HOT], [PIT_HOT], state=state)
    assert status_of(state, PIT_HOT) is AlarmStatus.SILENCED
    assert not state.sound_on


def test_silenced_alarm_rings_again_if_it_comes_back_after_clearing() -> None:
    """Silence lasts until the problem clears; a fresh occurrence is news."""
    state = silence_all(run([PIT_HOT], [PIT_HOT]))
    state = run([], [], [PIT_HOT], [PIT_HOT], state=state)
    assert status_of(state, PIT_HOT) is AlarmStatus.RINGING


def test_new_alarm_rings_even_when_another_is_silenced() -> None:
    state = silence_all(run([PIT_HOT], [PIT_HOT]))
    state = run([PIT_HOT, BRISKET_DONE], [PIT_HOT, BRISKET_DONE], state=state)
    assert status_of(state, PIT_HOT) is AlarmStatus.SILENCED
    assert status_of(state, BRISKET_DONE) is AlarmStatus.RINGING
    assert state.sound_on


def test_silence_button_leaves_pending_alarms_alone() -> None:
    """Something not yet ringing when you press silence still rings when it's confirmed."""
    state = silence_all(run([PIT_HOT]))
    state = run([PIT_HOT], state=state)
    assert status_of(state, PIT_HOT) is AlarmStatus.RINGING


def test_silence_one_alarm() -> None:
    state = run([PIT_HOT, BRISKET_DONE], [PIT_HOT, BRISKET_DONE])
    state = silence(state, key_of(BRISKET_DONE))
    assert status_of(state, BRISKET_DONE) is AlarmStatus.SILENCED
    assert status_of(state, PIT_HOT) is AlarmStatus.RINGING


def test_silencing_something_not_ringing_changes_nothing() -> None:
    state = run([PIT_HOT])
    assert silence(state, key_of(PIT_HOT)) == state
    assert silence(state, key_of(BRISKET_DONE)) == state


# ---------------------------------------------------------------- details ---


def test_latest_values_are_kept() -> None:
    hotter = Alarm(AlarmKind.PIT_HIGH, "G1", 190.0, 170.0)
    state = run([PIT_HOT], [hotter])
    assert state.tracked[key_of(PIT_HOT)].alarm.value == 190.0


def test_episode_remembers_when_it_started() -> None:
    state = run([], [PIT_HOT], [PIT_HOT], [PIT_HOT])
    assert state.tracked[key_of(PIT_HOT)].first_seen == START + POLL


def test_same_kind_on_two_devices_are_separate_alarms() -> None:
    white = Alarm(AlarmKind.PROBE_HIGH, "P1", 95.1, 95.0)
    pink = Alarm(AlarmKind.PROBE_HIGH, "P2", 96.0, 95.0)
    state = silence(run([white, pink], [white, pink]), key_of(white))
    assert status_of(state, white) is AlarmStatus.SILENCED
    assert status_of(state, pink) is AlarmStatus.RINGING


def test_restart_forgets_silencing_so_alarms_ring_again() -> None:
    """Fail-safe: nothing is saved, so a fresh start treats every alarm as new."""
    before_restart = silence_all(run([PIT_HOT], [PIT_HOT]))
    assert not before_restart.sound_on
    after_restart = run([PIT_HOT], [PIT_HOT])  # a brand-new AlarmState
    assert after_restart.sound_on
