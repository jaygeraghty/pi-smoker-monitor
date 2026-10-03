"""ETI Cloud adapter: wraps the `thermoworks-cloud` library.

Responsibilities:
- Authenticate with the ETI Cloud Firebase settings from config
  (api_key, app_id, referer) and your email/password.
- Keep one aiohttp session and auth object alive between polls.
- Fetch devices and channels, then map them to domain models:
    * device_name "rfx gateway"  -> Pit (its channel 1 = air probe) + Fan (its `fan` block)
    * device_name "rfx meat"     -> Probe (channels 1-4 = sensors along the probe)
- Convert library datetimes and °F values into the domain's conventions.

Known quirks:
- On Python 3.10 the library can't parse timestamps ending in "Z". This project
  requires Python 3.11+, which parses them natively, so no patch is needed.
- `fan.set_temp` units are unconfirmed (saw 570 with Billows disconnected).
  Verify on a live cook before relying on it.
"""

# TODO(milestone 1): implement EtiCloudSource.
