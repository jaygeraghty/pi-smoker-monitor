# pi-smoker-monitor

A Raspberry Pi monitor and alarm for BBQ smokers. It reads pit, fan and meat
temperatures, shows them on a touchscreen and sounds a proper alarm when
something needs you — loud enough to wake you during an overnight cook.

Works with ETI/ThermoWorks **RFX** probes, the **RFX Gateway** and the
**Billows** fan via ETI Cloud. A DIY Pi-based pit controller is planned as a
second data source.

> Not affiliated with or endorsed by ETI (Electronic Temperature Instruments
> Ltd) or ThermoWorks. Product names are used only to describe compatibility.

## Status

Early development. See [docs/roadmap.md](docs/roadmap.md) for milestones.

## How it works

```
RFX probes ─► RFX Gateway ─► ETI Cloud ─► pi-smoker-monitor ─► screen
Billows fan ─┘                              │                ├► speaker + button
                                            └ alarm rules ───┴► Pushover (optional)
```

The core logic (models and alarm rules) knows nothing about ETI Cloud, GPIO or
speakers. Those live behind small interfaces, so everything can be developed
and tested on a normal PC. See [docs/architecture.md](docs/architecture.md).

## Getting started (development)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Jaygeraghty/pi-smoker-monitor
cd pi-smoker-monitor
uv sync                       # creates .venv with runtime + dev dependencies
uv run pre-commit install     # lint/format checks on every commit
cp config.example.toml config.toml   # then fill in your ETI Cloud login
uv run smoker --help
```

Checks (the same ones CI runs):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE). This matches the licence of the
`thermoworks-cloud` library it depends on.
