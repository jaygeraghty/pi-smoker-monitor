"""The status file: everything the monitor knows, written as JSON every poll.

The monitor service writes it; screens read it (the Pi's display, and later a
page on your phone). Keeping the screens separate means a bug in a screen can
never stop an alarm.

The file describes the cook, not the brand, in three lists:
- pits: every pit (cooking chamber) temperature, whatever reports it: the RFX
  Gateway's pit probe, or a grill such as a Traeger. "kind" says which
  ("gateway" or "grill"). Each has its temperature, set temperature,
  alarm limits, what the fan is doing, and "details": brand-specific extras
  as ready-to-print label/value lines (e.g. Battery 75%).
- probes: food temperatures, with each probe's name, ring colours and target.
- sources: whether each data source (e.g. ETI Cloud) is working.

Limits can come from the ETI app or be set on the Pi (see service/settings.py).
Each pit and probe says which in "high_origin"/"low_origin" (pits) or
"target_origin"/"low_origin" (probes): "eti" or "pi". The limits shown are the
ones in force. "alarms_off" lists the alarms switched off in the rules, so a
screen can warn that something won't wake you.

Every pit and probe names its "source", so a screen can show where it came
from (e.g. "ETI Cloud", later "Traeger").

Every pit and probe has an "id" (e.g. "eti:PROBE-1") so alarms can point at
it; screens never show it. "fresh" is worked out here from the [alarms]
timeouts, so screens never need the alarm rules. Each alarm carries its
plain-English message, so screens don't need any alarm wording either.

Written atomically (to a temporary file, then swapped in), so a screen never
reads a half-written file. A screen can also check `updated_at`: if it stops
changing, the monitor itself has stopped.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarm_state import AlarmState, TrackedAlarm
from smoker_monitor.domain.alarms import AlarmRules, is_fresh
from smoker_monitor.domain.models import (
    NO_ALARMS,
    AlarmLimit,
    AlarmSettings,
    Gateway,
    Grill,
    GrillMode,
    Probe,
    Snapshot,
)
from smoker_monitor.domain.names import gateway_names, grill_names, probe_names
from smoker_monitor.report import (
    describe_alarm,
    format_dbm,
    format_fan_activity,
    format_percent,
)

STATUS_FILE = "status.json"

# Bump this when a change could break a screen: a field removed or renamed, or
# its meaning changed. Adding a field doesn't need a bump: screens ignore
# fields they don't know.
SCHEMA_VERSION = 2


# ------------------------------------------------------------- helpers ---


def limit_c(limit: AlarmLimit | None) -> float | None:
    """An alarm limit in °C, or None if it isn't set up or is switched off."""
    if limit is None or not limit.enabled:
        return None
    return limit.celsius


def effective_limit(eti: AlarmLimit | None, mine: AlarmLimit | None) -> tuple[float | None, str]:
    """The limit in force (°C, or None for none) and where it came from ("eti" or "pi").

    Your own limit, when set, replaces ETI's, as in alarms.check_limit.
    """
    if mine is not None:
        return limit_c(mine), "pi"
    return limit_c(eti), "eti"


def alarms_off(rules: AlarmRules) -> list[str]:
    """Names of the alarms that are switched off, e.g. ["probe_low"]."""
    off = [
        name
        for name in (
            "pit_high",
            "pit_low",
            "probe_high",
            "probe_low",
            "gateway_timeout",
            "probe_timeout",
            "source_unreachable",
            "grill_timeout",
            "grill_off_target",
        )
        if not getattr(rules, name)
    ]
    off += [
        name
        for name in ("gateway_battery_pct", "probe_battery_pct", "pellets_low_pct")
        if getattr(rules, name) == 0
    ]
    return off


def alarm_status(tracked: TrackedAlarm, snapshot: Snapshot) -> dict[str, Any]:
    """One alarm, with its plain-English message."""
    alarm = tracked.alarm
    return {
        "kind": alarm.kind.value,
        # The id of the pit or probe it's about; None when it's about a whole
        # source ("No gateway found", "Can't reach ...").
        "device": None if alarm.about_source else alarm.device,
        "source": snapshot.source_name(alarm.device) if alarm.about_source else None,
        "status": tracked.status.value,  # pending, ringing or silenced
        "value": alarm.value,
        "limit": alarm.limit,
        "since": tracked.first_seen.isoformat(),
        "message": describe_alarm(alarm, snapshot),
    }


def build_status(
    snapshot: Snapshot,
    state: AlarmState,
    now: datetime,
    rules: AlarmRules,
    custom: Mapping[str, AlarmSettings] | None = None,
) -> dict[str, Any]:
    """Everything a screen needs, as plain data ready for JSON.

    `rules` supplies the timeouts that decide whether each device is fresh.
    `custom` holds the limits set on the Pi, by device id.
    """
    custom = custom or {}
    names = probe_names(snapshot.probes)
    hubs = gateway_names(snapshot.gateways)
    grills = grill_names(snapshot.grills)
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": now.isoformat(),
        "sound_on": state.sound_on,
        "alarms_off": alarms_off(rules),
        "sources": [
            {"name": source.name, "ok": source.error is None, "error": source.error}
            for source in snapshot.sources
        ],
        "alarms": [alarm_status(tracked, snapshot) for tracked in state.tracked.values()],
        "pits": [
            pit_status(
                gateway,
                hubs[gateway.id],
                snapshot.source_name(gateway.source),
                now,
                rules,
                custom.get(gateway.id, NO_ALARMS),
            )
            for gateway in snapshot.gateways
        ]
        + [
            grill_status(grill, grills[grill.id], snapshot.source_name(grill.source), now, rules)
            for grill in snapshot.grills
        ],
        "probes": [
            probe_status(
                probe,
                names[probe.id],
                snapshot.source_name(probe.source),
                now,
                rules,
                custom.get(probe.id, NO_ALARMS),
            )
            for probe in snapshot.probes
        ],
    }


def pit_status(
    gateway: Gateway,
    name: str,
    source: str,
    now: datetime,
    rules: AlarmRules,
    mine: AlarmSettings = NO_ALARMS,
) -> dict[str, Any]:
    """A Gateway (and its fan, if any) as a pit. `name` is its display name (see
    domain/names.py); `source` is its source's name; `mine` is the limits set on the Pi."""
    fresh = is_fresh(gateway.last_seen, now, timedelta(minutes=rules.gateway_timeout_minutes))

    # What the fan is doing, and the temperature it's aiming for (only
    # meaningful while it's connected).
    billows = gateway.fan
    fan: str | None = None
    set_c: float | None = None
    fan_output_pct: int | None = None
    if billows is not None and not billows.connected:
        fan = "not connected"
    elif billows is not None:
        fan = format_fan_activity(billows)
        set_c = billows.set_temp_celsius
        fan_output_pct = billows.output_pct

    details = [
        {"label": "Battery", "value": format_percent(gateway.battery_pct)},
        {"label": "Wi-Fi", "value": format_dbm(gateway.wifi_dbm)},
    ]
    high_c, high_origin = effective_limit(gateway.pit_alarms.high, mine.high)
    low_c, low_origin = effective_limit(gateway.pit_alarms.low, mine.low)

    return {
        "id": gateway.id,
        "source": source,
        "kind": "gateway",  # a hub carrying a pit probe; later also "grill" (Traeger)
        "name": name,
        "fresh": fresh,
        "last_seen": gateway.last_seen.isoformat(),
        "temp_c": gateway.get_pit_temp(),
        "set_c": set_c,
        "high_c": high_c,
        "low_c": low_c,
        "high_origin": high_origin,
        "low_origin": low_origin,
        "fan": fan,
        "fan_output_pct": fan_output_pct,
        "pellet_pct": None,  # only grills have pellets
        "details": details,
    }


def grill_status(
    grill: Grill, name: str, source: str, now: datetime, rules: AlarmRules
) -> dict[str, Any]:
    """A grill (e.g. a Traeger) as a pit. `name` is its display name (see
    domain/names.py); `source` is its source's name.

    high_c and low_c are the "off target" limits (set temperature plus or minus
    [alarms] grill_off_target_c), shown only while they apply: when cooking.
    """
    fresh = is_fresh(grill.last_seen, now, timedelta(minutes=rules.grill_timeout_minutes))
    target = grill.set_temp_celsius if grill.mode.is_on else None
    high_c = low_c = None
    if rules.grill_off_target and grill.mode is GrillMode.COOKING and target is not None:
        high_c = target + rules.grill_off_target_c
        low_c = target - rules.grill_off_target_c

    details = [{"label": "Status", "value": grill.mode.value}]
    if grill.pellet_pct is not None:
        details.append({"label": "Pellets", "value": format_percent(grill.pellet_pct)})
    if grill.ambient_celsius is not None:
        details.append({"label": "Outside", "value": f"{grill.ambient_celsius:.0f}°C"})
    if not grill.connected:
        details.append({"label": "Wi-Fi", "value": "not connected"})
    elif grill.wifi_dbm is not None:
        details.append({"label": "Wi-Fi", "value": format_dbm(grill.wifi_dbm)})
    if grill.keep_warm:
        details.append({"label": "Keep Warm", "value": "on"})
    if grill.super_smoke:
        details.append({"label": "Super Smoke", "value": "on"})

    return {
        "id": grill.id,
        "source": source,
        "kind": "grill",
        "mode": grill.mode.value,  # e.g. "cooking", "offline"
        "is_on": grill.mode.is_on,  # False: a screen says what it's doing, not "no data"
        "name": name,
        "fresh": fresh,
        "last_seen": grill.last_seen.isoformat(),
        "temp_c": grill.celsius(),
        "set_c": target,
        "high_c": high_c,
        "low_c": low_c,
        "high_origin": None,  # these come from the grill alarm rules, not a limit
        "low_origin": None,
        "fan": None,  # a grill's fan isn't reported
        "fan_output_pct": None,
        "pellet_pct": grill.pellet_pct,
        "details": details,
    }


def probe_status(
    probe: Probe,
    name: str,
    source: str,
    now: datetime,
    rules: AlarmRules,
    mine: AlarmSettings = NO_ALARMS,
) -> dict[str, Any]:
    """One food probe. `name` is its display name (see domain/names.py); `source`
    is its source's name; `mine` is the limits set on the Pi."""
    target_c, target_origin = effective_limit(probe.alarms.high, mine.high)
    low_c, low_origin = effective_limit(probe.alarms.low, mine.low)
    fresh = is_fresh(probe.last_seen, now, timedelta(minutes=rules.probe_timeout_minutes))

    return {
        "id": probe.id,
        "source": source,
        "name": name,
        "rings": [ring.value for ring in probe.rings],  # only the app's colours
        "fresh": fresh,
        "last_seen": probe.last_seen.isoformat(),
        "core_c": probe.core_celsius(),
        "sensors_c": [sensor.celsius for sensor in probe.sensors],
        "target_c": target_c,  # the "meat done" alarm in force
        "target_origin": target_origin,
        "low_c": low_c,
        "low_origin": low_origin,
        "battery_pct": probe.battery_pct,
    }


def write_status(path: Path, status: dict[str, Any]) -> None:
    """Write `status` to `path` as JSON, atomically.

    The new content goes to a temporary file next to it, which then replaces
    the old file in one step, so readers see either the old or the new file,
    never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
