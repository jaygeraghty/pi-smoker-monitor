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
