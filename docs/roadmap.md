# Roadmap

## Milestone 0 — Scaffolding ✅
Repo layout, tooling (uv, ruff, mypy, pytest, pre-commit), CI, docs.

## Milestone 1 — Read the data
- [ ] `config`: load `config.toml` into typed settings
- [ ] `domain`: Snapshot, Pit, Fan, Probe, Reading models + °F/°C helpers
- [ ] `sources`: `Source` protocol, `EtiCloudSource`, replay source for tests
- [ ] Redacted ETI Cloud capture in `tests/fixtures/`
- [ ] Tests: mapping fixture → Snapshot; unit conversion; staleness
- [ ] `smoker snapshot` prints pit, Billows and probe readings in °C

## Milestone 2 — Alarm logic
- [ ] Rules: pit drift, meat at target (lowest sensor), stale data,
      Billows disconnected, probe battery low
- [ ] Alarm state machine: OK → ALARMING → ACKNOWLEDGED, hysteresis, cooldown
- [ ] Thorough unit tests with a fake clock

## Milestone 3 — Run as a service
- [ ] Async service loop with retries and back-off
- [ ] Notifiers: console, sound, Pushover
- [ ] Hardware: acknowledge button + LED (with PC fake)
- [ ] `smoker run`; systemd unit in `deploy/systemd/`
- [ ] Live test on a real cook

## Milestone 4 — Touchscreen UI
- [ ] Choose framework; pit, set temp, fan, probes, alarm banner, acknowledge

## Later
- DIY pit-controller Pi as a second `Source` (MQTT)
- Cook history / graphs
