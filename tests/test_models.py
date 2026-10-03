"""Tests for the domain models."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from smoker_monitor.domain.models import Reading

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
