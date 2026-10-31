"""Telling systemd the monitor is alive (and restarting it if it isn't).

Under systemd (see deploy/systemd/), the monitor's unit says `Type=notify` and
`WatchdogSec=120`. The monitor then:
- says READY=1 once it has started, and
- says WATCHDOG=1 at least every few seconds while it is working.

If it ever stops saying so for 2 minutes (stuck waiting on something forever,
say) systemd kills and restarts it. A crash is restarted by `Restart=always`
without needing this; the watchdog is for a monitor that is alive but stuck,
which would otherwise look fine and ring nothing.

Outside systemd there is no NOTIFY_SOCKET, and everything here does nothing.
Uses only the standard library (the message is one datagram on a Unix socket).
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

# Say "alive" at most this often, however often the monitor asks.
PING_EVERY_SECONDS = 5.0


class Watchdog(Protocol):
    """What the monitor tells whoever is watching it."""

    def ready(self) -> None: ...

    def ping(self) -> None: ...


class NoWatchdog:
    """Nobody is watching (a normal run from the command line, and tests)."""

    def ready(self) -> None:
        return None

    def ping(self) -> None:
        return None


class SystemdWatchdog:
    """Sends systemd's notifications. Harmless when not run by systemd."""

    def __init__(
        self,
        address: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.address = address if address is not None else os.environ.get("NOTIFY_SOCKET")
        self.clock = clock
        self._last_ping: float | None = None
        self._warned = False

    @property
    def active(self) -> bool:
        return bool(self.address)

    def ready(self) -> None:
        """Say the monitor has started."""
        self._send("READY=1")

    def ping(self) -> None:
        """Say the monitor is alive (at most every few seconds)."""
        now = self.clock()
        if self._last_ping is not None and now - self._last_ping < PING_EVERY_SECONDS:
            return
        self._last_ping = now
        self._send("WATCHDOG=1")

    def _send(self, message: str) -> None:
        if not self.address:
            return
        # A name starting with "@" is an "abstract" socket: it begins with a zero byte.
        address = "\0" + self.address[1:] if self.address.startswith("@") else self.address
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(address)
                sock.send(message.encode("ascii"))
        except OSError as e:
            if not self._warned:  # once is enough; this must never trouble the monitor
                self._warned = True
                log.warning("Couldn't tell systemd (%s): %s", message, e)
