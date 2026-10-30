"""Load and validate settings from config.toml.

Responsibilities:
- Read the TOML file (stdlib `tomllib`) and turn it into typed, frozen dataclasses.
- Fail fast with a clear message for missing or invalid values.
- Never log the password or tokens.

See config.example.toml for the expected shape.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarms import AlarmRules


class ConfigError(Exception):
    """Raised when config.toml is missing or invalid."""


# [alarms] settings that have been renamed: old name -> new name.
RENAMED_ALARMS = {
    "eti_unreachable": "source_unreachable",
    "eti_unreachable_minutes": "source_unreachable_minutes",
}


@dataclass(frozen=True)
class EtiCloudSettings:
    """Login and client settings for ETI Cloud."""

    email: str
    password: str = field(repr=False)  # never shown when printed or logged
    api_key: str
    app_id: str
    referer: str
    poll_seconds: int = 30  # [eti_cloud] poll_seconds, else [polling] interval_seconds


@dataclass(frozen=True)
class TraegerSettings:
    """Login and client settings for Traeger grills."""

    email: str
    password: str = field(repr=False)
    client_id: str


@dataclass(frozen=True)
class PollingSettings:
    """How often to fetch new readings."""

    interval_seconds: int


@dataclass(frozen=True)
class Config:
    """All settings the app needs. Each brand's section is optional, but at
    least one brand must be set up."""

    eti_cloud: EtiCloudSettings | None
    polling: PollingSettings
    traeger_mqtt: TraegerSettings | None = None
    alarms: AlarmRules = field(default_factory=AlarmRules)


def load_config(path: Path) -> Config:
    """Read the TOML file at `path` and return validated settings.

    Raises ConfigError with a clear message if the file is missing, isn't valid
    TOML, or a setting is missing or invalid.
    """
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"{path} not found. Copy config.example.toml to config.toml and fill it in."
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None

    interval = _get_int(data, "polling", "interval_seconds")
    if interval <= 0:
        raise ConfigError("[polling] interval_seconds must be greater than 0")

    eti_cloud = None
    if "eti_cloud" in data:
        eti_cloud = EtiCloudSettings(
            email=_get_str(data, "eti_cloud", "email"),
            password=_get_str(data, "eti_cloud", "password"),
            api_key=_get_str(data, "eti_cloud", "api_key"),
            app_id=_get_str(data, "eti_cloud", "app_id"),
            referer=_get_str(data, "eti_cloud", "referer"),
            poll_seconds=_poll_seconds(data, "eti_cloud", interval),
        )
    traeger_mqtt = None
    if "traeger_mqtt" in data:
        traeger_mqtt = TraegerSettings(
            email=_get_str(data, "traeger_mqtt", "email"),
            password=_get_str(data, "traeger_mqtt", "password"),
            client_id=_get_str(data, "traeger_mqtt", "client_id"),
        )

    # Traeger settings are read, but it can't be monitored yet, so it doesn't count.
    if eti_cloud is None:
        raise ConfigError("Nothing to monitor: add an [eti_cloud] section to the config")

    return Config(
        eti_cloud=eti_cloud,
        traeger_mqtt=traeger_mqtt,
        polling=PollingSettings(interval_seconds=interval),
        alarms=load_alarm_rules(data),
    )


def load_alarm_rules(data: dict[str, Any]) -> AlarmRules:
    """Read the optional [alarms] section. Any setting left out keeps its default.

    Unknown settings are an error rather than ignored: a typo like "pit_hihg"
    would otherwise silently leave an alarm in a state you didn't intend.
    """
    table = data.get("alarms", {})
    if not isinstance(table, dict):
        raise ConfigError("[alarms] must be a section")

    known = {f.name for f in fields(AlarmRules)}
    unknown = sorted(set(table) - known)
    if unknown:
        described = [
            f"{key} (now called {RENAMED_ALARMS[key]})" if key in RENAMED_ALARMS else key
            for key in unknown
        ]
        raise ConfigError(f"Unknown setting in [alarms]: {', '.join(described)}")

    defaults = AlarmRules()
    return AlarmRules(
        pit_high=_alarm_bool(table, "pit_high", defaults.pit_high),
        pit_low=_alarm_bool(table, "pit_low", defaults.pit_low),
        probe_high=_alarm_bool(table, "probe_high", defaults.probe_high),
        probe_low=_alarm_bool(table, "probe_low", defaults.probe_low),
        gateway_timeout=_alarm_bool(table, "gateway_timeout", defaults.gateway_timeout),
        gateway_timeout_minutes=_alarm_int(
            table, "gateway_timeout_minutes", defaults.gateway_timeout_minutes, 1, 1440
        ),
        probe_timeout=_alarm_bool(table, "probe_timeout", defaults.probe_timeout),
        probe_timeout_minutes=_alarm_int(
            table, "probe_timeout_minutes", defaults.probe_timeout_minutes, 1, 1440
        ),
        gateway_battery_pct=_alarm_int(
            table, "gateway_battery_pct", defaults.gateway_battery_pct, 0, 100
        ),
        probe_battery_pct=_alarm_int(
            table, "probe_battery_pct", defaults.probe_battery_pct, 0, 100
        ),
        source_unreachable=_alarm_bool(table, "source_unreachable", defaults.source_unreachable),
        source_unreachable_minutes=_alarm_int(
            table, "source_unreachable_minutes", defaults.source_unreachable_minutes, 1, 1440
        ),
        grill_timeout=_alarm_bool(table, "grill_timeout", defaults.grill_timeout),
        grill_timeout_minutes=_alarm_int(
            table, "grill_timeout_minutes", defaults.grill_timeout_minutes, 1, 1440
        ),
        grill_off_target=_alarm_bool(table, "grill_off_target", defaults.grill_off_target),
        grill_off_target_c=_alarm_int(
            table, "grill_off_target_c", defaults.grill_off_target_c, 1, 100
        ),
        pellets_low_pct=_alarm_int(table, "pellets_low_pct", defaults.pellets_low_pct, 0, 100),
    )


def _poll_seconds(data: dict[str, Any], section: str, default: int) -> int:
    """A brand's optional poll_seconds, e.g. [eti_cloud] poll_seconds = 60.

    Left out, the brand is polled every [polling] interval_seconds.
    """
    if "poll_seconds" not in data[section]:
        return default
    value = _get_int(data, section, "poll_seconds")
    if value <= 0:
        raise ConfigError(f"[{section}] poll_seconds must be greater than 0")
    return value


def _get(data: dict[str, Any], section: str, key: str) -> Any:
    """Return data[section][key], or raise ConfigError naming what's missing."""
    table = data.get(section)
    if not isinstance(table, dict):
        raise ConfigError(f"Missing [{section}] section in config")
    if key not in table:
        raise ConfigError(f"Missing setting [{section}] {key} in config")
    return table[key]


def _get_str(data: dict[str, Any], section: str, key: str) -> str:
    """Return a required, non-empty text setting."""
    value = _get(data, section, key)
    if not isinstance(value, str) or not value.strip():
        # Deliberately don't include the value: it could be the password.
        raise ConfigError(f"[{section}] {key} must be non-empty text")
    return value


def _get_int(data: dict[str, Any], section: str, key: str) -> int:
    """Return a required whole-number setting."""
    value = _get(data, section, key)
    # bool is a subclass of int in Python, so rule it out explicitly.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"[{section}] {key} must be a whole number")
    return value


def _alarm_bool(table: dict[str, Any], key: str, default: bool) -> bool:
    """Return an [alarms] on/off setting, or the default if it's left out."""
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"[alarms] {key} must be true or false")
    return value


def _alarm_int(table: dict[str, Any], key: str, default: int, lowest: int, highest: int) -> int:
    """Return an [alarms] whole-number setting within lowest..highest, or the default."""
    value = table.get(key, default)
    # bool is a subclass of int in Python, so rule it out explicitly.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"[alarms] {key} must be a whole number")
    if not lowest <= value <= highest:
        raise ConfigError(f"[alarms] {key} must be between {lowest} and {highest}")
    return value
