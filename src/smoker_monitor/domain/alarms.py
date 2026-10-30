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
- Gateway: alarm for each one that hasn't reported within its timeout, and
  "no gateway found" for a working source that should have one but sent none.
- Probe: alarm only for probes that are part of this cook (`live_probes`: ones
  the Pi has seen reporting since it started watching) and have gone quiet.
  A probe that was never live, e.g. left in a drawer, is ignored.

Batteries: alarm below the [alarms] percentage; 0 switches it off.

Grills (e.g. a Traeger), only while switched on, so a grill sitting in the
shed never alarms:
- gone quiet: no report within grill_timeout_minutes.
- off target: while cooking (not while lighting or heating up), its
  temperature is more than grill_off_target_c above or below its set
  temperature. Raising the set temperature mid-cook sets this off until the
  grill catches up.
- pellets low: below pellets_low_pct (if it has a pellet sensor); 0 switches
  it off.

Can't reach a source: when fetching from a source (e.g. ETI Cloud) fails, the
Pi can't tell a silent Gateway from a silent internet, so that source's devices
raise no timeout alarms. Instead, once it has failed for
source_unreachable_minutes, one "can't reach {source}" alarm goes off.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from smoker_monitor.domain.models import (
    NO_ALARMS,
    AlarmLimit,
    AlarmSettings,
    Grill,
    GrillMode,
    Snapshot,
)


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
    source_unreachable: bool = True  # the Pi can't get data from a source...
    source_unreachable_minutes: int = 5  # ...for this many minutes
    grill_timeout: bool = True  # a grill that's on has gone quiet...
    grill_timeout_minutes: int = 5  # ...for this many minutes
    grill_off_target: bool = True  # a cooking grill is too far from its set temperature...
    grill_off_target_c: int = 14  # ...by more than this many °C (14 °C is about 25 °F)
    pellets_low_pct: int = 20  # alarm below this; 0 switches it off


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
    SOURCE_UNREACHABLE = "source_unreachable"  # the Pi can't fetch from a source
    GRILL_TIMEOUT = "grill_timeout"
    GRILL_OFF_TARGET = "grill_off_target"  # value: grill °C, limit: its set °C
    PELLETS_LOW = "pellets_low"  # set by pellets_low_pct


@dataclass(frozen=True)
class Alarm:
    """One thing that is wrong right now.

    `kind` and `device` together identify the alarm (e.g. "probe_high on the
    white probe"), so it can be tracked over time and silenced. Wording it for
    a screen is the display's job, not this class's.

    `value` and `limit` depend on the kind:
    - temperature alarms: °C now, and the limit it crossed (None if unreadable)
    - timeouts: minutes since the device last reported, and the timeout
      (value is None for "no gateway found"; device is then the source key)
    - source unreachable: minutes it has been failing, and the limit
    - batteries: percent now, and the percentage it fell below
    """

    kind: AlarmKind
    device: str  # id of the Gateway or probe (e.g. "eti:G1"), or a source key ("eti")
    value: float | None
    limit: float | None

    @property
    def about_source(self) -> bool:
        """True if this is about a whole source ("eti"), not one device."""
        no_gateway = self.kind is AlarmKind.GATEWAY_TIMEOUT and self.value is None
        return no_gateway or self.kind is AlarmKind.SOURCE_UNREACHABLE


def evaluate(
    snapshot: Snapshot,
    now: datetime,
    rules: AlarmRules,
    custom: Mapping[str, AlarmSettings] | None = None,
    live_probes: Set[str] | None = None,
) -> tuple[Alarm, ...]:
    """Return every alarm that should be going off right now (empty if all is well).

    `custom` holds limits set on the Pi, keyed by device id; each side (high
    or low) that is set replaces ETI's for that device. `live_probes` holds the
    ids of probes seen reporting during this cook (see `fresh_probes`); only
    those can raise a "gone quiet" alarm.
    """
    custom = custom or {}
    live_probes = live_probes or frozenset()
    gateway_limit = timedelta(minutes=rules.gateway_timeout_minutes)
    probe_limit = timedelta(minutes=rules.probe_timeout_minutes)
    found: list[Alarm | None] = []

    # A failing source's devices can't be seen, so their "gone quiet" alarms
    # can't be judged; its own "can't reach" alarm stands in for them.
    failing = {s.key for s in snapshot.sources if s.failing_since is not None}
    found.extend(source_alarms(snapshot, now, rules, failing))

    for gateway in snapshot.gateways:
        if not is_fresh(gateway.last_seen, now, gateway_limit):
            if rules.gateway_timeout and gateway.source not in failing:
                found.append(
                    timeout_alarm(
                        AlarmKind.GATEWAY_TIMEOUT, gateway.id, gateway.last_seen, now, gateway_limit
                    )
                )
            continue

        found.append(
            check_battery(
                AlarmKind.GATEWAY_BATTERY,
                gateway.id,
                gateway.battery_pct,
                rules.gateway_battery_pct,
            )
        )
        pit = gateway.get_pit_temp()
        eti = gateway.pit_alarms
        mine = custom.get(gateway.id, NO_ALARMS)
        if rules.pit_high:
            found.append(
                check_limit(AlarmKind.PIT_HIGH, gateway.id, pit, eti.high, mine.high, True)
            )
        if rules.pit_low:
            found.append(check_limit(AlarmKind.PIT_LOW, gateway.id, pit, eti.low, mine.low, False))

    for probe in snapshot.probes:
        if not is_fresh(probe.last_seen, now, probe_limit):
            if rules.probe_timeout and probe.id in live_probes and probe.source not in failing:
                found.append(
                    timeout_alarm(
                        AlarmKind.PROBE_TIMEOUT, probe.id, probe.last_seen, now, probe_limit
                    )
                )
            continue

        found.append(
            check_battery(
                AlarmKind.PROBE_BATTERY, probe.id, probe.battery_pct, rules.probe_battery_pct
            )
        )
        core = probe.core_celsius()
        eti = probe.alarms
        mine = custom.get(probe.id, NO_ALARMS)
        if rules.probe_high:
            found.append(
                check_limit(AlarmKind.PROBE_HIGH, probe.id, core, eti.high, mine.high, True)
            )
        if rules.probe_low:
            found.append(check_limit(AlarmKind.PROBE_LOW, probe.id, core, eti.low, mine.low, False))

    for grill in snapshot.grills:
        found.extend(grill_alarms(grill, now, rules, grill.source in failing))

    return tuple(alarm for alarm in found if alarm is not None)


def grill_alarms(grill: Grill, now: datetime, rules: AlarmRules, failing: bool) -> list[Alarm]:
    """A grill's alarms (see the module docstring). `failing`: its source is down."""
    if not grill.mode.is_on:
        return []  # switched off: nothing to watch
    limit = timedelta(minutes=rules.grill_timeout_minutes)
    if not is_fresh(grill.last_seen, now, limit):
        if rules.grill_timeout and not failing:
            return [timeout_alarm(AlarmKind.GRILL_TIMEOUT, grill.id, grill.last_seen, now, limit)]
        return []
    if grill.mode is GrillMode.COOLING_DOWN:
        return []  # finished cooking: temperature and pellets no longer matter

    found: list[Alarm] = []
    pellets, below = grill.pellet_pct, rules.pellets_low_pct
    if below and pellets is not None and pellets < below:
        found.append(Alarm(AlarmKind.PELLETS_LOW, grill.id, pellets, below))
    temp, target = grill.celsius(), grill.set_temp_celsius
    if (
        rules.grill_off_target
        and grill.mode is GrillMode.COOKING
        and temp is not None
        and target is not None
        and abs(temp - target) > rules.grill_off_target_c
    ):
        found.append(Alarm(AlarmKind.GRILL_OFF_TARGET, grill.id, temp, target))
    return found


def fresh_probes(snapshot: Snapshot, now: datetime, rules: AlarmRules) -> frozenset[str]:
    """Ids of the probes reporting right now (within the probe timeout).

    The service adds these to its `live_probes` set every poll, so a probe that
    was ever fresh during the cook is remembered, and can then raise a "gone
    quiet" alarm if it stops reporting.
    """
    limit = timedelta(minutes=rules.probe_timeout_minutes)
    return frozenset(p.id for p in snapshot.probes if is_fresh(p.last_seen, now, limit))


def source_alarms(
    snapshot: Snapshot, now: datetime, rules: AlarmRules, failing: Set[str]
) -> list[Alarm]:
    """Alarms about whole sources rather than single devices.

    - "Can't reach {source}": its fetches have failed for source_unreachable_minutes.
    - "No gateway found from {source}": it's working, should have a Gateway, and
      reported none.
    """
    found: list[Alarm] = []
    unreachable_limit = timedelta(minutes=rules.source_unreachable_minutes)
    gateway_limit = timedelta(minutes=rules.gateway_timeout_minutes)
    for source in snapshot.sources:
        if source.failing_since is not None:
            if rules.source_unreachable and not is_fresh(
                source.failing_since, now, unreachable_limit
            ):
                found.append(
                    timeout_alarm(
                        AlarmKind.SOURCE_UNREACHABLE,
                        source.key,
                        source.failing_since,
                        now,
                        unreachable_limit,
                    )
                )
        elif (
            rules.gateway_timeout
            and source.expects_gateway
            and not any(g.source == source.key for g in snapshot.gateways)
        ):
            found.append(Alarm(AlarmKind.GATEWAY_TIMEOUT, source.key, None, minutes(gateway_limit)))
    return found


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
