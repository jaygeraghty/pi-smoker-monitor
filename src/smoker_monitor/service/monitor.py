"""The monitor service behind `smoker run`: the loop that wakes you up.

It watches any number of sources (brands, e.g. ETI Cloud), each fetched on
its own schedule (its poll_seconds). Every poll:
1. fetch a Snapshot from each source that's due, all at once
2. combine every source's latest Snapshot into one
3. remember which probes are live this cook, and keep any of those that a
   source has stopped listing (e.g. a Traeger probe pulled out), so they go
   stale and "gone quiet" rings
4. evaluate() what's wrong right now
5. step() the alarm memory: what should be ringing
6. write the status file for screens
7. tell the notifier (ring or stop)

Between polls it checks for a silence request every second, so pressing
silence takes effect straight away rather than at the next poll.

If a source's fetch fails (no internet, ETI Cloud down), its last good
Snapshot is used again (or nothing, if it has never worked). While it keeps
failing, its devices' "gone quiet" alarms are off, because the Pi can't tell
a quiet Gateway from a quiet internet. Instead, once it has failed for
[alarms] source_unreachable_minutes, "Can't reach {source}" rings. One
source failing never stops the others being watched.

Silence requests are a file (state/silence) so that anything can make one:
the `smoker silence` command now, the screen's button later.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from smoker_monitor.domain.alarm_state import AlarmState, silence_all, step
from smoker_monitor.domain.alarms import AlarmRules, evaluate, fresh_probes, is_fresh
from smoker_monitor.domain.models import Probe, Snapshot, SourceHealth
from smoker_monitor.domain.names import gateway_names, grill_names, probe_names
from smoker_monitor.notifiers.base import Notifier
from smoker_monitor.report import describe_alarm
from smoker_monitor.service.combine import combine, keep_vanished_probes
from smoker_monitor.service.status import STATUS_FILE, build_status, write_status
from smoker_monitor.sources.base import Source, SourceError

log = logging.getLogger(__name__)

SILENCE_FILE = "silence"

# How often to check for a silence request between polls.
SILENCE_CHECK_SECONDS = 1.0

# The shortest wait between polls, however the sources' schedules line up.
MIN_WAIT_SECONDS = 1.0


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_silence(state_dir: Path) -> None:
    """Ask a running monitor to silence its ringing alarms (what `smoker silence` does)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / SILENCE_FILE).touch()


@dataclass
class SourceState:
    """What the monitor remembers about one source between polls."""

    source: Source
    last_good: Snapshot | None = None  # its last good Snapshot, if any
    error: str | None = None  # why its last fetch failed (None if it worked)
    failing_since: datetime | None = None  # first failed fetch in a row
    next_due: datetime | None = None  # when to fetch next (None: straight away)

    def is_due(self, now: datetime) -> bool:
        return self.next_due is None or self.next_due <= now

    def health(self) -> SourceHealth:
        return SourceHealth(
            key=self.source.key,
            name=self.source.name,
            expects_gateway=self.source.expects_gateway,
            error=self.error,
            failing_since=self.failing_since,
        )


class Monitor:
    """Runs the poll loop. Everything it talks to is passed in, so tests can fake it."""

    def __init__(
        self,
        sources: Sequence[Source],
        rules: AlarmRules,
        notifier: Notifier,
        state_dir: Path,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        keys = [source.key for source in sources]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Two sources share a key: {keys}")
        self.states = [SourceState(source) for source in sources]
        self.rules = rules
        self.notifier = notifier
        self.state_dir = state_dir
        self.clock = clock
        self.sleep = sleep

        self.state = AlarmState()
        self.live_probes: set[str] = set()
        self.remembered_probes: dict[str, Probe] = {}  # last copy of each live probe
        self.snapshot: Snapshot | None = None  # the latest combined one

    async def run_forever(self) -> None:
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
                await self.wait_for_next_poll(self.seconds_until_due())
        finally:
            # Ctrl+C or a stop: close every source's connection cleanly.
            for state in self.states:
                await state.source.close()

    async def poll_once(self) -> AlarmState:
        """Fetch from every source that's due, then check alarms. Returns the new alarm state."""
        now = self.clock()
        due = [state for state in self.states if state.is_due(now)]
        await asyncio.gather(*(self._fetch(state, now) for state in due))

        snapshot = combine(now, [(state.health(), state.last_good) for state in self.states])
        self.live_probes |= fresh_probes(snapshot, now, self.rules)
        self.remembered_probes.update(
            (probe.id, probe) for probe in snapshot.probes if probe.id in self.live_probes
        )
        snapshot = keep_vanished_probes(snapshot, self.remembered_probes, self.live_probes)
        self.snapshot = snapshot
        alarms = evaluate(snapshot, now, self.rules, live_probes=self.live_probes)
        self.state = step(self.state, alarms, now)

        self._publish(snapshot, now)
        log.info("%s", summary(snapshot, self.state, now, self.rules))
        return self.state

    def seconds_until_due(self) -> float:
        """How long until the next source is due (at least MIN_WAIT_SECONDS)."""
        now = self.clock()
        waits = [
            (state.next_due - now).total_seconds() if state.next_due is not None else 0.0
            for state in self.states
        ]
        return max(MIN_WAIT_SECONDS, min(waits, default=MIN_WAIT_SECONDS))

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

    async def _fetch(self, state: SourceState, now: datetime) -> None:
        """Fetch from one source, remembering the result or why it failed.

        Never raises: a broken source (even a bug in it) is just a failing
        source, so it can't stop the others being watched.
        """
        source = state.source
        state.next_due = now + timedelta(seconds=source.poll_seconds)
        try:
            state.last_good = await source.fetch()
        except SourceError as e:
            state.error = str(e)
            log.warning("%s: fetch failed, using its last good data: %s", source.name, e)
        except Exception:
            # Not the source's message: it could hold anything, even a password.
            state.error = f"{source.name} failed unexpectedly (see the log)"
            log.exception("%s: fetch failed unexpectedly", source.name)
        else:
            state.error = None
            state.failing_since = None
            return
        if state.failing_since is None:
            state.failing_since = now

    def _publish(self, snapshot: Snapshot, now: datetime) -> None:
        """Write the status file and tell the notifier."""
        write_status(
            self.state_dir / STATUS_FILE,
            build_status(snapshot, self.state, now, self.rules),
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


def summary(snapshot: Snapshot, state: AlarmState, now: datetime, rules: AlarmRules) -> str:
    """A one-line log entry, e.g. 'pit 110.0°C | white probe 68.2°C | 1 ringing, 0 silenced'.

    A device that hasn't reported within its timeout shows as 'stale' rather
    than an old temperature that looks current.
    """
    gateway_limit = timedelta(minutes=rules.gateway_timeout_minutes)
    probe_limit = timedelta(minutes=rules.probe_timeout_minutes)
    names = probe_names(snapshot.probes)
    hubs = gateway_names(snapshot.gateways)
    parts = []
    for gateway in snapshot.gateways:
        # With more than one gateway, say whose pit it is.
        pit = "pit" if len(snapshot.gateways) == 1 else f"{hubs[gateway.id]} pit"
        if not is_fresh(gateway.last_seen, now, gateway_limit):
            parts.append(f"{pit} stale")
        else:
            parts.append(f"{pit} {format_reading(gateway.get_pit_temp())}")
    grill_limit = timedelta(minutes=rules.grill_timeout_minutes)
    for grill_id, grill_name in grill_names(snapshot.grills).items():
        grill = next(g for g in snapshot.grills if g.id == grill_id)
        if not grill.mode.is_on:
            parts.append(f"{grill_name} {grill.mode.value}")
        elif not is_fresh(grill.last_seen, now, grill_limit):
            parts.append(f"{grill_name} stale")
        else:
            parts.append(f"{grill_name} {format_reading(grill.celsius())}")
    for probe in snapshot.probes:
        if not is_fresh(probe.last_seen, now, probe_limit):
            parts.append(f"{names[probe.id]} stale")
        else:
            parts.append(f"{names[probe.id]} {format_reading(probe.core_celsius())}")
    parts.append(f"{len(state.ringing)} ringing, {len(state.silenced)} silenced")
    for source in snapshot.sources:
        if source.error is not None:
            parts.append(f"{source.name} unreachable, using last data")
    return " | ".join(parts)


def format_reading(celsius: float | None) -> str:
    """A temperature for the log, e.g. '68.2°C', or '--' if there's no reading."""
    return "--" if celsius is None else f"{celsius:.1f}°C"
