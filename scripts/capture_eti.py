"""Save a copy of your ETI Cloud data, plus a redacted copy for tests.

This is a developer tool, not part of the app. It logs in to ETI Cloud using
the details in config.toml and the app's own download code (fetch_raw),
fetches every device on your account (the RFX Gateway and each RFX MEAT probe)
along with their channels, and writes:

    captures/eti_raw.json        your real data (git-ignored, never committed)
    tests/fixtures/eti_idle.json the same data with anything that identifies
                                 your account swapped for obvious fakes

The redacted copy is what the tests use. Always read it before committing.

Run it from the repo root:
    uv run python scripts/capture_eti.py
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from smoker_monitor.config import load_config
from smoker_monitor.sources.eti_cloud import fetch_raw

CONFIG_FILE = Path("config.toml")
RAW_FILE = Path("captures/eti_raw.json")
FIXTURE_FILE = Path("tests/fixtures/eti_idle.json")

# Matches anything that looks like an email address.
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# -------------------------------------------------------------- redacting ---


def find_sensitive_values(raw: dict[str, Any]) -> dict[str, str]:
    """Build a lookup of real identifying values -> the fake to replace each with.

    Each real value always gets the same fake, so links between devices survive
    redaction (e.g. a probe's gatewayId still points at GATEWAY-1).
    """
    fakes: dict[str, str] = {}
    gateway_count = 0
    probe_count = 0

    for device in raw["devices"]:
        # Give each device a readable name: GATEWAY-1, PROBE-1, PROBE-2, ...
        if "gateway" in (device.get("device_name") or "").lower():
            gateway_count += 1
            name = f"GATEWAY-{gateway_count}"
        else:
            probe_count += 1
            name = f"PROBE-{probe_count}"

        # The account ID goes first, because it also appears as dataset_id.
        add_fake(fakes, device.get("account_id"), "ACCOUNT-ID")

        # The device's own identifiers.
        add_fake(fakes, device.get("serial"), name)
        add_fake(fakes, device.get("device_id"), name)
        add_fake(fakes, device.get("iot_device_id"), f"{name}-IOT")

        big_query = device.get("big_query_info") or {}
        add_fake(fakes, big_query.get("table_id"), f"{name}-TABLE")

    # Done separately, after every device has its name: a probe's gatewayId is
    # the Gateway's iot_device_id, which is already in the lookup.
    for device in raw["devices"]:
        extra = device.get("additional_properties") or {}
        add_fake(fakes, extra.get("gatewayId"), "UNKNOWN-GATEWAY-IOT")

    return fakes


def add_fake(fakes: dict[str, str], real: Any, fake: str) -> None:
    """Record real -> fake, unless real is empty or already has a fake."""
    if real and str(real) not in fakes:
        fakes[str(real)] = fake


def redact(value: Any, fakes: dict[str, str]) -> Any:
    """Return a copy of value with every real identifier swapped for its fake.

    The data is nested (dictionaries inside lists inside dictionaries), so this
    function calls itself on each inner part until it reaches plain text.
    """
    if isinstance(value, dict):
        return {redact(key, fakes): redact(item, fakes) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, fakes) for item in value]
    if isinstance(value, str):
        # Longest first, so a whole serial is replaced before any shorter value
        # that happens to appear inside it.
        for real in sorted(fakes, key=len, reverse=True):
            value = value.replace(real, fakes[real])
        return EMAIL_PATTERN.sub("user@example.com", value)
    # Numbers, True/False and None contain nothing identifying.
    return value


# ----------------------------------------------------------------- saving ---


def save_json(data: dict[str, Any], path: Path) -> None:
    """Write data to path as readable JSON, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


async def main() -> None:
    # Uses the app's own download code, so the capture always matches what the
    # app sees.
    config = load_config(CONFIG_FILE)
    raw = await fetch_raw(config.eti_cloud)
    save_json(raw, RAW_FILE)

    fakes = find_sensitive_values(raw)
    redacted = redact(raw, fakes)
    save_json(redacted, FIXTURE_FILE)

    print(f"Saved your real data to {RAW_FILE} (git-ignored).")
    print(f"Saved a redacted copy to {FIXTURE_FILE}:")
    for device in redacted["devices"]:
        count = len(redacted["channels"].get(device["serial"], []))
        print(f"  {device['serial']}: {device['device_name']} ({count} channels)")
    print(f"Replaced {len(fakes)} identifying values. Read the fixture before committing.")


if __name__ == "__main__":
    asyncio.run(main())
