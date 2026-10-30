# Adding a brand

The app reads any number of brands at once (ETI and Traeger today). Each brand
is a **source**: a class that fetches that brand's data and turns it into the
shared parts every brand uses. The rest of the app (alarms, the status file,
screens, the log) only ever sees those shared parts, so a new brand needs no
changes there.

## The shared parts (`domain/models.py`)

| Part | What it is | Who uses it |
| --- | --- | --- |
| `Probe` | A food probe: sensors, target (high alarm), battery | ETI, Traeger |
| `Gateway` | A hub carrying a pit probe and maybe a fan | ETI (RFX Gateway + Billows) |
| `Grill` | A cooker reporting its own temperature, mode and pellets | Traeger |

Rules that apply to every part:
- Temperatures are always °C. Convert from °F in the source.
- Missing data is `None`, never a made-up 0.
- `last_seen` is when the device itself last reported, not when we fetched
  it. (Traeger's cloud resends old status, so it uses the grill's own clock.)
- `source` is the brand's key (e.g. `"traeger"`). Ids become `"traeger:..."`,
  so two brands can never clash.
- Names your user gave a device go in `name`; the model or type in `label`.
  Serials are never shown on screens.

If a brand has something none of these fit, add an optional field (default
`None`) to the closest part rather than a new part, unless it's genuinely a
new kind of device.

## Step by step

1. **Capture real data first.** Write `scripts/capture_<brand>.py` (copy
   `capture_traeger.py`): log in, save the raw data to `captures/`
   (git-ignored) and a redacted copy to `tests/fixtures/`. Redact names, ids,
   serials, MAC and IP addresses, Wi-Fi names, emails and locations. Read the
   redacted file before committing it.

2. **Settings.** In `config.py`, add a frozen settings dataclass (password as
   `field(repr=False)`, plus `poll_seconds`), read its optional section in
   `load_config` (`_poll_seconds` gives the default), and count it in the
   "Nothing to monitor" check. Add the section, commented, to
   `config.example.toml`.

3. **The source class**, in `sources/<brand>.py`. It needs:

   ```python
   class MyBrandSource:
       key = "mybrand"  # short, unique: prefixes every device id
       name = "My Brand"  # shown on screens and in alarms
       expects_gateway = False  # True only if "No gateway found" should fire

       def __init__(self, settings: MyBrandSettings) -> None:
           self.poll_seconds = settings.poll_seconds

       async def fetch(self) -> Snapshot: ...  # raise SourceError on failure
       async def close(self) -> None: ...
   ```

   Keep it in two halves, like `eti_cloud.py` and `traeger.py`: the network
   part, and pure mapping functions (`to_snapshot` and friends) that turn raw
   data into the shared parts. The mapping is what the tests check against
   the fixture.

   - `fetch()` raises `SourceError` with a message safe to show (never the
     password) when it can't get data. The monitor then keeps that brand's
     last good data and, after `source_unreachable_minutes`, rings
     "Can't reach My Brand".
   - It must be **read-only**: fetch readings, never send commands to a
     device. If the brand's API needs a request to send data (like Traeger's
     "90"), allow only that, behind an allow-list that refuses everything else.

4. **The factory.** One line in `sources/factory.py`:

   ```python
   if config.mybrand is not None:
       sources.append(MyBrandSource(config.mybrand))
   ```

5. **Tests.** Mapping tests against the fixture (temperatures, names, ids,
   missing data), a test that the source raises `SourceError` cleanly, and a
   factory test.

## What you get for free

Once the source returns the shared parts, the rest already works:
- alarms: temperatures, targets, batteries, "gone quiet", "can't reach", and
  grill off-target and pellets; plus a probe from this cook that the brand
  stops listing is kept and goes stale, so it still rings
- the status file (`pits` and `probes`, each with its `source`), the report,
  and the log line
- display names, with duplicates numbered ("Pro 575 1", "Pro 575 2")
- polling on its own schedule, and one brand failing never stops the others
