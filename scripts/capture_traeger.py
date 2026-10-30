"""Save what your Traeger grill sends, plus a redacted copy for tests.

This is a developer tool, not part of the app. It logs in to Traeger with the
[traeger_mqtt] details in config.toml, finds your grills, listens to their
status messages for a while (60 seconds unless you say otherwise), and writes:

    captures/traeger_<what>_raw.json         your real data (git-ignored)
    tests/fixtures/traeger_<what>.json       the same with anything that
                                             identifies you swapped for fakes

READ-ONLY: it only listens. The one thing it sends is Traeger's "send me your
status" request (command 90), the same as opening the app. It can't change
anything on the grill.

Run it from the repo root, saying what the grill is doing, e.g.:
    uv run python scripts/capture_traeger.py off
    uv run python scripts/capture_traeger.py cooking --seconds 120

For a "cooking" capture, have the grill running at a set temperature, ideally
with the meat probe plugged in. Read the redacted file before sharing it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from smoker_monitor.config import load_config
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.traeger_cloud import GrillMessage, TraegerCloud

CONFIG_FILE = Path("config.toml")

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Anything shaped like a MAC address (00:11:22:aa:bb:cc or 001122AABBCC-ish).
MAC_PATTERN = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
IP_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Keys whose text values identify you, your grill or your network. Matched
# against the lower-cased key with any "_" removed, so "thing_name",
# "thingName" and "THINGNAME" all count.
SENSITIVE_KEY_PARTS = (
    "thingname",
    "friendlyname",
    "name",
    "userid",
    "user",
    "owner",
    "email",
    "serial",
    "mac",
    "ssid",
    "ip",
    "uuid",
    "token",
    "url",
    "address",
    "cognito",
    "phone",
    "lat",
    "lon",
)
# Shorter values are only replaced where they are the whole value.
MIN_SWAP_LENGTH = 6

# ...except these, which describe the kit, not you.
SAFE_KEYS = {"type", "units", "devicetypeid", "modelnumber", "model"}


# -------------------------------------------------------------- redacting ---


def is_sensitive_key(key: str) -> bool:
    flat = key.lower().replace("_", "")
    if flat in SAFE_KEYS:
        return False
    return any(part in flat for part in SENSITIVE_KEY_PARTS)


def find_sensitive_values(value: Any, fakes: dict[str, str], key: str = "") -> None:
    """Collect every text value under a sensitive key, each with its own fake.

    The same real value always gets the same fake ("GRILL-1", "VALUE-3"), so
    links survive: a grill's thingName in the list of grills still matches the
    one in its messages.
    """
    if isinstance(value, dict):
        for inner_key, item in value.items():
            find_sensitive_values(item, fakes, str(inner_key))
    elif isinstance(value, list):
        for item in value:
            find_sensitive_values(item, fakes, key)
    elif isinstance(value, str) and value and is_sensitive_key(key) and value not in fakes:
        flat = key.lower().replace("_", "")
        if flat == "thingname":
            fakes[value] = f"GRILL-{sum(f.startswith('GRILL-') for f in fakes.values()) + 1}"
        elif flat == "friendlyname":
            fakes[value] = "My Traeger"
        else:
            fakes[value] = f"VALUE-{len(fakes) + 1}"


def redact(value: Any, fakes: dict[str, str]) -> Any:
    """A copy of value with every real identifier swapped for its fake."""
    if isinstance(value, dict):
        return {redact(key, fakes): redact(item, fakes) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, fakes) for item in value]
    if isinstance(value, str):
        if value in fakes:
            return fakes[value]
        # Long values are also swapped inside other text (e.g. a grill's id
        # inside a topic). Short ones only as a whole, so a two-letter name
        # can't mangle every word containing those letters.
        for real in sorted(fakes, key=len, reverse=True):  # longest first
            if len(real) >= MIN_SWAP_LENGTH:
                value = value.replace(real, fakes[real])
        value = EMAIL_PATTERN.sub("user@example.com", value)
        value = MAC_PATTERN.sub("00:00:00:00:00:00", value)
        return IP_PATTERN.sub("0.0.0.0", value)
    return value


# -------------------------------------------------------------- capturing ---


async def capture(seconds: float) -> dict[str, Any]:
    """Log in, list the grills, and collect their messages for `seconds`."""
    config = load_config(CONFIG_FILE)
    if config.traeger_mqtt is None:
        raise SystemExit(f"{CONFIG_FILE} has no [traeger_mqtt] section, so there's nothing to do.")

    messages: list[GrillMessage] = []
    started = time.time()
    async with TraegerCloud(config.traeger_mqtt) as cloud:
        grills = await cloud.grills()
        if not grills:
            raise SystemExit("Your Traeger account has no grills.")
        print(f"Found {len(grills)} grill(s). Listening for {seconds:g} s...")
        things = [grill["thingName"] for grill in grills]
        await cloud.listen(things, messages.append, seconds=seconds)

    return {
        "grills": grills,
        "messages": [
            {
                "topic_thing": m.thing,
                "seconds_after_start": round(m.received_at - started, 1),
                "payload": m.payload,
            }
            for m in messages
        ],
    }


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("what", help='what the grill is doing, e.g. "off" or "cooking"')
    parser.add_argument("--seconds", type=float, default=60, help="how long to listen (60)")
    args = parser.parse_args()
    what = re.sub(r"[^a-z0-9_]+", "_", args.what.lower()).strip("_") or "capture"

    try:
        raw = asyncio.run(capture(args.seconds))
    except SourceError as e:
        raise SystemExit(f"Couldn't capture: {e}") from None

    raw_file = Path(f"captures/traeger_{what}_raw.json")
    fixture_file = Path(f"tests/fixtures/traeger_{what}.json")
    save_json(raw, raw_file)

    fakes: dict[str, str] = {}
    find_sensitive_values(raw, fakes)
    redacted = redact(raw, fakes)
    save_json(redacted, fixture_file)

    print(f"Got {len(raw['messages'])} message(s).")
    if not raw["messages"]:
        print("  None at all: is the grill switched on and connected to Wi-Fi?")
    print(f"Saved your real data to {raw_file} (git-ignored, keep it private).")
    print(f"Saved a redacted copy to {fixture_file}.")
    print(f"Replaced {len(fakes)} identifying values. Read the redacted file before sharing.")


if __name__ == "__main__":
    main()
