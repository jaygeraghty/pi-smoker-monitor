"""Tests for loading config.toml."""

from pathlib import Path

import pytest

from smoker_monitor.config import ConfigError, load_config

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
