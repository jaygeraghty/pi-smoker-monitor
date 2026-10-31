"""Noticing taps on the touchscreen.

Only "a finger touched the screen" matters, not where: while an alarm rings,
a tap anywhere silences it. That's easy half-asleep in the dark, and it means
the touch layer never needs matching up with the picture's orientation.

Uses the `evdev` library (Linux only; installed with `uv sync --extra pi`).
Reading the touchscreen needs your user in the "input" group.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


async def watch_taps(on_tap: Callable[[], None]) -> None:
    """Call `on_tap` each time a finger touches the screen. Runs until cancelled.

    Without evdev or a touchscreen it logs why and returns, so the screen
    still works, just without tap-to-silence.
    """
    try:
        import evdev
    except ImportError:
        log.warning("Tap to silence is off: install evdev (uv sync --extra pi)")
        return
    device = find_touchscreen(evdev)
    if device is None:
        log.warning("Tap to silence is off: no touchscreen found (is your user in 'input'?)")
        return
    log.info("Listening for taps on %s", device.name)
    async for event in device.async_read_loop():
        if is_touch_down(event.type, event.code, event.value, evdev.ecodes):
            on_tap()


def find_touchscreen(evdev: Any) -> Any:
    """The first input device that reports touches, or None."""
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        keys = device.capabilities().get(evdev.ecodes.EV_KEY, [])
        if evdev.ecodes.BTN_TOUCH in keys:
            return device
        device.close()
    return None


def is_touch_down(kind: int, code: int, value: int, ecodes: Any) -> bool:
    """True for the moment a finger lands (not while it stays, or when it lifts)."""
    return bool(kind == ecodes.EV_KEY and code == ecodes.BTN_TOUCH and value == 1)
