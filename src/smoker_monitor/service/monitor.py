"""The monitor service behind `smoker run`: the loop that wakes you up.

Every poll (config: [polling] interval_seconds):
1. fetch a Snapshot from the source (ETI Cloud)
2. remember which probes are live this cook
3. evaluate() what's wrong right now
4. step() the alarm memory: what should be ringing
5. write the status file for screens
6. tell the notifier (ring or stop)

Between polls it checks for a silence request every second, so pressing
silence takes effect straight away rather than at the next poll.

If a fetch fails (no internet, ETI Cloud down), the last good Snapshot is used
again (or an empty one if there has never been a good fetch). While fetching
keeps failing, the "device gone quiet" alarms are switched off, because the Pi
can't tell a quiet Gateway from a quiet internet. Instead, once it has failed
for [alarms] eti_unreachable_minutes, the "Can't reach ETI Cloud" alarm rings.

Silence requests are a file (state/silence) so that anything can make one:
the `smoker silence` command now, the screen's button later.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from smoker_monitor.domain.alarm_state import AlarmState, silence_all, step
from smoker_monitor.domain.alarms import AlarmRules, evaluate, fresh_probes, while_unreachable
from smoker_monitor.domain.models import Snapshot
from smoker_monitor.notifiers.base import Notifier
from smoker_monitor.report import describe_alarm
from smoker_monitor.service.status import STATUS_FILE, build_status, write_status
from smoker_monitor.sources.base import Source, SourceError

log = logging.getLogger(__name__)

SILENCE_FILE = "silence"

# How often to check for a silence request between polls.
SILENCE_CHECK_SECONDS = 1.0


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_silence(state_dir: Path) -> None:
    """Ask a running monitor to silence its ringing alarms (what `smoker silence` does)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / SILENCE_FILE).touch()


class Monitor:
    """Runs the poll loop. Everything it talks to is passed in, so tests can fake it."""

    def __init__(
        self,
        source: Source,
        rules: AlarmRules,
        notifier: Notifier,
        state_dir: Path,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.source = source
        self.rules = rules
        self.notifier = notifier
        self.state_dir = state_dir
        self.clock = clock
        self.sleep = sleep

        self.state = AlarmState()
        self.live_probes: set[str] = set()
        self.snapshot: Snapshot | None = None  # the last good one
        self.fetch_error: str | None = None
        self.failing_since: datetime | None = None  # first failed fetch in a row

    async def run_forever(self, interval_seconds: float) -> None:
        """Poll, then wait (watching for silence), forever. Stop with Ctrl+C."""
        # A silence request left over from before we started is stale.
        self._take_silence_request()
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception:
                    # A bug must not kill the monitor: log it and keep polling.
                    log.exception("Poll failed unexpectedly; carrying on")
                await self.wait_for_next_poll(interval_seconds)
        finally:
            # Ctrl+C or a stop: close the connection to ETI Cloud cleanly.
            await self.source.close()

    async def poll_once(self) -> AlarmState:
        """Do one full poll and return the new alarm state."""
        now = self.clock()
        snapshot = await self._fetch(now)

        self.live_probes |= fresh_probes(snapshot, now, self.rules)
        alarms = evaluate(snapshot, now, self.rules, live_probes=self.live_probes)
        if self.failing_since is not None:
            alarms = while_unreachable(alarms, self.failing_since, now, self.rules)
        self.state = step(self.state, alarms, now)

        self._publish(snapshot, now)
        log.info("%s", summary(snapshot, self.state, self.fetch_error))
        return self.state

    async def wait_for_next_poll(self, interval_seconds: float) -> None:
        """Sleep until the next poll, acting on silence requests as they arrive."""
        waited = 0.0
        while waited < interval_seconds:
            step_seconds = min(SILENCE_CHECK_SECONDS, interval_seconds - waited)
            await self.sleep(step_seconds)
            waited += step_seconds
            if self._take_silence_request():
                self.silence()

    def silence(self) -> None:
        """Silence everything that's ringing, and update the notifier and status at once."""
        self.state = silence_all(self.state)
        if self.snapshot is not None:
            self._publish(self.snapshot, self.clock())
        else:
            self.notifier.update(self.state, [])
        log.info("Silenced")

    # ------------------------------------------------------------ helpers ---

    async def _fetch(self, now: datetime) -> Snapshot:
        """The latest Snapshot, or the last good one if this fetch fails."""
        try:
            self.snapshot = await self.source.fetch()
            self.fetch_error = None
            self.failing_since = None
        except SourceError as e:
            self.fetch_error = str(e)
            if self.failing_since is None:
                self.failing_since = now
            log.warning("Fetch failed, using the last good data: %s", e)
        if self.snapshot is None:
            # Never had any data: an empty Snapshot (no Gateway).
            return Snapshot(taken_at=now, gateway=None, probes=())
        return self.snapshot

    def _publish(self, snapshot: Snapshot, now: datetime) -> None:
        """Write the status file and tell the notifier."""
        write_status(
            self.state_dir / STATUS_FILE,
            build_status(snapshot, self.state, now, self.fetch_error),
        )
        messages = [describe_alarm(t.alarm, snapshot) for t in self.state.ringing]
        self.notifier.update(self.state, messages)

    def _take_silence_request(self) -> bool:
        """True (and the request is used up) if someone asked for silence."""
        request = self.state_dir / SILENCE_FILE
        if not request.exists():
            return False
        request.unlink(missing_ok=True)
        return True


def summary(snapshot: Snapshot, state: AlarmState, fetch_error: str | None) -> str:
    """A one-line log entry, e.g. 'pit 110.0°C | P1 68.2°C | 1 ringing'."""
    parts = []
    if snapshot.gateway is not None:
        pit = snapshot.gateway.get_pit_temp()
        parts.append("pit --" if pit is None else f"pit {pit:.1f}°C")
    for probe in snapshot.probes:
        core = probe.core_celsius()
        parts.append(f"{probe.serial} " + ("--" if core is None else f"{core:.1f}°C"))
    parts.append(f"{len(state.ringing)} ringing, {len(state.silenced)} silenced")
    if fetch_error is not None:
        parts.append("ETI unreachable, using last data")
    return " | ".join(parts)
