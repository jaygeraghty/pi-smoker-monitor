"""Tests for the settings file the web page writes (service/settings.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smoker_monitor.domain.alarms import AlarmRules
from smoker_monitor.domain.models import AlarmLimit, Gateway, Probe, Reading, Snapshot
from smoker_monitor.service.settings import (
    SETTINGS_FILE,
    ConflictError,
    DeviceSettings,
    LimitChoice,
    Settings,
    SettingsError,
    ValidationError,
    apply_nicknames,
    end_cook,
    load_settings,
    save_settings,
    update_settings,
)

BASE = AlarmRules()
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def path_in(tmp_path: Path) -> Path:
    return tmp_path / SETTINGS_FILE


def request(**parts: object) -> dict[str, object]:
    return {"revision": 0, **parts}


# ------------------------------------------------------------ loading ---


def test_no_file_means_no_changes(tmp_path: Path) -> None:
    settings = load_settings(path_in(tmp_path), BASE)
    assert settings.rules == BASE
    assert settings.revision == 0
    assert settings.custom_limits() == {}


def test_a_damaged_file_is_an_error_not_a_crash(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(path, BASE)


@pytest.mark.parametrize("text", ["[]", '{"version": 99}', '{"alarms": {"pit_high": "yes"}}'])
def test_unusable_files_are_errors(tmp_path: Path, text: str) -> None:
    path = path_in(tmp_path)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(path, BASE)


def test_settings_sit_on_top_of_the_config_file_rules(tmp_path: Path) -> None:
    base = replace(BASE, probe_low=True, pit_low=False)
    path = path_in(tmp_path)
    path.write_text(json.dumps({"alarms": {"pit_low": True}}), encoding="utf-8")
    rules = load_settings(path, base).rules
    assert rules.pit_low is True  # changed on the page
    assert rules.probe_low is True  # still from config.toml


# ----------------------------------------------------------- updating ---


def test_saving_rules_keeps_only_what_differs_from_the_base(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    saved = update_settings(
        path, BASE, request(alarms={"pit_high": True, "probe_timeout_minutes": 10}, units="F")
    )
    assert saved.rules.probe_timeout_minutes == 10
    assert saved.changes == {"probe_timeout_minutes": 10}
    assert saved.units == "F"
    assert saved.revision == 1
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["alarms"] == {"probe_timeout_minutes": 10}
    assert load_settings(path, BASE) == saved


def test_a_stale_page_cannot_overwrite_a_newer_save(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    update_settings(path, BASE, request(units="F"))
    with pytest.raises(ConflictError):
        update_settings(path, BASE, request(units="C"))  # still says revision 0
    assert load_settings(path, BASE).units == "F"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("probe_timeout_minutes", 0),
        ("probe_timeout_minutes", 61),
        ("grill_off_target_c", 2),
        ("pellets_low_pct", 3),
        ("probe_battery_pct", 95),
        ("probe_timeout_minutes", 5.5),
        ("probe_timeout_minutes", True),
        ("pit_high", "off"),
        ("pit_high", 1),
        ("made_up", 1),
    ],
)
def test_silly_rule_values_are_refused(tmp_path: Path, name: str, value: object) -> None:
    path = path_in(tmp_path)
    with pytest.raises(ValidationError) as caught:
        update_settings(path, BASE, request(alarms={name: value}))
    assert f"alarms.{name}" in caught.value.errors
    assert not path.exists()  # nothing was written


@pytest.mark.parametrize("name", ["probe_battery_pct", "gateway_battery_pct", "pellets_low_pct"])
def test_percent_alarms_can_be_switched_off_with_zero(tmp_path: Path, name: str) -> None:
    saved = update_settings(path_in(tmp_path), BASE, request(alarms={name: 0}))
    assert getattr(saved.rules, name) == 0


def test_an_untouched_value_outside_the_page_ranges_does_not_block_a_save(
    tmp_path: Path,
) -> None:
    base = replace(BASE, probe_timeout_minutes=120)  # config.toml allows up to 1440
    saved = update_settings(
        path_in(tmp_path), base, request(alarms={"probe_timeout_minutes": 120, "pit_low": False})
    )
    assert saved.rules.probe_timeout_minutes == 120
    assert saved.rules.pit_low is False


def test_all_the_errors_are_reported_together(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        update_settings(
            path_in(tmp_path),
            BASE,
            request(alarms={"pit_high": "x", "grill_off_target_c": 99}, units="K"),
        )
    assert set(caught.value.errors) == {"alarms.pit_high", "alarms.grill_off_target_c", "units"}


# ------------------------------------------------------------ devices ---


def device(**parts: object) -> dict[str, object]:
    return {"eti:P1": parts}


def test_own_limits_become_custom_limits(tmp_path: Path) -> None:
    saved = update_settings(
        path_in(tmp_path),
        BASE,
        request(
            devices=device(name="  Brisket  ", high={"mode": "set", "c": 93}, low={"mode": "off"})
        ),
    )
    mine = saved.custom_limits()["eti:P1"]
    assert mine.high == AlarmLimit(enabled=True, celsius=93.0)
    assert mine.low == AlarmLimit(enabled=False, celsius=None)
    assert saved.devices["eti:P1"].name == "Brisket"


def test_eti_mode_means_no_custom_limit(tmp_path: Path) -> None:
    saved = update_settings(
        path_in(tmp_path), BASE, request(devices=device(name="Pork", high={"mode": "eti"}))
    )
    assert saved.custom_limits() == {}  # a nickname alone sets no limit
    assert saved.devices["eti:P1"].name == "Pork"


def test_a_device_with_nothing_set_is_not_kept(tmp_path: Path) -> None:
    saved = update_settings(path_in(tmp_path), BASE, request(devices=device(name="  ")))
    assert saved.devices == {}


@pytest.mark.parametrize(
    "parts",
    [
        {"high": {"mode": "set", "c": 5}},  # too low
        {"high": {"mode": "set", "c": 500}},  # too high
        {"high": {"mode": "set"}},  # no number
        {"high": {"mode": "set", "c": "hot"}},
        {"high": {"mode": "maybe"}},
        {"high": "set"},
        {"name": "x" * 25},
        {"name": "bad\x00name"},
        {"name": 7},
        {"high": {"mode": "set", "c": 90}, "low": {"mode": "set", "c": 90}},
    ],
)
def test_bad_device_settings_are_refused(tmp_path: Path, parts: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        update_settings(path_in(tmp_path), BASE, request(devices=device(**parts)))


def test_sending_devices_replaces_the_whole_list(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    update_settings(path, BASE, request(devices=device(name="Brisket")))
    saved = update_settings(path, BASE, {"revision": 1, "devices": {}})
    assert saved.devices == {}


def test_leaving_devices_out_keeps_them(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    update_settings(path, BASE, request(devices=device(name="Brisket")))
    saved = update_settings(path, BASE, {"revision": 1, "units": "F"})
    assert saved.devices["eti:P1"].name == "Brisket"


# ----------------------------------------------------------- end cook ---


def test_end_cook_clears_own_limits_but_keeps_nicknames_and_rules(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    update_settings(
        path,
        BASE,
        request(
            alarms={"pit_low": False},
            devices={
                "eti:P1": {"name": "Brisket", "high": {"mode": "set", "c": 93}},
                "eti:P2": {"low": {"mode": "off"}},
            },
        ),
    )
    ended = end_cook(path, BASE)
    assert ended.custom_limits() == {}
    assert ended.devices["eti:P1"].name == "Brisket"
    assert "eti:P2" not in ended.devices  # nothing left worth keeping
    assert ended.rules.pit_low is False
    assert ended.revision == 2
    assert load_settings(path, BASE) == ended


def test_end_cook_with_nothing_to_clear_changes_nothing(tmp_path: Path) -> None:
    path = path_in(tmp_path)
    assert end_cook(path, BASE).revision == 0
    assert not path.exists()


# ---------------------------------------------------------- nicknames ---


def test_nicknames_rename_devices_in_a_snapshot() -> None:
    probe = Probe(
        serial="P1", label="RFX MEAT", battery_pct=90, last_seen=NOW,
        sensors=(Reading(60.0, NOW),), name="white probe", source="eti",
    )  # fmt: skip
    gateway = Gateway(
        serial="G1", label="RFX GATEWAY", battery_pct=80, wifi_dbm=-50, last_seen=NOW,
        pit=Reading(110.0, NOW), fan=None, source="eti",
    )  # fmt: skip
    snapshot = Snapshot(taken_at=NOW, gateways=(gateway,), probes=(probe,))
    settings = Settings(devices={"eti:P1": DeviceSettings(name="Brisket")})
    renamed = apply_nicknames(snapshot, settings)
    assert renamed.probes[0].name == "Brisket"
    assert renamed.gateways[0].name == gateway.name  # no nickname: untouched
    assert apply_nicknames(snapshot, Settings()) is snapshot


def test_limit_choice_json_round_trip() -> None:
    assert LimitChoice("set", 93.0).to_json() == {"mode": "set", "c": 93.0}
    assert LimitChoice("off").to_json() == {"mode": "off"}


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    save_settings(path_in(tmp_path), Settings())
    assert [p.name for p in tmp_path.iterdir()] == [SETTINGS_FILE]
