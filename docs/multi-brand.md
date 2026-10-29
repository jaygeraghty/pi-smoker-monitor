# Multi-brand design (parked)

Notes from planning how to support brands other than ETI. Nothing here is
built yet; this records where the discussion got to so it can be picked up
later. Status v2 (the status file's `pits` / `probes` / `sources` shape) is
already compatible with it.

## How other brands work

| Brand | How data is reached | What it has | Notes |
|---|---|---|---|
| ETI (current) | Unofficial cloud | Gateway (hub) with pit channel and optional Billows fan; probes as separate devices | Alarms set in the cloud |
| Meater | Cloud REST API (beta, unsupported) | Probes only: internal + ambient temperature, cook target, state, time remaining; the Block hub is invisible | Suggested 2 requests/min, max 60 |
| FireBoard | Official cloud REST API | A device with channels (any channel can be pit or food), per-channel alerts, Drive fan data | 17 calls per 5 minutes |
| Inkbird | No official API; Bluetooth or local Tuya Wi-Fi | Station plus probes (food + ambient), batteries, targets | Local only |
| Combustion Inc | Open Bluetooth spec and SDKs | Probes only, several sensors, predictions | Local only |
| Traeger | Unofficial cloud (MQTT) | A grill: grill temp, set temp, its own probes, pellets, mode | Read-only, always |

## Agreed direction

- Each brand is its own `Source` (`fetch() -> Snapshot`, `close()`), in its own
  module. The interface has no way to send commands, so read-only is built in.
- Each class stands on its own:
  - `Probe`: a food probe from any brand.
  - `Gateway`: a hub carrying a pit temperature and maybe a fan (ETI).
  - `Grill`: a cooker reporting its own grill and set temperature (Traeger).
    Added when the Traeger source is built.
- Snapshot gains an optional grill; each source fills in what it has:
  `Snapshot(taken_at, gateway: Gateway | None, grill: Grill | None, probes)`.
  ETI gives gateway + probes; Meater gives probes only; Traeger gives grill +
  its probes.
- The monitor keeps one Snapshot per source (not merged), with per-source
  errors, polling interval and timeouts.
- "No gateway found" fires only for sources that should have one
  (`expects_gateway`, true for ETI only).
- In the status file every entry has `source` (e.g. "ETI Cloud", "Traeger") and
  pits also have `kind` ("gateway" or "grill").

## Steps when picked up

1. `source` on `Probe` and `Gateway`, so ids never clash between brands.
2. Monitor holds a list of sources built once from config by a small factory
   (the only code that names brands); per-source state, polling and timeouts;
   "Can't reach {source}" alarm.
3. `expects_gateway` on each source.
4. `Grill` and `Snapshot.grill`, with the Traeger source.

## Open questions

- Rename `eti_unreachable_minutes` to `source_unreachable_minutes`?
- Probe ambient temperature (Meater, Inkbird, Combustion): add an optional
  field when the first such brand is added.
- Which brands are actually wanted beyond ETI and Traeger.
