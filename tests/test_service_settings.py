"""The monitor and settings.json: changes made on the web page take effect live."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import timedelta
from pathlib import Path

from smoker_monitor.domain.alarms import AlarmKind, AlarmRules
from smoker_monitor.service.monitor import (
    END_COOK_FILE,
    Monitor,
    request_end_cook,
)
from smoker_monitor.service.settings import SETTINGS_FILE, update_settings
from smoker_monitor.service.status import STATUS_FILE
from tests.test_service import (
    START,
    Clock,
    RecordingNotifier,
    ScriptedSource,
    gateway,
    make_monitor,
    probe,
    snapshot,
)


def poll(monitor: Monitor) -> None:
    asyncio.run(monitor.poll_once())


def write_settings(tmp_path: Path, **parts: object) -> None:
    update_settings(
        tmp_path / SETTINGS_FILE, monitor_base(), {"revision": revision(tmp_path), **parts}
    )


def monitor_base() -> AlarmRules:
    return AlarmRules()


def revision(tmp_path: Path) -> int:
    path = tmp_path / SETTINGS_FILE
    return int(json.loads(path.read_text())["revision"]) if path.exists() else 0


def settle(monitor: Monitor) -> None:
    """Let the monitor notice a change made between polls (it checks every second)."""
    asyncio.run(monitor.wait_for_next_poll(1.0))


def kinds(monitor: Monitor) -> set[AlarmKind]:
    return {t.alarm.kind for t in monitor.state.tracked.values()}


def probe_status(tmp_path: Path) -> dict[str, object]:
    """The status file's only probe."""
    [only] = json.loads((tmp_path / STATUS_FILE).read_text(encoding="utf-8"))["probes"]
    assert isinstance(only, dict)
    return only


def status(tmp_path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((tmp_path / STATUS_FILE).read_text(encoding="utf-8"))
    return data


def test_without_a_settings_file_the_config_rules_apply(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(tmp_path, snapshot(START))
    poll(monitor)
    assert monitor.rules == monitor.base_rules


def test_your_own_target_is_used_and_shown_as_set_on_the_pi(tmp_path: Path) -> None:
    here = probe(START, core=60.0)
    monitor, _, _ = make_monitor(tmp_path, snapshot(START, probes=(here,)))
    write_settings(tmp_path, devices={"eti:P1": {"high": {"mode": "set", "c": 55}}})
    poll(monitor)
    assert AlarmKind.PROBE_HIGH in kinds(monitor)  # 60 °C is over your 55 °C target
    assert probe_status(tmp_path)["target_c"] == 55.0
    assert probe_status(tmp_path)["target_origin"] == "pi"


def test_a_switched_off_side_silences_even_the_eti_limit(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(tmp_path, snapshot(START, pit=175.0))  # over ETI's 170
    poll(monitor)
    assert AlarmKind.PIT_HIGH in kinds(monitor)
    write_settings(tmp_path, devices={"eti:G1": {"high": {"mode": "off"}}})
    settle(monitor)
    assert AlarmKind.PIT_HIGH not in kinds(monitor)


def test_a_nickname_reaches_the_alarm_message_and_the_status(tmp_path: Path) -> None:
    later = START + timedelta(seconds=30)
    monitor, clock, notifier = make_monitor(
        tmp_path,
        snapshot(START, probes=(probe(START, core=99.0),)),
        snapshot(later, probes=(probe(later, core=99.0),)),
    )
    write_settings(
        tmp_path,
        devices={"eti:P1": {"name": "Brisket", "high": {"mode": "set", "c": 93}}},
    )
    poll(monitor)
    clock.now = later
    poll(monitor)  # seen twice: now it rings
    assert probe_status(tmp_path)["name"] == "Brisket"
    assert any("Brisket" in m for m in notifier.updates[-1][1])


def test_switching_an_alarm_off_in_settings_stops_it_and_is_listed(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(tmp_path, snapshot(START, pit=175.0))
    poll(monitor)
    write_settings(tmp_path, alarms={"pit_high": False})
    settle(monitor)
    assert AlarmKind.PIT_HIGH not in kinds(monitor)
    assert status(tmp_path)["alarms_off"] == ["pit_high", "probe_low"]


def test_changes_apply_without_waiting_for_the_next_poll(tmp_path: Path) -> None:
    monitor, clock, _ = make_monitor(tmp_path, snapshot(START, pit=175.0))
    poll(monitor)
    assert AlarmKind.PIT_HIGH in kinds(monitor)
    write_settings(tmp_path, alarms={"pit_high": False})

    settle(monitor)  # one second, no fetch

    assert AlarmKind.PIT_HIGH not in kinds(monitor)
    assert monitor.states[0].source.fetches == 1  # type: ignore[attr-defined]
    assert clock.now == START


def test_a_damaged_settings_file_keeps_the_last_good_settings(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(tmp_path, snapshot(START, pit=175.0), snapshot(START, pit=175.0))
    write_settings(tmp_path, alarms={"pit_high": False})
    poll(monitor)
    assert monitor.rules.pit_high is False

    (tmp_path / SETTINGS_FILE).write_text("{ broken", encoding="utf-8")
    poll(monitor)

    assert monitor.rules.pit_high is False  # still the last good ones
    assert AlarmKind.PIT_HIGH not in kinds(monitor)


def test_deleting_the_settings_file_goes_back_to_the_config_rules(tmp_path: Path) -> None:
    monitor, _, _ = make_monitor(tmp_path, snapshot(START, pit=175.0), snapshot(START, pit=175.0))
    write_settings(tmp_path, alarms={"pit_high": False})
    poll(monitor)
    (tmp_path / SETTINGS_FILE).unlink()
    poll(monitor)
    assert monitor.rules.pit_high is True
    assert AlarmKind.PIT_HIGH in kinds(monitor)


# ---------------------------------------------------------- end cook ---


def pulled_out_probe_setup(tmp_path: Path) -> tuple[Monitor, Clock]:
    """A probe is in use, then ETI stops listing it (pulled out and switched off)."""
    first = snapshot(START, probes=(probe(START),))
    later = START + timedelta(minutes=10)
    monitor, clock, _ = make_monitor(tmp_path, first, snapshot(later), snapshot(later))
    return monitor, clock


def test_a_probe_that_goes_quiet_rings_until_the_cook_is_ended(tmp_path: Path) -> None:
    monitor, clock = pulled_out_probe_setup(tmp_path)
    poll(monitor)
    clock.now = START + timedelta(minutes=10)
    poll(monitor)
    assert AlarmKind.PROBE_TIMEOUT in kinds(monitor)

    request_end_cook(tmp_path)
    settle(monitor)

    assert AlarmKind.PROBE_TIMEOUT not in kinds(monitor)
    assert not (tmp_path / END_COOK_FILE).exists()  # the request was used up
    assert monitor.live_probes == set()


def test_an_old_end_cook_request_is_ignored_at_start(tmp_path: Path) -> None:
    request_end_cook(tmp_path)
    source = ScriptedSource(snapshot(START))
    monitor = Monitor([source], monitor_base(), RecordingNotifier(), tmp_path, Clock())

    async def one_poll_then_stop() -> None:
        async def stop(_: float) -> None:
            raise KeyboardInterrupt

        monitor.sleep = stop
        with contextlib.suppress(KeyboardInterrupt):
            await monitor.run_forever()

    asyncio.run(one_poll_then_stop())
    assert not (tmp_path / END_COOK_FILE).exists()


def test_gateway_helper_is_the_one_the_settings_ids_refer_to() -> None:
    assert gateway(START).id == "eti:G1"
    assert probe(START).id == "eti:P1"
