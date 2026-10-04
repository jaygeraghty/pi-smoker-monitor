"""ETI Cloud adapter: fetches raw ETI Cloud data and turns it into a Snapshot.

This module has two halves:
- Fetching: `EtiCloudSource` logs in with the `thermoworks-cloud` library and
  downloads every device and its channels (`fetch_raw`).
- Mapping: pure functions (`to_snapshot` and friends) that convert that raw data
  into our domain models. No network, so they are fully testable against
  tests/fixtures/eti_idle.json.

The raw data has this shape:
    {
        "devices": [ {...device details...}, ... ],
        "channels": { "<device serial>": [ {...channel 1...}, ... ] },
    }

What the raw data looks like in practice (from a real capture):
- device_name "rfx gateway" is the Gateway. Its channel 1 is the pit (air) probe,
  and its "fan" block is the Billows fan.
- device_name "rfx meat" is a meat probe. Channels 1-4 are its sensors.
- Temperatures are in °F. Some values are numbers (96.1), some text ("154").
- A channel with status "NO PROBE" has nothing plugged in; its value is stale.
- fan.set_temp has no units of its own; we assume °F like everything else.
  It read 570 with Billows disconnected, so this is unconfirmed until a live cook.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientSession
from thermoworks_cloud import AuthFactory, ResourceNotFoundError, ThermoworksCloud

from smoker_monitor.config import EtiCloudSettings
from smoker_monitor.domain.models import Fan, Gateway, Probe, Reading, Snapshot
from smoker_monitor.domain.units import f_to_c
from smoker_monitor.sources.base import SourceError

GATEWAY_NAME = "rfx gateway"
PROBE_NAME = "rfx meat"

# Channel statuses that mean "there is no real reading here".
NO_READING_STATUSES = {"NO PROBE"}

# Used when a time is missing. Being decades old, anything stamped with it
# counts as stale, so missing data errs towards raising the alarm.
LONG_AGO = datetime(1970, 1, 1, tzinfo=UTC)

# ETI numbers channels from 1. A probe has 4 sensors and the Gateway has 1,
# so we stop at the first missing channel; this is just a safety limit.
MAX_CHANNELS = 10


# ----------------------------------------------------------------- fetching ---


class EtiCloudSource:
    """A Source that reads your RFX Gateway, Billows and RFX probes from ETI Cloud."""

    def __init__(self, settings: EtiCloudSettings) -> None:
        self._settings = settings

    async def fetch(self) -> Snapshot:
        """Download the latest data and return it as a Snapshot.

        Raises SourceError, with a message safe to show the user, if anything
        goes wrong (no internet, wrong password, ETI Cloud down, ...).
        """
        try:
            raw = await fetch_raw(self._settings)
        except SourceError:
            raise
        except Exception as e:
            # Belt and braces: make sure the password can never leak into
            # the message, even if a library includes it in an error.
            detail = str(e).replace(self._settings.password, "***")
            raise SourceError(
                f"Couldn't fetch from ETI Cloud ({type(e).__name__}: {detail})"
            ) from e
        return to_snapshot(raw, datetime.now(UTC))


async def fetch_raw(settings: EtiCloudSettings) -> dict[str, Any]:
    """Log in to ETI Cloud and return every device and its channels as plain data.

    Each call opens a fresh connection and logs in again. That's fine for a
    one-off snapshot; the long-running service will keep a session open instead.
    """
    async with ClientSession() as session:
        # The api_key, app_id and referer point the library at ETI Cloud
        # rather than its default (ThermoWorks Cloud).
        auth = await AuthFactory(
            session,
            api_key=settings.api_key,
            app_id=settings.app_id,
            referer=settings.referer,
        ).build_auth(settings.email, settings.password)
        cloud = ThermoworksCloud(auth)

        user = await cloud.get_user()
        if not user.account_id:
            raise SourceError("ETI Cloud returned no account for this login")
        devices = await cloud.get_devices(user.account_id)

        channels: dict[str, list[dict[str, Any]]] = {}
        for device in devices:
            if device.serial:
                channels[device.serial] = await fetch_channels(cloud, device.serial)

    raw = {"devices": [asdict(device) for device in devices], "channels": channels}
    # Round-trip through JSON so datetimes become plain text, exactly like the
    # saved fixtures. The mapping functions below then only deal with one format.
    result: dict[str, Any] = json.loads(json.dumps(raw, default=str))
    return result


async def fetch_channels(cloud: ThermoworksCloud, serial: str) -> list[dict[str, Any]]:
    """Return all channels for one device, stopping at the first one that doesn't exist."""
    found = []
    for number in range(1, MAX_CHANNELS + 1):
        try:
            channel = await cloud.get_device_channel(device_serial=serial, channel=str(number))
        except ResourceNotFoundError:
            break
        found.append(asdict(channel))
    return found


# ------------------------------------------------------------------ mapping ---


def to_snapshot(raw: dict[str, Any], now: datetime) -> Snapshot:
    """Build a Snapshot from raw ETI Cloud data, as seen at time `now`."""
    gateway: Gateway | None = None
    probes: list[Probe] = []

    for device in raw.get("devices", []):
        name = (device.get("device_name") or "").lower()
        channels = raw.get("channels", {}).get(device.get("serial"), [])

        # Only one Gateway is expected; if there are more, the first one wins.
        if name == GATEWAY_NAME and gateway is None:
            gateway = to_gateway(device, channels)
        elif name == PROBE_NAME:
            probes.append(to_probe(device, channels))
        # Any other device type is ignored.

    return Snapshot(taken_at=now, gateway=gateway, probes=tuple(probes))


def to_gateway(device: dict[str, Any], channels: list[dict[str, Any]]) -> Gateway:
    """Map a raw Gateway device and its channels to a Gateway."""
    last_seen = parse_time(device.get("last_seen")) or LONG_AGO
    sorted_channels = sort_channels(channels)
    # Channel 1 is the pit probe. No channels at all means no pit probe.
    pit = to_reading(sorted_channels[0], last_seen) if sorted_channels else None

    return Gateway(
        serial=str(device.get("serial")),
        label=device.get("label") or None,
        battery_pct=to_int(device.get("battery")),
        wifi_dbm=to_int(device.get("wifi_strength")),
        last_seen=last_seen,
        pit=pit,
        fan=to_fan(device.get("fan")),
    )


def to_probe(device: dict[str, Any], channels: list[dict[str, Any]]) -> Probe:
    """Map a raw meat-probe device and its channels to a Probe."""
    last_seen = parse_time(device.get("last_seen")) or LONG_AGO
    sensors = tuple(to_reading(channel, last_seen) for channel in sort_channels(channels))

    return Probe(
        serial=str(device.get("serial")),
        label=device.get("label") or None,
        battery_pct=to_int(device.get("battery")),
        last_seen=last_seen,
        sensors=sensors,
    )


def to_fan(fan: dict[str, Any] | None) -> Fan | None:
    """Map a raw fan block to a Fan, or None if the Gateway reports no fan."""
    if not fan:
        return None
    state = to_int(fan.get("state"))
    return Fan(
        # The fan block has no units of its own; assume °F like the rest.
        set_temp_celsius=to_celsius(fan.get("set_temp"), "F"),
        # An unknown state is treated as 0 (off).
        state=state if state is not None else 0,
        connected=fan.get("connected") is True,
    )


def to_reading(channel: dict[str, Any], fallback_time: datetime) -> Reading:
    """Map one raw channel to a Reading.

    The temperature is None if nothing is plugged in or the value is unreadable.
    The time is when the value was saved, falling back to when the device was
    last seen.
    """
    if channel.get("status") in NO_READING_STATUSES:
        celsius = None
    else:
        celsius = to_celsius(channel.get("value"), channel.get("units"))

    taken_at = (
        parse_time(channel.get("last_telemetry_saved"))
        or parse_time(channel.get("last_seen"))
        or fallback_time
    )
    return Reading(celsius=celsius, taken_at=taken_at)


# ------------------------------------------------------------------ helpers ---


def sort_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return channels in number order (1, 2, 3, ...). Unnumbered ones go last."""

    def number(channel: dict[str, Any]) -> int:
        value = to_int(channel.get("number"))
        return value if value is not None else 999

    return sorted(channels, key=number)


def to_celsius(value: Any, units: Any) -> float | None:
    """Convert a raw temperature (number or text, in °F or °C) to °C.

    Returns None if the value is missing or isn't a number.
    """
    number = to_float(value)
    if number is None:
        return None
    if str(units).upper() == "C":
        return number
    return f_to_c(number)


def to_float(value: Any) -> float | None:
    """Turn a number or numeric text into a float; anything else gives None."""
    # True/False count as numbers in Python, but they are never a temperature.
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def to_int(value: Any) -> int | None:
    """Turn a whole number or numeric text into an int; anything else gives None."""
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO time string (e.g. "2026-09-21 11:23:07.637000+00:00").

    Returns None if it's missing or unreadable. Times without a timezone are
    assumed to be UTC.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
