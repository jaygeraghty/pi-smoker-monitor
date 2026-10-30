"""Tests for the ETI capture script's redaction of free text."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = Path(__file__).parent.parent / "scripts" / "capture_eti.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_eti", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load_script()


def test_notes_and_share_links_are_blanked() -> None:
    raw: dict[str, Any] = {
        "devices": [
            {
                "additional_properties": {
                    "notes": "Jamie's garden, 12 Some Road",
                    "publicLink": "https://cloud.example/share/abc",
                    "sessionLabel": "brisket",
                }
            },
            {"additional_properties": {"notes": "", "publicLink": ""}},
            {"additional_properties": None},
        ]
    }
    result = capture.blank_free_text(raw)
    first, second, _ = result["devices"]
    assert first["additional_properties"] == {
        "notes": "REDACTED",
        "publicLink": "REDACTED",
        "sessionLabel": "brisket",  # kept: the app shows it
    }
    assert second["additional_properties"] == {"notes": "", "publicLink": ""}
