# Test fixtures

Saved ETI Cloud responses, so tests run without network access or a live cook.

## Adding a capture

1. Dump the raw device/channel data from your account to `captures/` (git-ignored).
2. Copy it here and **redact** anything that identifies your account:
   `account_id`, `dataset_id`, emails, and ideally serials/MAC addresses
   (replace with obviously fake values such as `M000000001`).
3. Name it after what it shows, e.g. `idle_gateway_two_probes.json`,
   `billows_connected_live_cook.json`.

Useful scenarios to capture over time: idle kit, a live cook with Billows
connected, Gateway offline, probe battery low, a probe out of range.

## What's here

- `eti_idle.json`: kit switched off (air probe unplugged, Billows disconnected).
- `eti_fan_blowing.json`: Billows connected, set to 570 °F (298.9 °C), fan
  blowing (state 1). The pit is at about 23 °C, far below its low alarm, so ETI
  flags it as alarming. The probes are stale (last seen hours earlier).
  Captured 2026-10-05 20:57 UTC.
- `eti_fan_pulsing.json`: the same kit moments later, set to 77 °F (25 °C),
  fan pulsing (state 2). The pit is inside its alarm limits, so nothing is
  alarming. Captured 2026-10-05 20:59 UTC.

- `eti_fan_482f.json`: the same kit at 21:02 UTC, set to 482 °F (250 °C), fan
  blowing, pit at 73.4 °F (sent as a number this time, not text). The app's
  pit alarms are 507 °F and 457 °F, and the low one is alarming.

In all three fan captures the app had moved the pit alarms to 25 °F above and below
the set temperature.

## Traeger

Made with `scripts/capture_traeger.py` (read-only: it only listens, plus
Traeger's "send me your status" request). Each file holds the account's list
of grills and every status message heard while listening, redacted:

    uv run python scripts/capture_traeger.py off
    uv run python scripts/capture_traeger.py cooking --seconds 120

- `traeger_off.json`: a Pro 575 (firmware 02.02.05), switched off. One
  message: Traeger's cloud resending the grill's last status, from
  2026-09-05 18:55 UTC (`status.time`), when asked on 2026-10-30. Offline
  (system_status 99), °C (units 0), grill 16 °C, set 75 °C, pellets 30%, no
  probe plugged in.
