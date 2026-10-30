"""Tests for the Traeger capture script's redaction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = Path(__file__).parent.parent / "scripts" / "capture_traeger.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_traeger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load_script()

RAW: dict[str, Any] = {
    "grills": [{"thingName": "801F12ABCDEF", "friendlyName": "Jamie's Pro", "userId": "abc-123"}],
    "messages": [
        {
            "topic_thing": "801F12ABCDEF",
            "seconds_after_start": 1.2,
            "payload": {
                "thingName": "801F12ABCDEF",
                "status": {"grill": 225, "set": 225, "units": 1, "pellet_level": 80},
                "details": {
                    "friendlyName": "Jamie's Pro",
                    "macAddress": "80:1f:12:ab:cd:ef",
                    "ssid": "HomeWiFi-5G",
                    "ip": "192.168.1.40",
                    "owner": "me@example.org",
                },
                "acc": [{"uuid": "p-1", "type": "probe", "name": "a"}],
            },
        }
    ],
}


def redacted() -> dict[str, Any]:
    fakes: dict[str, str] = {}
    capture.find_sensitive_values(RAW, fakes)
    result: dict[str, Any] = capture.redact(RAW, fakes)
    return result


def test_nothing_identifying_survives() -> None:
    text = str(redacted())
    for real in ("801F12ABCDEF", "Jamie", "abc-123", "80:1f:12", "HomeWiFi", "192.168", "me@ex"):
        assert real not in text, real


def test_links_between_grill_and_messages_survive() -> None:
    result = redacted()
    thing = result["grills"][0]["thingName"]
    assert thing == "GRILL-1"
    assert result["messages"][0]["topic_thing"] == thing
    assert result["messages"][0]["payload"]["thingName"] == thing


def test_readings_and_kit_types_are_kept() -> None:
    message = redacted()["messages"][0]["payload"]
    assert message["status"] == {"grill": 225, "set": 225, "units": 1, "pellet_level": 80}
    assert message["acc"][0]["type"] == "probe"


def test_short_values_dont_mangle_other_text() -> None:
    """The probe called "a" is replaced, but every other "a" is left alone."""
    message = redacted()["messages"][0]["payload"]
    assert message["acc"][0]["name"] != "a"
    assert message["acc"][0]["type"] == "probe"
