"""The status file: everything the monitor knows, written as JSON every poll.

The monitor service writes it; screens read it (the Pi's display, and later a
page on your phone). Keeping the screens separate means a bug in a screen can
never stop an alarm.

The file describes the cook, not the brand, in three lists:
- pits: every pit (cooking chamber) temperature, whatever reports it. Today
  that's the RFX Gateway's pit probe; later a Traeger grill too. "kind" says
  which ("gateway" or "grill"). Each has its temperature, set temperature,
  alarm limits, what the fan is doing, and "details": brand-specific extras
  as ready-to-print label/value lines (e.g. Battery 75%).
- probes: food temperatures, with each probe's name, ring colours and target.
- sources: whether each data source (e.g. ETI Cloud) is working.

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarm_state import AlarmState, TrackedAlarm
from smoker_monitor.domain.alarms import AlarmRules, is_fresh
from smoker_monitor.domain.models import AlarmLimit, Gateway, Probe, Snapshot
from smoker_monitor.domain.names import gateway_names, probe_names
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
) -> dict[str, Any]:
    """Everything a screen needs, as plain data ready for JSON.

    `rules` supplies the timeouts that decide whether each device is fresh.
    """
    names = probe_names(snapshot.probes)
    hubs = gateway_names(snapshot.gateways)
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": now.isoformat(),
        "sound_on": state.sound_on,
        "sources": [
            {"name": source.name, "ok": source.error is None, "error": source.error}
            for source in snapshot.sources
        ],
        "alarms": [alarm_status(tracked, snapshot) for tracked in state.tracked.values()],
        "pits": [
            pit_status(gateway, hubs[gateway.id], snapshot.source_name(gateway.source), now, rules)
            for gateway in snapshot.gateways
        ],
        "probes": [
            probe_status(probe, names[probe.id], snapshot.source_name(probe.source), now, rules)
            for probe in snapshot.probes
        ],
    }


def pit_status(
    gateway: Gateway, name: str, source: str, now: datetime, rules: AlarmRules
) -> dict[str, Any]:
    """A Gateway (and its fan, if any) as a pit. `name` is its display name (see
    domain/names.py); `source` is its source's name."""
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

    return {
        "id": gateway.id,
        "source": source,
        "kind": "gateway",  # a hub carrying a pit probe; later also "grill" (Traeger)
        "name": name,
        "fresh": fresh,
        "last_seen": gateway.last_seen.isoformat(),
        "temp_c": gateway.get_pit_temp(),
        "set_c": set_c,
        "high_c": limit_c(gateway.pit_alarms.high),
        "low_c": limit_c(gateway.pit_alarms.low),
        "fan": fan,
        "fan_output_pct": fan_output_pct,
        "details": details,
    }


def probe_status(
    probe: Probe, name: str, source: str, now: datetime, rules: AlarmRules
) -> dict[str, Any]:
    """One food probe. `name` is its display name (see domain/names.py); `source`
    is its source's name."""
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
        "target_c": limit_c(probe.alarms.high),  # the app's "meat done" alarm
        "low_c": limit_c(probe.alarms.low),
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
