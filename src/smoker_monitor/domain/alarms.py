"""Alarm rules: decide what is wrong with the cook right now.

Pure logic, no network or hardware: `evaluate` takes a Snapshot and returns
the alarms that should be going off at this moment. Remembering alarms over
time (delays, silencing) is a separate job for the alarm state machine.

Fresh data only: a device counts as fresh if it reported within its timeout
([alarms] gateway_timeout_minutes / probe_timeout_minutes). Temperature and
battery alarms only look at fresh devices, so old data (a probe left in a
drawer, a Gateway that went offline) can never set them off. Missing or old
data is the job of the timeout alarms instead.

How a temperature limit (a high or low alarm) is checked:
1. Is that kind of alarm switched on in [alarms]? If not, skip it.
2. Which limit applies? A custom (Pi-side) limit if set, otherwise ETI's.
3. Is that limit enabled? If not, skip it.
4. Alarm if ETI says it is alarming (only when using ETI's own limit), or if
   the temperature has reached the limit: >= for high, <= for low.

Timeouts:
- Gateway: alarm if it hasn't reported within its timeout, or is missing.
- Probe: alarm only for probes that are part of this cook (`live_probes`: ones
  the Pi has seen reporting since it started watching) and have gone quiet.
  A probe that was never live, e.g. left in a drawer, is ignored.

Batteries: alarm below the [alarms] percentage; 0 switches it off.

Can't reach ETI Cloud: when fetching fails, the Pi can't tell a silent Gateway
from a silent internet, so `while_unreachable` swaps the timeout alarms for a
single "can't reach ETI Cloud" alarm once it has failed for long enough.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from smoker_monitor.domain.models import NO_ALARMS, AlarmLimit, AlarmSettings, Snapshot

# Device name used for an alarm about a Gateway that isn't in the data at all.
NO_DEVICE = ""


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
    eti_unreachable: bool = True  # the Pi can't get data from ETI Cloud...
    eti_unreachable_minutes: int = 5  # ...for this many minutes


class AlarmKind(StrEnum):
    """What kind of thing is wrong. Values match the [alarms] setting names."""

    PIT_HIGH = "pit_high"
    PIT_LOW = "pit_low"
    PROBE_HIGH = "probe_high"  # meat reached its target
    PROBE_LOW = "probe_low"
    GATEWAY_TIMEOUT = "gateway_timeout"
    PROBE_TIMEOUT = "probe_timeout"
    GATEWAY_BATTERY = "gateway_battery"  # set by gateway_battery_pct
    PROBE_BATTERY = "probe_battery"  # set by probe_battery_pct
    ETI_UNREACHABLE = "eti_unreachable"  # the Pi can't fetch from ETI Cloud


# Alarms that judge "has this device gone quiet?". They can't be judged while
# ETI Cloud itself can't be reached.
TIMEOUT_KINDS = frozenset({AlarmKind.GATEWAY_TIMEOUT, AlarmKind.PROBE_TIMEOUT})


@dataclass(frozen=True)
class Alarm:
    """One thing that is wrong right now.

    `kind` and `device` together identify the alarm (e.g. "probe_high on the
    white probe"), so it can be tracked over time and silenced. Wording it for
    a screen is the display's job, not this class's.

    `value` and `limit` depend on the kind:
    - temperature alarms: °C now, and the limit it crossed (None if unreadable)
    - timeouts: minutes since the device last reported, and the timeout
      (value is None if the Gateway is missing altogether)
    - batteries: percent now, and the percentage it fell below
    """

    kind: AlarmKind
    device: str  # serial of the Gateway or probe (NO_DEVICE if missing)
    value: float | None
    limit: float | None


def evaluate(
    snapshot: Snapshot,
    now: datetime,
    rules: AlarmRules,
    custom: Mapping[str, AlarmSettings] | None = None,
    live_probes: Set[str] | None = None,
) -> tuple[Alarm, ...]:
    """Return every alarm that should be going off right now (empty if all is well).

    `custom` holds limits set on the Pi, keyed by device serial; each side (high
    or low) that is set replaces ETI's for that device. `live_probes` holds the
    serials of probes seen reporting during this cook (see `fresh_probes`); only
    those can raise a "gone quiet" alarm.
    """
    custom = custom or {}
    live_probes = live_probes or frozenset()
    gateway_limit = timedelta(minutes=rules.gateway_timeout_minutes)
    probe_limit = timedelta(minutes=rules.probe_timeout_minutes)
    found: list[Alarm | None] = []

    gateway = snapshot.gateway
    if gateway is None:
        if rules.gateway_timeout:
            found.append(Alarm(AlarmKind.GATEWAY_TIMEOUT, NO_DEVICE, None, minutes(gateway_limit)))
    elif not is_fresh(gateway.last_seen, now, gateway_limit):
        if rules.gateway_timeout:
            found.append(
                timeout_alarm(
                    AlarmKind.GATEWAY_TIMEOUT, gateway.serial, gateway.last_seen, now, gateway_limit
                )
            )
    else:
        found.append(
            check_battery(
                AlarmKind.GATEWAY_BATTERY,
                gateway.serial,
                gateway.battery_pct,
                rules.gateway_battery_pct,
            )
        )
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
        if not is_fresh(probe.last_seen, now, probe_limit):
            if rules.probe_timeout and probe.serial in live_probes:
                found.append(
                    timeout_alarm(
                        AlarmKind.PROBE_TIMEOUT, probe.serial, probe.last_seen, now, probe_limit
                    )
                )
            continue

        found.append(
            check_battery(
                AlarmKind.PROBE_BATTERY, probe.serial, probe.battery_pct, rules.probe_battery_pct
            )
        )
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


def fresh_probes(snapshot: Snapshot, now: datetime, rules: AlarmRules) -> frozenset[str]:
    """Serials of the probes reporting right now (within the probe timeout).

    The service adds these to its `live_probes` set every poll, so a probe that
    was ever fresh during the cook is remembered, and can then raise a "gone
    quiet" alarm if it stops reporting.
    """
    limit = timedelta(minutes=rules.probe_timeout_minutes)
    return frozenset(p.serial for p in snapshot.probes if is_fresh(p.last_seen, now, limit))


def while_unreachable(
    alarms: tuple[Alarm, ...], failing_since: datetime, now: datetime, rules: AlarmRules
) -> tuple[Alarm, ...]:
    """The alarms to use while fetching from ETI Cloud keeps failing.

    `alarms` were evaluated on the last good data. Its timeout alarms are
    dropped (we can't see the devices, so we don't know if they are quiet) and,
    once fetching has failed for eti_unreachable_minutes, a "can't reach ETI
    Cloud" alarm is added. `failing_since` is the time of the first failed
    fetch in a row.
    """
    kept = tuple(alarm for alarm in alarms if alarm.kind not in TIMEOUT_KINDS)
    limit = timedelta(minutes=rules.eti_unreachable_minutes)
    if not rules.eti_unreachable or is_fresh(failing_since, now, limit):
        return kept
    return (*kept, timeout_alarm(AlarmKind.ETI_UNREACHABLE, NO_DEVICE, failing_since, now, limit))


# ---------------------------------------------------------------- checks ---


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
        return Alarm(kind=kind, device=device, value=celsius, limit=limit.celsius)
    return None


def has_reached(celsius: float | None, limit_celsius: float | None, high: bool) -> bool:
    """True if the temperature has reached the limit: >= for high, <= for low.

    False if either is unknown: no temperature means no temperature alarm (the
    timeout rules cover missing data), and an unreadable limit can't be checked.
    """
    if celsius is None or limit_celsius is None:
        return False
    return celsius >= limit_celsius if high else celsius <= limit_celsius


def check_battery(kind: AlarmKind, device: str, pct: int | None, below: int) -> Alarm | None:
    """Alarm if the battery is below `below` percent. 0 switches it off; unknown is ignored."""
    if below == 0 or pct is None or pct >= below:
        return None
    return Alarm(kind=kind, device=device, value=pct, limit=below)


def timeout_alarm(
    kind: AlarmKind, device: str, last_seen: datetime, now: datetime, limit: timedelta
) -> Alarm:
    """An alarm saying how long a device has been quiet, against its timeout."""
    return Alarm(kind=kind, device=device, value=minutes(now - last_seen), limit=minutes(limit))


# --------------------------------------------------------------- helpers ---


def is_fresh(last_seen: datetime, now: datetime, limit: timedelta) -> bool:
    """True if the device reported within `limit`. Exactly on the limit still counts."""
    return now - last_seen <= limit


def minutes(age: timedelta) -> float:
    """A time difference in minutes, e.g. 90 seconds -> 1.5."""
    return age.total_seconds() / 60
