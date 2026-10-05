"""ETI Cloud adapter: fetches raw ETI Cloud data and turns it into a Snapshot.

This module has two halves:
- Fetching: `EtiCloudSource` logs in with the `thermoworks-cloud` library,
  keeps the connection open between polls, and downloads every device and its
  channels (`download`). Each fetch has a time limit.
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
- device_name "rfx meat" is a meat probe. Channel 0 is the whole probe: the big
  PROBE number in the app, and where the app saves the probe's alarms.
  Channels 1-4 are its individual sensors (their alarm fields are unused).
  The Gateway has no channel 0.
- Temperatures are in °F. Some values are numbers (96.1), some text ("154").
- A channel with status "NO PROBE" has nothing plugged in; its value is stale.
- fan.set_temp has no units of its own; we assume °F like everything else.
  It read 570 with Billows disconnected, so this is unconfirmed until a live cook.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientSession
from thermoworks_cloud import AuthFactory, ResourceNotFoundError, ThermoworksCloud

from smoker_monitor.config import EtiCloudSettings
from smoker_monitor.domain.models import (
    NO_ALARMS,
    AlarmLimit,
    AlarmSettings,
    Fan,
    Gateway,
    Probe,
    Reading,
    Snapshot,
)
from smoker_monitor.domain.units import f_to_c
from smoker_monitor.sources.base import SourceError

GATEWAY_NAME = "rfx gateway"
PROBE_NAME = "rfx meat"

# Channel statuses that mean "there is no real reading here".
NO_READING_STATUSES = {"NO PROBE"}

# Used when a time is missing. Being decades old, anything stamped with it
# counts as stale, so missing data errs towards raising the alarm.
LONG_AGO = datetime(1970, 1, 1, tzinfo=UTC)

# ETI numbers sensor channels from 1. A probe has 4 sensors and the Gateway has
# 1, so we stop at the first missing channel; this is just a safety limit.
MAX_CHANNELS = 10

# A meat probe's channel 0 is the whole probe (the app's big PROBE number). It is
# where the app saves the probe's alarms.
WHOLE_PROBE_CHANNEL = 0


# ----------------------------------------------------------------- fetching ---

# How long one fetch may take before we give up on it. A normal fetch takes a
# second or two. Without a limit, a request ETI never answers would hang the
# monitor forever: no alarms, no silence button. Kept under the 30 s poll.
FETCH_TIMEOUT_SECONDS = 20.0


class EtiCloudSource:
    """A Source that reads your RFX Gateway, Billows and RFX probes from ETI Cloud.

    It logs in on the first fetch and keeps that connection for the next ones
    (the library refreshes the login token by itself when it expires). After
    any failure the connection is thrown away, so the next fetch starts afresh
    with a new login. Call `close()` when finished with it.
    """

    def __init__(
        self, settings: EtiCloudSettings, timeout_seconds: float = FETCH_TIMEOUT_SECONDS
    ) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._session: ClientSession | None = None
        self._cloud: ThermoworksCloud | None = None

    async def fetch(self) -> Snapshot:
        """Download the latest data and return it as a Snapshot.

        Raises SourceError, with a message safe to show the user, if anything
        goes wrong (no internet, wrong password, ETI Cloud down or too slow, ...).
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                cloud = await self._connect()
                raw = await download(cloud)
        except Exception as e:
            await self.close()
            raise self._as_source_error(e) from e
        return to_snapshot(raw, datetime.now(UTC))

    async def close(self) -> None:
        """Close the connection, if one is open. Safe to call at any time."""
        session = self._session
        self._session = None
        self._cloud = None
        if session is not None:
            await session.close()

    async def _connect(self) -> ThermoworksCloud:
        """The logged-in connection, logging in first if there isn't one."""
        if self._cloud is None:
            self._session = ClientSession()
            self._cloud = await log_in(self._session, self._settings)
        return self._cloud

    def _as_source_error(self, error: Exception) -> SourceError:
        """Turn any failure into a SourceError whose message is safe to show."""
        if isinstance(error, SourceError):
            return error
        if isinstance(error, TimeoutError):
            return SourceError(f"ETI Cloud didn't answer within {self._timeout_seconds:g} s")
        # Belt and braces: make sure the password can never leak into the
        # message, even if a library includes it in an error.
        detail = str(error).replace(self._settings.password, "***")
        return SourceError(f"Couldn't fetch from ETI Cloud ({type(error).__name__}: {detail})")


async def log_in(session: ClientSession, settings: EtiCloudSettings) -> ThermoworksCloud:
    """Log in to ETI Cloud and return a connection ready to download from."""
    # The api_key, app_id and referer point the library at ETI Cloud rather
    # than its default (ThermoWorks Cloud).
    auth = await AuthFactory(
        session,
        api_key=settings.api_key,
        app_id=settings.app_id,
        referer=settings.referer,
    ).build_auth(settings.email, settings.password)
    return ThermoworksCloud(auth)


async def download(cloud: ThermoworksCloud) -> dict[str, Any]:
    """Return every device and its channels as plain data."""
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


async def fetch_raw(settings: EtiCloudSettings) -> dict[str, Any]:
    """Log in, download everything once, and close. Used by the capture script."""
    async with ClientSession() as session:
        return await download(await log_in(session, settings))


async def fetch_channels(cloud: ThermoworksCloud, serial: str) -> list[dict[str, Any]]:
    """Return all channels for one device.

    Meat probes have a channel 0 (the whole probe, holding its alarms) and then
    channels 1-4. The Gateway has no channel 0, only channel 1. So channel 0 is
    fetched on its own, fine if missing, and then 1, 2, 3, ... until the first
    one that doesn't exist.
    """
    found = []
    whole_probe = await fetch_channel(cloud, serial, WHOLE_PROBE_CHANNEL)
    if whole_probe is not None:
        found.append(whole_probe)
    for number in range(1, MAX_CHANNELS + 1):
        channel = await fetch_channel(cloud, serial, number)
        if channel is None:
            break
        found.append(channel)
    return found


async def fetch_channel(cloud: ThermoworksCloud, serial: str, number: int) -> dict[str, Any] | None:
    """Return one channel as plain data, or None if the device doesn't have it."""
    try:
        channel = await cloud.get_device_channel(device_serial=serial, channel=str(number))
    except ResourceNotFoundError:
        return None
    result: dict[str, Any] = asdict(channel)
    return result


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
    # The pit's alarms are set on channel 1 too.
    pit_alarms = to_alarm_settings(sorted_channels[0]) if sorted_channels else NO_ALARMS

    return Gateway(
        serial=str(device.get("serial")),
        label=device.get("label") or None,
        battery_pct=to_int(device.get("battery")),
        wifi_dbm=to_int(device.get("wifi_strength")),
        last_seen=last_seen,
        pit=pit,
        fan=to_fan(device.get("fan")),
        pit_alarms=pit_alarms,
    )


def to_probe(device: dict[str, Any], channels: list[dict[str, Any]]) -> Probe:
    """Map a raw meat-probe device and its channels to a Probe."""
    last_seen = parse_time(device.get("last_seen")) or LONG_AGO
    sorted_channels = sort_channels(channels)
    whole_probe = [c for c in sorted_channels if channel_number(c) == WHOLE_PROBE_CHANNEL]
    sensor_channels = [c for c in sorted_channels if channel_number(c) != WHOLE_PROBE_CHANNEL]

    # Only channels 1-4 are sensors; channel 0 is a summary of them.
    sensors = tuple(to_reading(channel, last_seen) for channel in sensor_channels)

    # The app saves a probe's alarms on channel 0. Data without a channel 0
    # (older captures) falls back to channel 1.
    alarm_channels = whole_probe or sensor_channels
    alarms = to_alarm_settings(alarm_channels[0]) if alarm_channels else NO_ALARMS

    return Probe(
        serial=str(device.get("serial")),
        label=device.get("label") or None,
        battery_pct=to_int(device.get("battery")),
        last_seen=last_seen,
        sensors=sensors,
        alarms=alarms,
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


def to_alarm_settings(channel: dict[str, Any]) -> AlarmSettings:
    """Map the high and low alarms on one raw channel to AlarmSettings."""
    return AlarmSettings(
        high=to_alarm_limit(channel.get("alarm_high")),
        low=to_alarm_limit(channel.get("alarm_low")),
    )


def to_alarm_limit(raw: Any) -> AlarmLimit | None:
    """Map one raw alarm (ETI's alarm_high or alarm_low) to an AlarmLimit.

    Returns None if there's no alarm data at all. An alarm that is switched on
    but has an unreadable value keeps enabled=True with celsius=None, so the
    alarm rules can still warn about it rather than quietly ignoring it.
    """
    if not isinstance(raw, dict):
        return None
    return AlarmLimit(
        enabled=raw.get("enabled") is True,
        celsius=to_celsius(raw.get("value"), raw.get("units")),
        alarming=raw.get("alarming") is True,
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
    """Return channels in number order (0, 1, 2, ...). Unnumbered ones go last."""

    def order(channel: dict[str, Any]) -> int:
        number = channel_number(channel)
        return number if number is not None else 999

    return sorted(channels, key=order)


def channel_number(channel: dict[str, Any]) -> int | None:
    """A channel's number (ETI sends it as text, e.g. "1"), or None if missing."""
    return to_int(channel.get("number"))


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
