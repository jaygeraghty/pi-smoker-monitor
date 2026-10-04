"""Load and validate settings from config.toml.

Responsibilities:
- Read the TOML file (stdlib `tomllib`) and turn it into typed, frozen dataclasses.
- Fail fast with a clear message for missing or invalid values.
- Never log the password or tokens.

See config.example.toml for the expected shape.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when config.toml is missing or invalid."""


@dataclass(frozen=True)
class EtiCloudSettings:
    """Login and client settings for ETI Cloud."""

    email: str
    password: str = field(repr=False)  # never shown when printed or logged
    api_key: str
    app_id: str
    referer: str


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
    """All settings the app needs."""

    eti_cloud: EtiCloudSettings
    polling: PollingSettings
    traeger_mqtt: TraegerSettings | None = None


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

    eti_cloud = EtiCloudSettings(
        email=_get_str(data, "eti_cloud", "email"),
        password=_get_str(data, "eti_cloud", "password"),
        api_key=_get_str(data, "eti_cloud", "api_key"),
        app_id=_get_str(data, "eti_cloud", "app_id"),
        referer=_get_str(data, "eti_cloud", "referer"),
    )
    traeger_mqtt = None
    if "traeger_mqtt" in data:
        traeger_mqtt = TraegerSettings(
            email=_get_str(data, "traeger_mqtt", "email"),
            password=_get_str(data, "traeger_mqtt", "password"),
            client_id=_get_str(data, "traeger_mqtt", "client_id"),
        )

    interval = _get_int(data, "polling", "interval_seconds")
    if interval <= 0:
        raise ConfigError("[polling] interval_seconds must be greater than 0")

    return Config(
        eti_cloud=eti_cloud,
        traeger_mqtt=traeger_mqtt,
        polling=PollingSettings(interval_seconds=interval),
    )


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
