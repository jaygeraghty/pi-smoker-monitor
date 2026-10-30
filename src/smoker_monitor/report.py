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
from smoker_monitor.domain.models import (
    AlarmSettings,
    Fan,
    FanActivity,
    Gateway,
    Grill,
    Probe,
    Snapshot,
)
from smoker_monitor.domain.names import (
    device_names,
    gateway_names,
    grill_names,
    probe_base_name,
    probe_names,
)


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
    grill_limit = timedelta(minutes=rules.grill_timeout_minutes)
    probe_limit = timedelta(minutes=rules.probe_timeout_minutes)
    lines: list[str] = []

    if alarms:
        lines.extend(format_active_alarms(alarms, snapshot))
        lines.append("")

    if not snapshot.gateways and not snapshot.grills:
        lines.append("No gateway found.")
    gateway_display = gateway_names(snapshot.gateways)
    for number, gateway in enumerate(snapshot.gateways):
        if number:
            lines.append("")
        fresh = is_fresh(gateway.last_seen, now, gateway_limit)
        lines.extend(format_gateway(gateway, now, fresh, gateway_display[gateway.id]))

    grill_display = grill_names(snapshot.grills)
    for number, grill in enumerate(snapshot.grills):
        if number or snapshot.gateways:
            lines.append("")
        fresh = is_fresh(grill.last_seen, now, grill_limit)
        lines.extend(format_grill(grill, now, fresh, grill_display[grill.id]))

    names = probe_names(snapshot.probes)
    for probe in snapshot.probes:
        lines.append("")
        fresh = is_fresh(probe.last_seen, now, probe_limit)
        lines.extend(format_probe(probe, now, fresh, names[probe.id]))

    return "\n".join(lines)


def format_active_alarms(alarms: Sequence[Alarm], snapshot: Snapshot) -> list[str]:
    """A heading plus one line per alarm, e.g. '  Pit too hot: 182.0°C (limit 170.0°C)'."""
    heading = "*** 1 ALARM ***" if len(alarms) == 1 else f"*** {len(alarms)} ALARMS ***"
    return [heading] + [f"  {describe_alarm(alarm, snapshot)}" for alarm in alarms]


def describe_alarm(alarm: Alarm, snapshot: Snapshot) -> str:
    """One alarm in plain English. The Snapshot supplies the device's name.

    With one Gateway, its alarms just say "Pit" and "Gateway"; with several,
    they name which one, e.g. "RFX GATEWAY 2 pit too hot".
    """
    name = device_names(snapshot).get(alarm.device, "Unknown device")
    several = len(snapshot.gateways) > 1
    pit = f"{name} pit" if several else "Pit"
    hub = name if several else "Gateway"
    value, limit = alarm.value, alarm.limit
    match alarm.kind:
        case AlarmKind.PIT_HIGH:
            return f"{pit} too hot: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.PIT_LOW:
            return f"{pit} too cold: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.PROBE_HIGH:
            return f"{name} reached target: {format_temp(value)} (target {format_temp(limit)})"
        case AlarmKind.PROBE_LOW:
            return f"{name} too cold: {format_temp(value)} (limit {format_temp(limit)})"
        case AlarmKind.GATEWAY_TIMEOUT:
            if value is None:  # device is the source's key
                return f"No gateway found from {snapshot.source_name(alarm.device)}"
            return f"{hub} silent for {format_minutes(value)} (limit {format_minutes(limit)})"
        case AlarmKind.PROBE_TIMEOUT:
            return f"{name} silent for {format_minutes(value)} (limit {format_minutes(limit)})"
        case AlarmKind.GATEWAY_BATTERY:
            return f"{hub} battery low: {format_percent(value)} (below {format_percent(limit)})"
        case AlarmKind.PROBE_BATTERY:
            return f"{name} battery low: {format_percent(value)} (below {format_percent(limit)})"
        case AlarmKind.GRILL_TIMEOUT:
            return f"{name} silent for {format_minutes(value)} (limit {format_minutes(limit)})"
        case AlarmKind.GRILL_OFF_TARGET:
            hot = value is not None and limit is not None and value > limit
            return (
                f"{name} too {'hot' if hot else 'cold'}: {format_temp(value)} "
                f"(set to {format_temp(limit)})"
            )
        case AlarmKind.PELLETS_LOW:
            return f"{name} pellets low: {format_percent(value)} (below {format_percent(limit)})"
        case AlarmKind.SOURCE_UNREACHABLE:  # device is the source's key
            return (
                f"Can't reach {snapshot.source_name(alarm.device)} for {format_minutes(value)} "
                f"(limit {format_minutes(limit)}): readings are out of date"
            )
        case _:
            assert_never(alarm.kind)


def format_fan_activity(fan: Fan) -> str:
    """What the fan is doing, e.g. 'blowing'. An unknown code is shown as-is."""
    if fan.activity is not FanActivity.UNKNOWN:
        return fan.activity.value
    code = "missing" if fan.state is None else str(fan.state)
    return f"state {code} (UNKNOWN)"


def format_gateway(gateway: Gateway, now: datetime, fresh: bool, name: str) -> list[str]:
    """Lines describing the Gateway, its pit probe and the Billows fan.

    `name` is its display name (see domain/names.py)."""
    lines = [
        name,
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
        lines.append(
            f"  Fan:   connected, set to {format_temp(fan.set_temp_celsius)}, "
            f"{format_fan_activity(fan)}"
        )
        deviation = gateway.pit_deviation()
        if deviation is not None:
            lines.append(f"  Pit is {deviation:+.1f}°C from the set temperature")

    return lines


def format_grill(grill: Grill, now: datetime, fresh: bool, name: str) -> list[str]:
    """Lines describing a grill. `name` is its display name (see domain/names.py)."""
    state = grill.mode.value if grill.connected else f"{grill.mode.value}, not connected"
    lines = [
        name,
        f"  {state.capitalize()}, last seen {format_age(now - grill.last_seen)}"
        + ("" if fresh else " (no recent data)"),
        f"  Grill:   {format_temp(grill.celsius())}, set to {format_temp(grill.set_temp_celsius)}",
    ]
    if grill.pellet_pct is not None:
        lines.append(f"  Pellets: {format_percent(grill.pellet_pct)}")
    extras = []
    if grill.ambient_celsius is not None:
        extras.append(f"outside {format_temp(grill.ambient_celsius)}")
    if grill.wifi_dbm is not None:
        extras.append(f"Wi-Fi {format_dbm(grill.wifi_dbm)}")
    extras += [
        name
        for name, on in (("Keep Warm", grill.keep_warm), ("Super Smoke", grill.super_smoke))
        if on
    ]
    if extras:
        lines.append(f"  Also:    {', '.join(extras)}")
    return lines


def format_probe(
    probe: Probe, now: datetime, fresh: bool = True, name: str | None = None
) -> list[str]:
    """Lines describing one meat probe. `name` is its display name (see domain/names.py)."""
    sensors = " / ".join(format_temp(s.celsius) for s in probe.sensors) or "none"
    lines = [
        name or probe_base_name(probe),
        f"  Battery {format_percent(probe.battery_pct)}, "
        f"last seen {format_age(now - probe.last_seen)}",
        f"  Core:    {format_temp(probe.core_celsius())}",
        f"  Sensors: {sensors}",
        f"  Alarms:  {format_alarms(probe.alarms, fresh)}",
    ]
    if probe.rings:
        lines.append(f"  Rings:   {', '.join(ring.value for ring in probe.rings)}")
    return lines


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
