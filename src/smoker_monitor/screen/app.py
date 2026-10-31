"""The screen app behind `smoker screen`: keeps the screen showing the status file.

It's a separate program from the monitor (`smoker run`), on purpose: if the
screen code ever breaks, the alarms keep working.

Every second it checks state/status.json, and redraws when the file has
changed or the clock's minute has ticked over. A tap anywhere on the screen
while an alarm rings asks the monitor to silence it (the same as running
`smoker silence`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from smoker_monitor.screen.draw import draw_message, draw_screen
from smoker_monitor.screen.touch import watch_taps
from smoker_monitor.service.monitor import request_silence
from smoker_monitor.service.status import STATUS_FILE

log = logging.getLogger(__name__)

# How often to look at the status file.
CHECK_SECONDS = 1.0


class Display(Protocol):
    """Somewhere to show the picture: the real screen, or a fake in tests."""

    def show(self, image: Image.Image) -> None: ...


def local_now() -> datetime:
    return datetime.now().astimezone()


def load_status(path: Path) -> dict[str, Any] | None:
    """The status file's contents, or None if it's missing or unreadable."""
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return status if isinstance(status, dict) else None


class ScreenApp:
    """Keeps a Display showing the status. Everything it uses is passed in, for tests."""

    def __init__(
        self,
        state_dir: Path,
        display: Display,
        clock: Callable[[], datetime] = local_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.state_dir = state_dir
        self.display = display
        self.clock = clock
        self.sleep = sleep
        self.status: dict[str, Any] | None = None
        self._shown: tuple[object, ...] | None = None  # what's on screen now

    async def run(self) -> None:
        """Keep the screen up to date, and listen for taps. Runs until stopped."""
        taps = asyncio.create_task(watch_taps(self.tap))
        try:
            while True:
                try:
                    self.refresh()
                except Exception:
                    # A drawing bug mustn't stop the screen for good.
                    log.exception("Couldn't draw the screen; trying again")
                await self.sleep(CHECK_SECONDS)
        finally:
            taps.cancel()

    def refresh(self) -> bool:
        """Redraw if anything visible has changed. Returns True if it redrew."""
        path = self.state_dir / STATUS_FILE
        now = self.clock()
        try:
            changed = path.stat().st_mtime_ns
        except OSError:
            changed = None
        # The minute is part of it: the clock, and "monitor stopped", move on.
        showing = (changed, now.strftime("%Y-%m-%d %H:%M"))
        if showing == self._shown:
            return False
        self.status = load_status(path)
        if self.status is None:
            image = draw_message("Waiting for the monitor to start…")
        else:
            image = draw_screen(self.status, now)
        self.display.show(image)
        self._shown = showing
        return True

    def tap(self) -> None:
        """A finger touched the screen: silence any ringing alarm."""
        if self.status and any(a.get("status") == "ringing" for a in self.status.get("alarms", [])):
            request_silence(self.state_dir)
            log.info("Tapped: silence requested")
