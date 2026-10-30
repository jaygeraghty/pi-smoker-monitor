"""Traeger source: your Traeger grill (and its probes) as a Snapshot.

READ-ONLY: it listens to the grill's status (see traeger_cloud.py) and never
sends anything but Traeger's "send me your status" request.

Unlike ETI, Traeger pushes its status rather than being asked for it: while
the grill is on, a message arrives every few seconds. So this source keeps a
listener running in the background, and each fetch() turns the latest message
from each grill into a Snapshot.

This module has two halves:
- `TraegerSource`: the background listener and fetch().
- Mapping: pure functions (`to_snapshot` and friends), testable without a
  network.

What a status message looks like. Field names come from the open-source Home
Assistant Traeger integrations, and were confirmed by a real capture from a
Pro 575 on firmware 02.02.05, switched off (tests/fixtures/traeger_off.json):
    {"status": {
        "grill": 16, "set": 75,         # grill and set temperature
        "units": 0,                     # 0 = °C, 1 = °F (for every temperature)
        "system_status": 99,            # what it's doing, see GRILL_MODES
        "time": 1788634514,             # when the grill sent it (epoch seconds)
        "connected": false,
        "pellet_level": 30,             # %, on grills with a pellet sensor
        "probe": 0, "probe_set": 0, "probe_con": 0, "probe_alarm_fired": 0,
        "acc": [ {"type": "probe", "uuid": "p0", "channel": "p0", "con": 0,
                  "probe": {"get_temp": 0, "set_temp": 0, "alarm_fired": 0}} ]
    }, "details": {...}, "settings": {...}, "usage": {...}, ...}
Still unconfirmed until a capture of a cook: the codes while lit (igniting 4
to cooling down 8) and a plugged-in probe.

The list of grills (GET /users/self "things") gives each grill's thingName,
friendlyName (your name for it) and deviceTypeId (its model, see MODELS).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from smoker_monitor.config import TraegerSettings
from smoker_monitor.domain.models import (
    AlarmLimit,
    AlarmSettings,
    Grill,
    GrillMode,
    Probe,
    Reading,
    Snapshot,
)
from smoker_monitor.domain.units import f_to_c
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.traeger_cloud import GrillMessage, TraegerCloud

log = logging.getLogger(__name__)

SOURCE_KEY = "traeger"

# system_status codes -> what the grill is doing.
GRILL_MODES = {
    99: GrillMode.OFFLINE,
    9: GrillMode.SHUT_DOWN,
    8: GrillMode.COOLING_DOWN,
    7: GrillMode.COOKING,  # a custom cook (a programme on the grill)
    6: GrillMode.COOKING,  # a manual cook
    5: GrillMode.PREHEATING,
    4: GrillMode.IGNITING,
    3: GrillMode.IDLE,
    2: GrillMode.SLEEPING,
}

# deviceTypeId -> model name, where known.
MODELS = {"2104": "Pro 575"}

# Longest grill name shown, so a long one can't break a screen's layout.
MAX_NAME_LENGTH = 20

# How far ahead of our clock a grill's own timestamp may be and still be believed.
CLOCK_SLACK_SECONDS = 300

# How long the first fetch after connecting waits for each grill's first
# message (once per connection). A grill that's off still answers (the cloud
# resends its last status), so this is only a backstop.
FIRST_MESSAGE_SECONDS = 5.0

# How long the first fetch waits to hear the list of grills.
STARTUP_SECONDS = 20.0

# How long to wait before reconnecting after the connection drops.
RECONNECT_SECONDS = 10.0


class TraegerSource:
    """A Source that reads your Traeger grills (and their probes). Read-only."""

    key = SOURCE_KEY
    name = "Traeger"
    expects_gateway = False  # a grill reports its own temperature: no hub

    def __init__(self, settings: TraegerSettings, cloud: TraegerCloud | None = None) -> None:
        self.poll_seconds = settings.poll_seconds
        self._cloud = cloud or TraegerCloud(settings)
        self._grills: list[dict[str, Any]] | None = None  # from the cloud, once known
        self._latest: dict[str, GrillMessage] = {}  # thingName -> its latest message
        self._listening_since = datetime.now(UTC)
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None  # why the listener stopped, if it did
        self._ready = asyncio.Event()  # set once the list of grills is known
        self._heard_all = asyncio.Event()  # set once every grill has sent a message
        self._waited_for_first = False  # the one-off wait after connecting is done

    async def fetch(self) -> Snapshot:
        """The latest status from every grill. Raises SourceError if not listening."""
        if self._task is None or self._task.done():
            self._start()
        try:
            await asyncio.wait_for(self._ready.wait(), STARTUP_SECONDS)
        except TimeoutError:
            raise SourceError("Traeger didn't answer in time") from None
        if self._error is not None:
            raise SourceError(self._error)
        # Just connected: each grill's first message (asked for straight after
        # connecting) takes a second or two. Wait briefly for it, once, so a
        # one-off `smoker snapshot` doesn't show a lit grill as offline. Only
        # once: a grill that never answers mustn't slow down every poll.
        if not self._waited_for_first:
            self._waited_for_first = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._heard_all.wait(), FIRST_MESSAGE_SECONDS)
        assert self._grills is not None  # set before _ready
        return to_snapshot(self._grills, self._latest, self._listening_since)

    async def close(self) -> None:
        """Stop listening and close the connection."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._cloud.close()

    # ------------------------------------------------------------ helpers ---

    def _start(self) -> None:
        self._ready = asyncio.Event()
        self._heard_all = asyncio.Event()
        self._waited_for_first = False
        self._error = None
        self._task = asyncio.create_task(self._listen_forever())

    async def _listen_forever(self) -> None:
        """Find the grills, then listen to them, reconnecting as needed.

        Any failure stops it with self._error set; the next fetch() reports
        that error and starts it again.
        """
        try:
            self._grills = await self._cloud.grills()
            self._listening_since = datetime.now(UTC)
            self._ready.set()
            things = [grill["thingName"] for grill in self._grills]
            while True:
                # Returns when its MQTT address is about to expire: go round
                # again for a fresh one.
                await self._cloud.listen(things, self._remember)
        except asyncio.CancelledError:
            raise
        except SourceError as e:
            self._error = str(e)
        except Exception:
            log.exception("Traeger listener failed unexpectedly")
            self._error = "Traeger failed unexpectedly (see the log)"
        self._ready.set()  # wake a waiting fetch() so it sees the error

    def _remember(self, message: GrillMessage) -> None:
        self._latest[message.thing] = message
        grills = self._grills or []
        if all(grill["thingName"] in self._latest for grill in grills):
            self._heard_all.set()


# ------------------------------------------------------------------ mapping ---


def to_snapshot(
    grills: list[dict[str, Any]],
    latest: Mapping[str, GrillMessage],
    listening_since: datetime,
) -> Snapshot:
    """A Snapshot of every grill and its probes.

    A grill we haven't heard from since we started listening shows as offline
    (a grill that's switched off sends nothing).
    """
    found_grills: list[Grill] = []
    probes: list[Probe] = []
    for info in grills:
        thing = str(info["thingName"])
        message = latest.get(thing)
        if message is None:
            found_grills.append(silent_grill(info, listening_since))
            continue
        status = message.payload.get("status")
        status = status if isinstance(status, dict) else {}
        seen = status_time(status, message.received_at)
        found_grills.append(to_grill(info, message.payload, seen))
        probes.extend(to_probes(thing, status, seen))
    return Snapshot(
        taken_at=datetime.now(UTC), gateways=(), probes=tuple(probes), grills=tuple(found_grills)
    )


def silent_grill(info: Mapping[str, Any], since: datetime) -> Grill:
    """A grill that hasn't said anything: offline, no readings."""
    return Grill(
        serial=str(info["thingName"]),
        label=model(info),
        last_seen=since,
        connected=False,
        mode=GrillMode.OFFLINE,
        temp=None,
        set_temp_celsius=None,
        name=friendly_name(info),
        source=SOURCE_KEY,
    )


def to_grill(info: Mapping[str, Any], payload: Mapping[str, Any], seen: datetime) -> Grill:
    """One grill from its whole message ("status", plus "features" and "settings")."""
    status, features, settings = (
        section(payload, key) for key in ("status", "features", "settings")
    )
    in_f = uses_fahrenheit(status)
    code = status.get("system_status")
    mode = GRILL_MODES.get(code, GrillMode.UNKNOWN) if isinstance(code, int) else GrillMode.UNKNOWN
    return Grill(
        serial=str(info["thingName"]),
        label=model(info),
        last_seen=seen,
        connected=status.get("connected") is True,
        mode=mode,
        temp=Reading(to_celsius(status.get("grill"), in_f), seen),
        set_temp_celsius=to_target(status.get("set"), in_f),
        # Only believed with a pellet sensor fitted: without one it may read
        # 0, which would set off "pellets low" for no reason.
        pellet_pct=(
            percent(status.get("pellet_level"))
            if features.get("pellet_sensor_connected") == 1
            else None
        ),
        ambient_celsius=to_celsius(status.get("ambient"), in_f),
        wifi_dbm=whole_number(settings.get("rssi")),
        keep_warm=status.get("keepwarm") == 1,
        super_smoke=status.get("smoke") == 1,
        name=friendly_name(info),
        source=SOURCE_KEY,
    )


def to_probes(thing: str, status: Mapping[str, Any], seen: datetime) -> list[Probe]:
    """The grill's plugged-in probes.

    Newer firmware lists probes in "acc" (accessories); older grills have one
    built-in probe as "probe", "probe_set" and "probe_con". A probe that isn't
    plugged in is left out.
    """
    in_f = uses_fahrenheit(status)
    accessories = status.get("acc")
    found: list[Probe] = []
    if isinstance(accessories, list):
        for number, acc in enumerate(accessories, start=1):
            if not isinstance(acc, dict) or acc.get("type") != "probe" or acc.get("con") != 1:
                continue
            inner = acc.get("probe")
            reading: dict[str, Any] = inner if isinstance(inner, dict) else {}
            which = str(acc.get("uuid") or acc.get("channel") or number)
            found.append(
                to_probe(
                    f"{thing}-{which}",
                    to_celsius(reading.get("get_temp"), in_f),
                    to_target(reading.get("set_temp"), in_f),
                    reading.get("alarm_fired") in (True, 1),
                    seen,
                )
            )
        if found:
            return found
    if status.get("probe_con") == 1:
        found.append(
            to_probe(
                f"{thing}-probe",
                to_celsius(status.get("probe"), in_f),
                to_target(status.get("probe_set"), in_f),
                status.get("probe_alarm_fired") == 1,
                seen,
            )
        )
    return found


def to_probe(
    serial: str, celsius: float | None, target: float | None, fired: bool, seen: datetime
) -> Probe:
    """A Traeger food probe. Its target, if set, becomes its high alarm."""
    high = AlarmLimit(enabled=True, celsius=target, alarming=fired) if target is not None else None
    return Probe(
        serial=serial,
        label="Traeger probe",
        battery_pct=None,  # wired: no battery
        last_seen=seen,
        sensors=(Reading(celsius, seen),),
        alarms=AlarmSettings(high=high, low=None),
        source=SOURCE_KEY,
    )


# ------------------------------------------------------------------ helpers ---


def status_time(status: Mapping[str, Any], received_at: float) -> datetime:
    """When the grill last reported this status.

    Traeger's cloud keeps a grill's last status and sends it again when asked,
    even if the grill has been off for weeks (a real capture: received in
    October, "time" from 5 September). So "time", the grill's own timestamp,
    is what says how fresh the status is. Without a believable one (missing,
    or later than when it arrived), the time it arrived is used instead.
    """
    stamp = status.get("time")
    if isinstance(stamp, bool) or not isinstance(stamp, int | float):
        return datetime.fromtimestamp(received_at, UTC)
    if 0 < stamp <= received_at + CLOCK_SLACK_SECONDS:
        return datetime.fromtimestamp(stamp, UTC)
    return datetime.fromtimestamp(received_at, UTC)


def section(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """One part of a message (e.g. "status"), or empty if it's missing or odd."""
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def whole_number(value: object) -> int | None:
    """A whole number, or None if missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def uses_fahrenheit(status: Mapping[str, Any]) -> bool:
    """True if the grill's temperatures are °F ("units" 1), False for °C (0)."""
    return status.get("units") != 0


def to_celsius(value: object, in_fahrenheit: bool) -> float | None:
    """A temperature in °C, or None if it's missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    celsius = f_to_c(float(value)) if in_fahrenheit else float(value)
    return round(celsius, 1)


def to_target(value: object, in_fahrenheit: bool) -> float | None:
    """A set (target) temperature in °C, or None if nothing is set (Traeger sends 0)."""
    return None if value == 0 else to_celsius(value, in_fahrenheit)


def percent(value: object) -> int | None:
    """A whole percentage 0-100, or None if missing or out of range."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if 0 <= value <= 100 else None


def model(info: Mapping[str, Any]) -> str:
    """The grill's model name, e.g. "Pro 575", or "Traeger" if unknown."""
    return MODELS.get(str(info.get("deviceTypeId")), "Traeger")


def friendly_name(info: Mapping[str, Any]) -> str | None:
    """Your name for the grill, if you've given one (tidied, at most 20 characters)."""
    name = info.get("friendlyName")
    if not isinstance(name, str):
        return None
    return " ".join(name.split())[:MAX_NAME_LENGTH].strip() or None
