"""The status file: everything the monitor knows, written as JSON every poll.

The service writes it; screens read it (the Pi's display, and later a page on
your phone). Keeping the screens separate means a bug in a screen can never
stop an alarm. Each alarm carries its plain-English message, so a screen
doesn't need to know any alarm rules or wording.

Written atomically (to a temporary file, then swapped in), so a screen never
reads a half-written file. A screen can also check `updated_at`: if it stops
changing, the monitor itself has stopped.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarm_state import AlarmState
from smoker_monitor.domain.models import Gateway, Probe, Snapshot
from smoker_monitor.report import describe_alarm

STATUS_FILE = "status.json"


def build_status(
    snapshot: Snapshot, state: AlarmState, now: datetime, fetch_error: str | None
) -> dict[str, Any]:
    """Everything a screen needs, as plain data ready for JSON."""
    return {
        "updated_at": now.isoformat(),
        "sound_on": state.sound_on,
        "fetch_error": fetch_error,  # None when the last fetch worked
        "alarms": [
            {
                "kind": tracked.alarm.kind.value,
                "device": tracked.alarm.device,
                "status": tracked.status.value,  # pending, ringing or silenced
                "value": tracked.alarm.value,
                "limit": tracked.alarm.limit,
                "since": tracked.first_seen.isoformat(),
                "message": describe_alarm(tracked.alarm, snapshot),
            }
            for tracked in state.tracked.values()
        ],
        "gateway": gateway_status(snapshot.gateway),
        "probes": [probe_status(probe) for probe in snapshot.probes],
    }


def gateway_status(gateway: Gateway | None) -> dict[str, Any] | None:
    if gateway is None:
        return None
    fan = gateway.fan
    return {
        "serial": gateway.serial,
        "label": gateway.label,
        "battery_pct": gateway.battery_pct,
        "wifi_dbm": gateway.wifi_dbm,
        "last_seen": gateway.last_seen.isoformat(),
        "pit_c": gateway.get_pit_temp(),
        "fan": None
        if fan is None
        else {"connected": fan.connected, "set_c": fan.set_temp_celsius, "state": fan.state},
    }


def probe_status(probe: Probe) -> dict[str, Any]:
    return {
        "serial": probe.serial,
        "label": probe.label,
        "battery_pct": probe.battery_pct,
        "last_seen": probe.last_seen.isoformat(),
        "core_c": probe.core_celsius(),
        "sensors_c": [sensor.celsius for sensor in probe.sensors],
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
