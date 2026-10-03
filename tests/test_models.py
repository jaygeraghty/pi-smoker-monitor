"""Tests for the domain models."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from smoker_monitor.domain.models import Probe, Reading

TEN_AM = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)


def test_age_is_time_since_reading() -> None:
    reading = Reading(celsius=107.0, taken_at=TEN_AM)
    now = TEN_AM + timedelta(minutes=5)
    assert reading.age(now) == timedelta(minutes=5)


def test_age_is_zero_when_taken_now() -> None:
    reading = Reading(celsius=107.0, taken_at=TEN_AM)
    now = TEN_AM
    assert reading.age(now) == timedelta(minutes=0)


def test_reading_cannot_be_changed() -> None:
    reading = Reading(celsius=107.0, taken_at=TEN_AM)
    with pytest.raises(FrozenInstanceError):
        reading.celsius = 120.0  # type: ignore[misc]


def test_missing_temperature_still_has_age() -> None:
    reading = Reading(celsius=None, taken_at=TEN_AM)
    assert reading.celsius is None
    now = TEN_AM + timedelta(minutes=5)
    assert reading.age(now) == timedelta(minutes=5)


def test_values_stored() -> None:
    reading = Reading(celsius=107.0, taken_at=TEN_AM)
    assert reading.celsius == 107.0
    assert reading.taken_at == TEN_AM


def test_readings_with_same_values_are_equal() -> None:
    reading1 = Reading(celsius=107.0, taken_at=TEN_AM)
    reading2 = Reading(celsius=107.0, taken_at=TEN_AM)
    assert reading1 == reading2


def reading(celsius: float | None) -> Reading:
    """Shorthand for a Reading taken at TEN_AM."""
    return Reading(celsius=celsius, taken_at=TEN_AM)


def probe(*sensors: Reading) -> Probe:
    """Shorthand for a Probe with the given sensors and dummy details."""
    return Probe(
        serial="M000000001",
        label=None,
        battery_pct=100,
        last_seen=TEN_AM,
        sensors=sensors,
    )


def test_core_is_lowest_sensor() -> None:
    p = probe(reading(70.0), reading(65.5), reading(72.0), reading(80.0))
    assert p.core_celsius() == 65.5


def test_core_ignores_missing_sensors() -> None:
    p = probe(reading(70.0), reading(None), reading(68.0), reading(None))
    assert p.core_celsius() == 68.0


def test_core_is_none_when_all_sensors_missing() -> None:
    p = probe(reading(None), reading(None), reading(None), reading(None))
    assert p.core_celsius() is None


def test_core_is_none_with_no_sensors() -> None:
    p = probe()
    assert p.core_celsius() is None


def test_core_includes_zero_degrees() -> None:
    """Regression: 0.0 is falsy, so a careless `if celsius:` would skip it."""
    p = probe(reading(0.0), reading(5.0))
    assert p.core_celsius() == 0.0
