"""Plain-text report of a Snapshot and its alarms, as printed by `smoker snapshot`.

Pure functions: they take a Snapshot (plus the current time and any active
alarms) and return text, so they are easy to test and could be reused later
(e.g. in a log line). Wording alarms for people happens here, not in the
alarm rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import assert_never

from smoker_monitor.domain.alarms import Alarm, AlarmKind, AlarmRules, is_fresh
from smoker_monitor.domain.models import AlarmSettings, Gateway, Probe, Snapshot


def format_snapshot(
    snapshot: Snapshot,
    now: datetime,
    alarms: Sequence[Alarm] = (),
    rules: AlarmRules | None = None,
) -> str:
    """Return a multi-line, human-readable summary: any alarms first, then each device.

    `rules` supplies the timeouts that decide whether a device's data is recent;
    a device past its timeout has its ETI "ALARMING" flags shown as old news.
    """
    rules = rules or AlarmRules()
    gateway_limit = timedelta(minutes=rules.gateway_timeout_minutes)
    probe_limit = timedelta(minutes=rules.probe_timeout_minutes)
    lines: list[str] = []

    if alarms:
        lines.extend(format_active_alarms(alarms, snapshot))
        lines.append("")

    if snapshot.gateway is None:
        lines.append("No RFX Gateway found on this ETI Cloud account.")
    else:
        fresh = is_fresh(snapshot.gateway.last_seen, now, gateway_limit)
        lines.extend(format_gateway(snapshot.gateway, now, fresh))

    for probe in snapshot.probes:
        lines.append("")
        lines.extend(format_probe(probe, now, is_fresh(probe.last_seen, now, probe_limit)))

    return "\n".join(lines)


def format_active_alarms(alarms: Sequence[Alarm], snapshot: Snapshot) -> list[str]:
    """A heading plus one line per alarm, e.g. '  Pit too hot: 182.0°C (limit 170.0°C)'."""
    heading = "*** 1 ALARM ***" if len(alarms) == 1 else f"*** {len(alarms)} ALARMS ***"
    return [heading] + [f"  {describe_alarm(alarm, snapshot)}" for alarm in alarms]


def describe_alarm(alarm: Alarm, snapshot: Snapshot) -> str:
    """One alarm in plain English. The Snapshot supplies the device's name."""
    name = device_name(alarm.device, snapshot)
    value, limit = alarm.value, alarm.limit
    match alarm.kind:
        case AlarmKind.PIT_HIGH:
            return f"Pit too hot: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.PIT_LOW:
            return f"Pit too cold: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.PROBE_HIGH:
            return f"{name} reached target: {format_temp(value)} (target {format_temp(limit)})"
        case AlarmKind.PROBE_LOW:
            return f"{name} too cold: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.GATEWAY_TIMEOUT:
            if value is None:
                return "No RFX Gateway found"
            return f"Gateway silent for {format_minutes(value)} (limit {format_minutes(limit)})"
        case AlarmKind.PROBE_TIMEOUT:
            return f"{name} silent for {format_minutes(value)} (limit {format_minutes(limit)})"
        case AlarmKind.GATEWAY_BATTERY:
            return f"Gateway battery low: {format_percent(value)} (below {format_percent(limit)})"
        case AlarmKind.PROBE_BATTERY:
            return f"{name} battery low: {format_percent(value)} (below {format_percent(limit)})"
        case _:
            assert_never(alarm.kind)


def device_name(serial: str, snapshot: Snapshot) -> str:
    """A probe's name as shown in the report, e.g. 'RFX MEAT (P1)'; else the serial."""
    for probe in snapshot.probes:
        if probe.serial == serial:
            return f"{probe.label or 'Probe'} ({serial})"
    return serial


def format_gateway(gateway: Gateway, now: datetime, fresh: bool = True) -> list[str]:
    """Lines describing the Gateway, its pit probe and the Billows fan."""
    lines = [
        f"{gateway.label or 'Gateway'} ({gateway.serial})",
        f"  Battery {format_percent(gateway.battery_pct)}, "
        f"Wi-Fi {format_dbm(gateway.wifi_dbm)}, "
        f"last seen {format_age(now - gateway.last_seen)}",
    ]

    if gateway.pit is None or gateway.pit.celsius is None:
        lines.append("  Pit:   no reading (is the air probe plugged in?)")
    else:
        lines.append(f"  Pit:   {format_temp(gateway.pit.celsius)}")
    lines.append(f"  Alarm: {format_alarms(gateway.pit_alarms, fresh)}")

    fan = gateway.fan

    if fan is None:
        lines.append("  Fan:   none")
    elif not fan.connected:
        lines.append("  Fan:   not connected")
    else:
        lines.append(f"  Fan:   connected, set to {format_temp(fan.set_temp_celsius)}")
        deviation = gateway.pit_deviation()
        if deviation is not None:
            lines.append(f"  Pit is {deviation:+.1f}°C from the set temperature")

    return lines


def format_probe(probe: Probe, now: datetime, fresh: bool = True) -> list[str]:
    """Lines describing one meat probe."""
    sensors = " / ".join(format_temp(s.celsius) for s in probe.sensors) or "none"
    return [
        f"{probe.label or 'Probe'} ({probe.serial})",
        f"  Battery {format_percent(probe.battery_pct)}, "
        f"last seen {format_age(now - probe.last_seen)}",
        f"  Core:    {format_temp(probe.core_celsius())}",
        f"  Sensors: {sensors}",
        f"  Alarms:  {format_alarms(probe.alarms, fresh)}",
    ]


# ------------------------------------------------------------------ helpers ---


def format_temp(celsius: float | None) -> str:
    """e.g. 107.2°C, or -- when unknown."""
    return "--" if celsius is None else f"{celsius:.1f}°C"


def format_alarms(alarms: AlarmSettings, fresh: bool = True) -> str:
    """e.g. 'high 170.0°C, low 30.0°C', or 'off' when none are switched on.

    Only alarms switched on in the ETI app are shown. One that is going off
    right now is marked ALARMING. If the device's data isn't recent, ETI's
    flags are frozen at whatever it last heard, so ALARMING is dropped and the
    line says "(no recent data)" instead.
    """
    parts = []
    for name, limit in (("high", alarms.high), ("low", alarms.low)):
        if limit is None or not limit.enabled:
            continue
        text = f"{name} {format_temp(limit.celsius)}"
        if limit.alarming and fresh:
            text += " ALARMING"
        parts.append(text)
    if not parts:
        return "off"
    line = ", ".join(parts)
    return line if fresh else f"{line} (no recent data)"


def format_minutes(value: float | None) -> str:
    """e.g. '12 min', '5 h 33 min' or '2 h' (rounded down), or ? when unknown."""
    if value is None:
        return "?"
    hours, minutes = divmod(int(value), 60)
    if hours == 0:
        return f"{minutes} min"
    return f"{hours} h" if minutes == 0 else f"{hours} h {minutes} min"


def format_percent(value: float | None) -> str:
    """e.g. 70%, or ? when unknown."""
    return "?" if value is None else f"{value:.0f}%"


def format_dbm(value: int | None) -> str:
    """e.g. -52 dBm, or ? when unknown."""
    return "?" if value is None else f"{value} dBm"


def format_age(age: timedelta) -> str:
    """Turn a time difference into words, e.g. '45 s ago', '3 days ago'."""
    seconds = int(age.total_seconds())
    if seconds < 0:
        # The device's clock is slightly ahead of ours; treat it as now.
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return f"{days} day ago" if days == 1 else f"{days} days ago"
