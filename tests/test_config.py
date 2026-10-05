"""Tests for loading config.toml."""

from pathlib import Path

import pytest

from smoker_monitor.config import ConfigError, load_config
from smoker_monitor.domain.alarms import AlarmRules

REPO_ROOT = Path(__file__).parent.parent

VALID = """
[eti_cloud]
email = "me@example.com"
password = "s3cret-pw"
api_key = "test-api-key"
app_id = "test-app-id"
referer = "https://cloud.etiltd.com/"

[polling]
interval_seconds = 30
"""

# An optional Traeger section, added onto VALID by the Traeger tests.
TRAEGER = """
[traeger_mqtt]
email = "grill@example.com"
password = "grill-pw"
client_id = "test-client-id"
"""


def write_config(tmp_path: Path, text: str) -> Path:
    """Write `text` to a config.toml inside pytest's temporary folder."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_config_loads(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID))
    assert config.eti_cloud.email == "me@example.com"
    assert config.eti_cloud.password == "s3cret-pw"
    assert config.eti_cloud.referer == "https://cloud.etiltd.com/"
    assert config.polling.interval_seconds == 30


def test_example_config_is_valid() -> None:
    """Keeps config.example.toml in sync with what load_config expects."""
    config = load_config(REPO_ROOT / "config.example.toml")
    assert config.polling.interval_seconds > 0


def test_missing_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.example\.toml"):
        load_config(tmp_path / "config.toml")


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write_config(tmp_path, "this is = = not toml"))


def test_missing_section_is_named(tmp_path: Path) -> None:
    text = VALID.replace("[polling]\ninterval_seconds = 30\n", "")
    with pytest.raises(ConfigError, match=r"\[polling\]"):
        load_config(write_config(tmp_path, text))


def test_missing_setting_is_named(tmp_path: Path) -> None:
    text = VALID.replace('email = "me@example.com"\n', "")
    with pytest.raises(ConfigError, match="email"):
        load_config(write_config(tmp_path, text))


def test_empty_text_setting_rejected(tmp_path: Path) -> None:
    text = VALID.replace('email = "me@example.com"', 'email = ""')
    with pytest.raises(ConfigError, match="email"):
        load_config(write_config(tmp_path, text))


def test_interval_must_be_a_number(tmp_path: Path) -> None:
    text = VALID.replace("interval_seconds = 30", 'interval_seconds = "30"')
    with pytest.raises(ConfigError, match="whole number"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_interval_must_be_positive(tmp_path: Path, bad: str) -> None:
    text = VALID.replace("interval_seconds = 30", f"interval_seconds = {bad}")
    with pytest.raises(ConfigError, match="greater than 0"):
        load_config(write_config(tmp_path, text))


def test_password_never_in_repr(tmp_path: Path) -> None:
    """Printing or logging the config must not reveal the password."""
    config = load_config(write_config(tmp_path, VALID))
    assert "s3cret-pw" not in repr(config)


def test_bad_password_error_does_not_echo_value(tmp_path: Path) -> None:
    """Error messages must never contain the password itself."""
    text = VALID.replace('password = "s3cret-pw"', "password = 12345")
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(tmp_path, text))
    assert "12345" not in str(exc.value)


# ----------------------------------------------------------------- Traeger ---


def test_traeger_section_loads(tmp_path: Path) -> None:
    """A complete [traeger_mqtt] section is read in."""
    config = load_config(write_config(tmp_path, VALID + TRAEGER))
    assert config.traeger_mqtt is not None
    assert config.traeger_mqtt.email == "grill@example.com"
    assert config.traeger_mqtt.password == "grill-pw"
    assert config.traeger_mqtt.client_id == "test-client-id"


def test_traeger_is_optional(tmp_path: Path) -> None:
    """No [traeger_mqtt] section is fine: it just means no Traeger grill."""
    config = load_config(write_config(tmp_path, VALID))
    assert config.traeger_mqtt is None


def test_half_filled_traeger_section_fails(tmp_path: Path) -> None:
    """A half-filled section is a mistake, so fail loudly rather than ignore it."""
    text = VALID + TRAEGER.replace('password = "grill-pw"\n', "")
    with pytest.raises(ConfigError, match=r"\[traeger_mqtt\] password"):
        load_config(write_config(tmp_path, text))


def test_traeger_password_never_in_repr(tmp_path: Path) -> None:
    """Printing or logging the config must not reveal the Traeger password either."""
    config = load_config(write_config(tmp_path, VALID + TRAEGER))
    assert "grill-pw" not in repr(config)


def test_example_config_includes_traeger() -> None:
    """Keeps the Traeger section in config.example.toml in sync with load_config."""
    config = load_config(REPO_ROOT / "config.example.toml")
    assert config.traeger_mqtt is not None


# ------------------------------------------------------------------ alarms ---


def test_no_alarms_section_uses_safe_defaults(tmp_path: Path) -> None:
    """Fail-safe: a config without [alarms] still has every important alarm on."""
    rules = load_config(write_config(tmp_path, VALID)).alarms
    assert rules == AlarmRules()
    assert rules.pit_high and rules.pit_low and rules.probe_high
    assert rules.gateway_timeout and rules.probe_timeout
    assert rules.probe_low is False


def test_alarms_section_is_read(tmp_path: Path) -> None:
    text = (
        VALID
        + """
[alarms]
pit_low = false
probe_low = true
gateway_timeout_minutes = 10
probe_battery_pct = 0
"""
    )
    rules = load_config(write_config(tmp_path, text)).alarms
    assert rules.pit_low is False
    assert rules.probe_low is True
    assert rules.gateway_timeout_minutes == 10
    assert rules.probe_battery_pct == 0  # 0 switches the battery alarm off


def test_settings_left_out_keep_their_defaults(tmp_path: Path) -> None:
    text = VALID + "\n[alarms]\npit_low = false\n"
    rules = load_config(write_config(tmp_path, text)).alarms
    assert rules.pit_low is False
    assert rules.pit_high is True
    assert rules.probe_timeout_minutes == AlarmRules().probe_timeout_minutes


def test_unknown_alarm_setting_is_an_error(tmp_path: Path) -> None:
    """A typo must not silently leave an alarm in the wrong state."""
    text = VALID + "\n[alarms]\npit_hihg = false\n"
    with pytest.raises(ConfigError, match="pit_hihg"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize("bad", ['"yes"', "1", '"true"'])
def test_alarm_switch_must_be_true_or_false(tmp_path: Path, bad: str) -> None:
    text = VALID + f"\n[alarms]\npit_high = {bad}\n"
    with pytest.raises(ConfigError, match="true or false"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("gateway_timeout_minutes", "0"),
        ("probe_timeout_minutes", "-1"),
        ("gateway_battery_pct", "101"),
        ("probe_battery_pct", "-5"),
    ],
)
def test_alarm_numbers_must_be_in_range(tmp_path: Path, key: str, bad: str) -> None:
    text = VALID + f"\n[alarms]\n{key} = {bad}\n"
    with pytest.raises(ConfigError, match="between"):
        load_config(write_config(tmp_path, text))


def test_alarm_numbers_must_be_whole(tmp_path: Path) -> None:
    text = VALID + "\n[alarms]\nprobe_timeout_minutes = 2.5\n"
    with pytest.raises(ConfigError, match="whole number"):
        load_config(write_config(tmp_path, text))


def test_example_config_alarms_are_valid() -> None:
    """Keeps the [alarms] section in config.example.toml in sync with load_config."""
    rules = load_config(REPO_ROOT / "config.example.toml").alarms
    assert rules == AlarmRules()
