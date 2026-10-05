"""A notifier for Replit and development: prints alarms and rings the terminal bell."""

from __future__ import annotations

from collections.abc import Callable

from smoker_monitor.domain.alarm_state import AlarmState


class ConsoleNotifier:
    """Prints ringing alarms and rings the terminal bell; for Replit and testing.

    Prints every poll while something rings (so it keeps nagging, like a real
    alarm), and a single "quiet" line when the noise stops.
    """

    def __init__(self, write: Callable[[str], None] = print) -> None:
        self._write = write
        self._was_on = False

    def update(self, state: AlarmState, messages: list[str]) -> None:
        if state.sound_on:
            for message in messages:
                self._write(f"\a🔔 ALARM: {message}")
            self._write("   (run `smoker silence` to silence)")
        elif self._was_on:
            self._write("🔕 Alarm quiet.")
        self._was_on = state.sound_on
