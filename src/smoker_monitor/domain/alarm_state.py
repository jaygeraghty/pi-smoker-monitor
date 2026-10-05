"""Alarm memory: which alarms should be ringing, across polls.

`evaluate` (in alarms.py) says what is wrong *right now*, with no memory. This
module remembers each alarm over time, so that:
- a single glitchy reading never wakes you (it must be seen in CONFIRM_POLLS
  polls before it rings),
- a reading wobbling around a limit doesn't start and stop the alarm every
  poll (one missed poll doesn't reset or clear anything),
- you can silence an alarm, and it stays quiet until its problem has gone,
- a problem that sorts itself out stops the alarm on its own (once it has
  been gone for CLEAR_POLLS polls in a row).

An alarm is identified by its kind and device (AlarmKey). A new alarm always
rings, even if others are silenced.

Pure logic, no hardware or clock: the service calls `step` once per poll with
that poll's alarms, and `silence_all` when the silence button is pressed.
Nothing is saved to disk, so after a restart any active alarm rings again
(the fail-safe).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from smoker_monitor.domain.alarms import Alarm, AlarmKind

# How many polls an alarm must be seen in before it rings. A missed poll in
# between doesn't reset the count, so a wobbling reading still gets there.
CONFIRM_POLLS = 2

# How many polls in a row an alarm must be gone before it is forgotten: it
# stops ringing (or un-silences) and would have to be confirmed afresh.
CLEAR_POLLS = 2

# Identifies an alarm across polls, e.g. (PROBE_HIGH, "P1").
AlarmKey = tuple[AlarmKind, str]


def key_of(alarm: Alarm) -> AlarmKey:
    """The identity of an alarm: its kind and device."""
    return (alarm.kind, alarm.device)


class AlarmStatus(StrEnum):
    """Where an alarm is in its life."""

    PENDING = "pending"  # seen, but not often enough yet to ring
    RINGING = "ringing"  # wake me
    SILENCED = "silenced"  # I know; quiet until it clears


@dataclass(frozen=True)
class TrackedAlarm:
    """One alarm being watched over time."""

    alarm: Alarm  # the latest version seen (its value updates each poll)
    first_seen: datetime  # when this episode started
    seen: int = 1  # polls it has been seen in
    missing: int = 0  # polls in a row it has been gone
    silenced: bool = False

    @property
    def status(self) -> AlarmStatus:
        if self.silenced:
            return AlarmStatus.SILENCED
        if self.seen >= CONFIRM_POLLS:
            return AlarmStatus.RINGING
        return AlarmStatus.PENDING


@dataclass(frozen=True)
class AlarmState:
    """Every alarm currently being watched. Start with an empty one: AlarmState()."""

    tracked: dict[AlarmKey, TrackedAlarm] = field(default_factory=dict)

    def with_status(self, status: AlarmStatus) -> tuple[TrackedAlarm, ...]:
        """The watched alarms that have this status, in the order they started."""
        return tuple(t for t in self.tracked.values() if t.status is status)

    @property
    def ringing(self) -> tuple[TrackedAlarm, ...]:
        return self.with_status(AlarmStatus.RINGING)

    @property
    def silenced(self) -> tuple[TrackedAlarm, ...]:
        return self.with_status(AlarmStatus.SILENCED)

    @property
    def sound_on(self) -> bool:
        """True if anything should be making noise right now."""
        return bool(self.ringing)


def step(state: AlarmState, alarms: Iterable[Alarm], now: datetime) -> AlarmState:
    """Return the new state after one poll that found `alarms`.

    - Seen again: count it, reset its "missing" count, keep its latest values.
    - Seen for the first time: start watching it (pending).
    - Not seen: count it as missing; after CLEAR_POLLS in a row, forget it.
    """
    present = {key_of(alarm): alarm for alarm in alarms}
    tracked: dict[AlarmKey, TrackedAlarm] = {}

    for key, old in state.tracked.items():
        if key in present:
            tracked[key] = replace(old, alarm=present[key], seen=old.seen + 1, missing=0)
        elif old.missing + 1 < CLEAR_POLLS:
            tracked[key] = replace(old, missing=old.missing + 1)
        # else: gone for CLEAR_POLLS polls in a row, so it is forgotten.

    for key, alarm in present.items():
        if key not in tracked:
            tracked[key] = TrackedAlarm(alarm=alarm, first_seen=now)

    return AlarmState(tracked)


def silence_all(state: AlarmState) -> AlarmState:
    """Silence everything that is ringing (the silence button).

    Pending alarms are left alone: if one starts ringing later, that's news.
    """
    return AlarmState(
        {
            key: replace(t, silenced=True) if t.status is AlarmStatus.RINGING else t
            for key, t in state.tracked.items()
        }
    )


def silence(state: AlarmState, key: AlarmKey) -> AlarmState:
    """Silence one ringing alarm (e.g. picked on a screen). Anything else: no change."""
    found = state.tracked.get(key)
    if found is None or found.status is not AlarmStatus.RINGING:
        return state
    return AlarmState({**state.tracked, key: replace(found, silenced=True)})
