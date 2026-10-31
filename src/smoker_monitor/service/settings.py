"""Settings you change from the web page, kept in state/settings.json.

config.toml holds secrets and fixed settings and is never written by the
program. This file holds what you change while cooking: which alarms are on
and their thresholds, probe nicknames, your own target temperatures, and °C or
°F. The web page is the only thing that writes it; the monitor only reads it.

How it combines with config.toml: the [alarms] section there is the starting
point ("base"). The file stores only the settings that differ from the base, so
changing config.toml still flows through for anything you haven't touched.

The file looks like:

    {
      "version": 1,
      "revision": 7,
      "units": "C",
      "alarms": {"probe_timeout_minutes": 10, "probe_low": true},
      "devices": {
        "eti:PROBE-1": {
          "name": "Brisket",
          "high": {"mode": "set", "c": 93.0},
          "low": {"mode": "off"}
        }
      }
    }

For each side (high, low) of a device, "mode" is:
- "eti":  use the limit from the ETI app (the default, so it's left out)
- "set":  use your own number ("c", in °C)
- "off":  no alarm on that side, even if the ETI app has one

`revision` goes up by one on every save. The page sends the revision it was
showing, and a save is refused if someone else has saved since, so two phones
can never silently overwrite each other.

A damaged file never stops the monitor: it keeps its last good settings.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarms import AlarmRules
from smoker_monitor.domain.models import AlarmLimit, AlarmSettings, Snapshot

SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 1

# --------------------------------------------------------------- ranges ---
# What the page allows, so a slip of the finger can't set something silly.
# (The page gets these from the API, so there is one list.)

MINUTES = (1, 60)
PERCENT = (5, 90)  # batteries and pellets; 0 switches them off
OFF_TARGET_C = (3, 50)  # how far a grill may stray from its set temperature, in °C
PROBE_C = (10, 120)  # a food probe's own limits (the page uses this)
PIT_C = (40, 350)  # a pit's own limits (the page uses this)
DEVICE_C = (PROBE_C[0], PIT_C[1])  # what the server accepts for either
NAME_LENGTH = 24

SWITCH_FIELDS = (
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
MINUTE_FIELDS = (
    "gateway_timeout_minutes",
    "probe_timeout_minutes",
    "source_unreachable_minutes",
    "grill_timeout_minutes",
)
PERCENT_FIELDS = ("gateway_battery_pct", "probe_battery_pct", "pellets_low_pct")
DEGREE_FIELDS = ("grill_off_target_c",)

MODES = ("eti", "set", "off")
UNITS = ("C", "F")


class SettingsError(Exception):
    """The settings file can't be used (damaged, or from a newer version)."""


class ValidationError(Exception):
    """A requested change is not allowed. `errors` maps each field to a plain message."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


class ConflictError(Exception):
    """Someone saved since the page loaded its settings."""


@dataclass(frozen=True)
class LimitChoice:
    """One side (high or low) of a device's alarm: ETI's, your own, or off."""

    mode: str = "eti"
    celsius: float | None = None

    def as_limit(self) -> AlarmLimit | None:
        """The limit for evaluate(): None means "use ETI's"."""
        if self.mode == "set":
            return AlarmLimit(enabled=True, celsius=self.celsius)
        if self.mode == "off":
            return AlarmLimit(enabled=False, celsius=None)
        return None

    def to_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "c": self.celsius} if self.mode == "set" else {"mode": self.mode}


NO_CHOICE = LimitChoice()


@dataclass(frozen=True)
class DeviceSettings:
    """Your settings for one probe or pit."""

    name: str | None = None  # your nickname for it
    high: LimitChoice = NO_CHOICE
    low: LimitChoice = NO_CHOICE

    @property
    def is_default(self) -> bool:
        return self.name is None and self.high == NO_CHOICE and self.low == NO_CHOICE

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.name is not None:
            out["name"] = self.name
        if self.high != NO_CHOICE:
            out["high"] = self.high.to_json()
        if self.low != NO_CHOICE:
            out["low"] = self.low.to_json()
        return out


@dataclass(frozen=True)
class Settings:
    """Everything the web page can change.

    `rules` is the effective alarm rules (the config.toml base with your
    changes applied). `changes` is just the part that differs from the base,
    which is what gets written to the file.
    """

    rules: AlarmRules = field(default_factory=AlarmRules)
    changes: Mapping[str, Any] = field(default_factory=dict)
    devices: Mapping[str, DeviceSettings] = field(default_factory=dict)
    units: str = "C"
    revision: int = 0

    def custom_limits(self) -> dict[str, AlarmSettings]:
        """Your own limits by device id, in the form evaluate() takes."""
        return {
            device_id: AlarmSettings(high=device.high.as_limit(), low=device.low.as_limit())
            for device_id, device in self.devices.items()
            if device.high != NO_CHOICE or device.low != NO_CHOICE
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "revision": self.revision,
            "units": self.units,
            "alarms": dict(self.changes),
            "devices": {i: d.to_json() for i, d in sorted(self.devices.items())},
        }


# -------------------------------------------------------------- reading ---


def load_settings(path: Path, base: AlarmRules) -> Settings:
    """Read the settings file. A missing file means "no changes yet".

    Raises SettingsError if the file is there but unusable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Settings(rules=base)
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"Couldn't read {path}: {e}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(f"{path} isn't valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    if data.get("version", SETTINGS_VERSION) != SETTINGS_VERSION:
        raise SettingsError(f"{path} is from a different version of the program")
    try:
        return build_settings(
            base,
            data.get("alarms", {}),
            data.get("devices", {}),
            data.get("units", "C"),
            revision=_whole(data.get("revision", 0)),
        )
    except ValidationError as e:
        raise SettingsError(f"{path} has a bad value: {e}") from None


def build_settings(
    base: AlarmRules,
    alarms: Any,
    devices: Any,
    units: Any,
    revision: int,
) -> Settings:
    """Check the pieces and put them together, or raise ValidationError.

    `alarms` holds alarm rule values by name. Values equal to the base are
    dropped (the file only keeps differences), and only those are range-checked,
    so an untouched setting that config.toml set outside the page's ranges
    doesn't block a save.
    """
    errors: dict[str, str] = {}
    if units not in UNITS:
        errors["units"] = "Choose °C or °F"
    changes = _check_rules(base, alarms, errors)
    checked = _check_devices(devices, errors)
    if errors:
        raise ValidationError(errors)
    return Settings(
        rules=replace(base, **changes),
        changes=changes,
        devices=checked,
        units=units,
        revision=revision,
    )


def _whole(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _check_rules(base: AlarmRules, alarms: Any, errors: dict[str, str]) -> dict[str, Any]:
    if not isinstance(alarms, dict):
        errors["alarms"] = "Alarm settings must be a list of values"
        return {}
    known = {f.name for f in fields(AlarmRules)}
    changes: dict[str, Any] = {}
    for name, value in alarms.items():
        key = f"alarms.{name}"
        if name not in known:
            errors[key] = "Unknown setting"
            continue
        current = getattr(base, name)
        if value == current and type(value) is type(current):
            continue  # same as the base: nothing to keep
        problem = _rule_problem(name, value)
        if problem:
            errors[key] = problem
        else:
            changes[name] = value
    return changes


def _rule_problem(name: str, value: Any) -> str | None:
    """Why `value` isn't allowed for the rule `name`, or None if it is."""
    if name in SWITCH_FIELDS:
        return None if isinstance(value, bool) else "Must be on or off"
    if isinstance(value, bool) or not isinstance(value, int):
        return "Enter a whole number"
    if name in MINUTE_FIELDS:
        return _range_problem(value, MINUTES)
    if name in DEGREE_FIELDS:
        return _range_problem(value, OFF_TARGET_C)
    if name in PERCENT_FIELDS:
        return None if value == 0 else _range_problem(value, PERCENT)
    return "Unknown setting"


def _range_problem(value: int | float, limits: tuple[int, int]) -> str | None:
    low, high = limits
    return None if low <= value <= high else f"Choose between {low} and {high}"


def _check_devices(devices: Any, errors: dict[str, str]) -> dict[str, DeviceSettings]:
    if not isinstance(devices, dict):
        errors["devices"] = "Device settings must be a list"
        return {}
    checked: dict[str, DeviceSettings] = {}
    for device_id, raw in devices.items():
        key = f"devices.{device_id}"
        if not isinstance(raw, dict):
            errors[key] = "Not a valid setting"
            continue
        name = _check_name(raw.get("name"), f"{key}.name", errors)
        high = _check_choice(raw.get("high"), f"{key}.high", errors)
        low = _check_choice(raw.get("low"), f"{key}.low", errors)
        if (
            high.mode == "set"
            and low.mode == "set"
            and high.celsius is not None
            and low.celsius is not None
            and low.celsius >= high.celsius
        ):
            errors[f"{key}.low"] = "The too-cold limit must be below the target"
        device = DeviceSettings(name=name, high=high, low=low)
        if not device.is_default:
            checked[device_id] = device
    return checked


def _check_name(value: Any, key: str, errors: dict[str, str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors[key] = "A name must be text"
        return None
    name = " ".join(value.split())  # tidy spaces
    if not name:
        return None
    if len(name) > NAME_LENGTH:
        errors[key] = f"Use {NAME_LENGTH} characters or fewer"
    elif any(not ch.isprintable() for ch in name):
        errors[key] = "Use letters, numbers and punctuation only"
    return name


def _check_choice(value: Any, key: str, errors: dict[str, str]) -> LimitChoice:
    if value is None:
        return NO_CHOICE
    if not isinstance(value, dict) or value.get("mode") not in MODES:
        errors[key] = "Choose the ETI app's value, your own, or off"
        return NO_CHOICE
    mode = value["mode"]
    if mode != "set":
        return LimitChoice(mode=mode)
    celsius = value.get("c")
    if isinstance(celsius, bool) or not isinstance(celsius, int | float):
        errors[key] = "Enter a temperature"
        return NO_CHOICE
    problem = _range_problem(celsius, DEVICE_C)
    if problem:
        errors[key] = problem
        return NO_CHOICE
    return LimitChoice(mode="set", celsius=round(float(celsius), 1))


# -------------------------------------------------------------- writing ---


def save_settings(path: Path, settings: Settings) -> None:
    """Write the settings atomically (a temporary file, then swapped in)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(settings.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def update_settings(path: Path, base: AlarmRules, request: Mapping[str, Any]) -> Settings:
    """Apply a change from the page and save it. Returns the new settings.

    `request` has the `revision` the page was showing, and any of `alarms`,
    `devices` and `units`. What is left out stays as it was; what is sent
    replaces it whole (send every alarm value, and the full list of devices).

    Raises ConflictError if someone has saved since, ValidationError if a value
    isn't allowed. Nothing is written in either case.
    """
    current = load_settings(path, base)
    if request.get("revision") != current.revision:
        raise ConflictError("Someone else changed the settings. Reloaded the latest.")
    alarms = request.get("alarms", current.changes)
    devices = request.get("devices", {i: d.to_json() for i, d in current.devices.items()})
    updated = build_settings(
        base,
        alarms,
        devices,
        request.get("units", current.units),
        revision=current.revision + 1,
    )
    save_settings(path, updated)
    return updated


def end_cook(path: Path, base: AlarmRules) -> Settings:
    """Clear your own target temperatures. Nicknames and alarm rules stay.

    Returns the new settings (unchanged if there was nothing to clear).
    """
    current = load_settings(path, base)
    devices = {
        device_id: replace(device, high=NO_CHOICE, low=NO_CHOICE)
        for device_id, device in current.devices.items()
    }
    devices = {i: d for i, d in devices.items() if not d.is_default}
    if devices == dict(current.devices):
        return current
    updated = replace(current, devices=devices, revision=current.revision + 1)
    save_settings(path, updated)
    return updated


# --------------------------------------------------------------- naming ---


def apply_nicknames(snapshot: Snapshot, settings: Settings) -> Snapshot:
    """Give each device the nickname you chose, so every screen, log line and
    alarm message uses it. Devices without one keep their own name."""
    names = {i: d.name for i, d in settings.devices.items() if d.name}
    if not names:
        return snapshot
    return replace(
        snapshot,
        gateways=tuple(
            replace(g, name=names[g.id]) if g.id in names else g for g in snapshot.gateways
        ),
        probes=tuple(replace(p, name=names[p.id]) if p.id in names else p for p in snapshot.probes),
        grills=tuple(replace(g, name=names[g.id]) if g.id in names else g for g in snapshot.grills),
    )
