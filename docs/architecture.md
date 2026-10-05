# Architecture

## Principle: ports and adapters

The core (domain models and alarm rules) has no I/O and no third-party
imports. Everything that touches the outside world — ETI Cloud, GPIO, the
speaker, Pushover, the screen — is an adapter behind a small interface
(`typing.Protocol`, the Python equivalent of a C# interface).

Why: you can develop and test almost everything on a PC, replaying saved ETI
Cloud data, without a Pi, a live cook or a network connection. And the DIY
pit-controller Pi becomes just another `Source` later.

## Dependency direction

```
cli ──► service ──► alarms ──► domain
          │  │                    ▲
          │  └──► notifiers ──────┤
          │  └──► hardware        │
          └─────► sources ────────┘
ui ──► (reads Snapshots + alarm states published by service)
```

Arrows point at what a module may import. `domain` imports nothing from the
package; nothing imports `cli`.

## Modules

| Module | Responsibility | Milestone |
| --- | --- | --- |
| `domain` | Snapshot, Probe, Pit, Fan, Reading; unit conversion; staleness | 1 |
| `config` | Load and validate `config.toml` into typed settings | 1 |
| `sources` | `Source` protocol; ETI Cloud adapter; fake/replay source | 1 |
| `cli` | `smoker snapshot`, `smoker run`, `smoker silence` | 1, 3 |
| `domain.alarms`, `domain.alarm_state` | Alarm rules, and alarm memory (confirm, clear, silence) | 2 |
| `notifiers` | Sound, Pushover, console behind one protocol | 3 |
| `hardware` | Acknowledge button + LED (gpiozero), with a fake | 3 |
| `service` | Poll → evaluate → notify loop; writes `state/status.json` for screens | 3 |
| `ui` | Touchscreen display | 4 |

## Data from ETI Cloud (observed, October 2026)

- RFX Gateway: `device_name` "rfx gateway"; channel 1 is the air (pit) probe;
  a `fan` block holds Billows `connected`, `set_temp`, `state`.
- RFX MEAT: `device_name` "rfx meat"; channels 1–4 are sensors along the probe.
- All temperatures are °F. Each device and channel has `last_seen`.
- Transmit interval 60 s, so cloud data is normally at most ~1 minute old.

## Decisions log

Record notable decisions as short entries in `docs/decisions/` (one file each:
context, decision, consequences). First candidates: Python 3.11+ only;
GPL-3.0 licence; one internal temperature unit.
