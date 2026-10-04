"""Plain-text report of a Snapshot, as printed by `smoker snapshot`.

Pure functions: they take a Snapshot and the current time and return text, so
they are easy to test and could be reused later (e.g. in a log line).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from smoker_monitor.domain.models import AlarmSettings, Gateway, Probe, Snapshot

# How old the Gateway's last report can be before we warn that data is stale.
STALE_AFTER = timedelta(minutes=5)


def format_snapshot(snapshot: Snapshot, now: datetime) -> str:
    """Return a multi-line, human-readable summary of the snapshot."""
    lines: list[str] = []

    if snapshot.gateway is None:
        lines.append("No RFX Gateway found on this ETI Cloud account.")
    else:
        lines.extend(format_gateway(snapshot.gateway, now))

    for probe in snapshot.probes:
        lines.append("")
        lines.extend(format_probe(probe, now))

    if snapshot.is_stale(now, STALE_AFTER):
        lines.append("")
        lines.append("WARNING: no recent data from the Gateway; readings may be out of date.")

    return "\n".join(lines)


def format_gateway(gateway: Gateway, now: datetime) -> list[str]:
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
    lines.append(f"  Alarm: {format_alarms(gateway.pit_alarms)}")

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


def format_probe(probe: Probe, now: datetime) -> list[str]:
    """Lines describing one meat probe."""
    sensors = " / ".join(format_temp(s.celsius) for s in probe.sensors) or "none"
    return [
        f"{probe.label or 'Probe'} ({probe.serial})",
        f"  Battery {format_percent(probe.battery_pct)}, "
        f"last seen {format_age(now - probe.last_seen)}",
        f"  Core:    {format_temp(probe.core_celsius())}",
        f"  Sensors: {sensors}",
        f"  Alarms:  {format_alarms(probe.alarms)}",
    ]


# ------------------------------------------------------------------ helpers ---


def format_temp(celsius: float | None) -> str:
    """e.g. 107.2°C, or -- when unknown."""
    return "--" if celsius is None else f"{celsius:.1f}°C"


def format_alarms(alarms: AlarmSettings) -> str:
    """e.g. 'high 170.0°C, low 30.0°C', or 'off' when none are switched on.

    Only alarms switched on in the ETI app are shown. One that is going off
    right now is marked ALARMING.
    """
    parts = []
    for name, limit in (("high", alarms.high), ("low", alarms.low)):
        if limit is None or not limit.enabled:
            continue
        text = f"{name} {format_temp(limit.celsius)}"
        if limit.alarming:
            text += " ALARMING"
        parts.append(text)
    return ", ".join(parts) or "off"


def format_percent(value: int | None) -> str:
    """e.g. 70%, or ? when unknown."""
    return "?" if value is None else f"{value}%"


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
