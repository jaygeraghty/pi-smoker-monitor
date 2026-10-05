"""Alarm rules: decide what is wrong with the cook right now.

Pure logic, no network or hardware: `evaluate` takes a Snapshot and returns
the alarms that should be going off at this moment. Remembering alarms over
time (delays, silencing) is a separate job for the alarm state machine.

How a temperature limit (a high or low alarm) is checked:
1. Is that kind of alarm switched on in [alarms]? If not, skip it.
2. Which limit applies? A custom (Pi-side) limit if set, otherwise ETI's.
3. Is that limit enabled? If not, skip it.
4. Alarm if ETI says it is alarming (only when using ETI's own limit), or if
   the temperature has reached the limit: >= for high, <= for low.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smoker_monitor.domain.models import NO_ALARMS, AlarmLimit, AlarmSettings, Snapshot


@dataclass(frozen=True)
class AlarmRules:
    """Which alarms are switched on, and their thresholds.

    Any alarm that is switched on wakes you. The defaults are deliberately
    cautious: everything on except "probe too cold", so a config file without
    an [alarms] section still protects you. Loaded from config.toml by config.py.
    """

    pit_high: bool = True  # pit over its high limit
    pit_low: bool = True  # pit under its low limit (fire going out)
    probe_high: bool = True  # meat reached its target
    probe_low: bool = False  # meat under its low limit (rarely useful)
    gateway_timeout: bool = True  # no data from the Gateway...
    gateway_timeout_minutes: int = 5  # ...for this many minutes
    probe_timeout: bool = True  # a probe in use has gone quiet...
    probe_timeout_minutes: int = 5  # ...for this many minutes
    gateway_battery_pct: int = 10  # alarm below this; 0 switches it off
    probe_battery_pct: int = 10  # alarm below this; 0 switches it off


class AlarmKind(StrEnum):
    """What kind of thing is wrong. Values match the [alarms] switch names."""

    PIT_HIGH = "pit_high"
    PIT_LOW = "pit_low"
    PROBE_HIGH = "probe_high"  # meat reached its target
    PROBE_LOW = "probe_low"


@dataclass(frozen=True)
class Alarm:
    """One thing that is wrong right now.

    `kind` and `device` together identify the alarm (e.g. "probe_high on the
    white probe"), so it can be tracked over time and silenced. Wording it for
    a screen is the display's job, not this class's.
    """

    kind: AlarmKind
    device: str  # serial of the Gateway or probe
    celsius: float | None  # the temperature that triggered it
    limit_celsius: float | None  # the limit it crossed (None if unreadable)


def evaluate(
    snapshot: Snapshot,
    now: datetime,
    rules: AlarmRules,
    custom: Mapping[str, AlarmSettings] | None = None,
) -> tuple[Alarm, ...]:
    """Return every alarm that should be going off right now (empty if all is well).

    `custom` holds limits set on the Pi, keyed by device serial; each side (high
    or low) that is set replaces ETI's for that device. `now` is not used yet:
    it is for the timeout rules, which come next.
    """
    custom = custom or {}
    found: list[Alarm | None] = []

    gateway = snapshot.gateway
    if gateway is not None:
        pit = gateway.get_pit_temp()
        eti = gateway.pit_alarms
        mine = custom.get(gateway.serial, NO_ALARMS)
        if rules.pit_high:
            found.append(
                check_limit(AlarmKind.PIT_HIGH, gateway.serial, pit, eti.high, mine.high, True)
            )
        if rules.pit_low:
            found.append(
                check_limit(AlarmKind.PIT_LOW, gateway.serial, pit, eti.low, mine.low, False)
            )

    for probe in snapshot.probes:
        core = probe.core_celsius()
        eti = probe.alarms
        mine = custom.get(probe.serial, NO_ALARMS)
        if rules.probe_high:
            found.append(
                check_limit(AlarmKind.PROBE_HIGH, probe.serial, core, eti.high, mine.high, True)
            )
        if rules.probe_low:
            found.append(
                check_limit(AlarmKind.PROBE_LOW, probe.serial, core, eti.low, mine.low, False)
            )

    return tuple(alarm for alarm in found if alarm is not None)


def check_limit(
    kind: AlarmKind,
    device: str,
    celsius: float | None,
    eti_limit: AlarmLimit | None,
    custom_limit: AlarmLimit | None,
    high: bool,
) -> Alarm | None:
    """Check one limit (a high or a low) on one device. Returns an Alarm or None.

    A custom limit, when set, completely replaces ETI's, including ETI's
    "alarming" flag, because that flag is about ETI's number, not ours.
    """
    limit = custom_limit if custom_limit is not None else eti_limit
    if limit is None or not limit.enabled:
        return None

    eti_says_alarming = custom_limit is None and limit.alarming
    if eti_says_alarming or has_reached(celsius, limit.celsius, high):
        return Alarm(kind=kind, device=device, celsius=celsius, limit_celsius=limit.celsius)
    return None


def has_reached(celsius: float | None, limit_celsius: float | None, high: bool) -> bool:
    """True if the temperature has reached the limit: >= for high, <= for low.

    False if either is unknown: no temperature means no temperature alarm (the
    timeout rules cover missing data), and an unreadable limit can't be checked.
    """
    if celsius is None or limit_celsius is None:
        return False
    return celsius >= limit_celsius if high else celsius <= limit_celsius
