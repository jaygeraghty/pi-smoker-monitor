"""Tests for the plain-text snapshot report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smoker_monitor.domain.models import Fan, Gateway, Reading, Snapshot
from smoker_monitor.report import format_age, format_snapshot, format_temp
from smoker_monitor.sources.eti_cloud import to_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "eti_idle.json"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def idle_report() -> str:
    """The report for the real (redacted) capture."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return format_snapshot(to_snapshot(raw, NOW), NOW)


def live_gateway(pit_celsius: float, fan: Fan | None) -> Gateway:
    """A Gateway seen one minute before NOW."""
    seen = NOW - timedelta(minutes=1)
    return Gateway(
        serial="G1",
        label="RFX GATEWAY",
        battery_pct=80,
        wifi_dbm=-50,
        last_seen=seen,
        pit=Reading(celsius=pit_celsius, taken_at=seen),
        fan=fan,
    )


def test_idle_report_lists_every_device() -> None:
    report = idle_report()
    assert "GATEWAY-1" in report
    assert "PROBE-1" in report
    assert "PROBE-2" in report


def test_idle_report_explains_missing_pit_probe() -> None:
    assert "Pit:   no reading" in idle_report()


def test_idle_report_shows_fan_not_connected() -> None:
    assert "Fan:   not connected" in idle_report()


def test_idle_report_shows_probe_temps_in_celsius() -> None:
    # PROBE-1's coldest sensor was 96.1°F, which is 35.6°C.
    assert "Core:    35.6°C" in idle_report()


def test_idle_report_warns_data_is_stale() -> None:
    assert "WARNING" in idle_report()


def test_fresh_report_has_no_warning() -> None:
    snapshot = Snapshot(taken_at=NOW, gateway=live_gateway(107.0, None), probes=())
    assert "WARNING" not in format_snapshot(snapshot, NOW)


def test_connected_fan_shows_set_temp_and_deviation() -> None:
    fan = Fan(set_temp_celsius=107.0, state=1, connected=True)
    snapshot = Snapshot(taken_at=NOW, gateway=live_gateway(110.0, fan), probes=())
    report = format_snapshot(snapshot, NOW)
    assert "set to 107.0°C" in report
    assert "+3.0°C from the set temperature" in report


def test_missing_gateway_is_explained() -> None:
    snapshot = Snapshot(taken_at=NOW, gateway=None, probes=())
    report = format_snapshot(snapshot, NOW)
    assert "No RFX Gateway found" in report
    assert "WARNING" in report


@pytest.mark.parametrize(
    ("age", "words"),
    [
        (timedelta(seconds=-5), "just now"),
        (timedelta(seconds=45), "45 s ago"),
        (timedelta(minutes=5), "5 min ago"),
        (timedelta(hours=3), "3 h ago"),
        (timedelta(days=1), "1 day ago"),
        (timedelta(days=13), "13 days ago"),
    ],
)
def test_format_age(age: timedelta, words: str) -> None:
    assert format_age(age) == words


def test_format_temp() -> None:
    assert format_temp(107.25) == "107.2°C"
    assert format_temp(None) == "--"
