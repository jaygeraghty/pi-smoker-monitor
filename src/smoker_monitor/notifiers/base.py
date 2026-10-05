"""The Notifier protocol: anything that tells you about ringing alarms.

The service calls `update` with the latest alarm state after every poll (and
whenever silence is pressed); each notifier decides what to do about it, e.g.
start or stop a sound.
"""

from __future__ import annotations

from typing import Protocol

from smoker_monitor.domain.alarm_state import AlarmState


class Notifier(Protocol):
    """Something that tells you about ringing alarms (and stops when they stop)."""

    def update(self, state: AlarmState, messages: list[str]) -> None:
        """Called with the current state and a message per ringing alarm."""
        ...
