"""Tests for temperature unit conversion."""

import pytest

from smoker_monitor.domain.units import c_to_f, f_to_c


def test_f_to_c_boiling_point() -> None:
    assert f_to_c(212) == pytest.approx(100)


def test_f_to_c_freezing_point() -> None:
    assert f_to_c(32) == pytest.approx(0)


def test_f_to_c_real_pit_reading() -> None:
    assert f_to_c(154) == pytest.approx(67.78, abs=0.01)


def test_c_to_f_boiling_point() -> None:
    assert c_to_f(100) == pytest.approx(212)


def test_c_to_f_freezing_point() -> None:
    assert c_to_f(0) == pytest.approx(32)


def test_minus_40_is_the_same_in_both() -> None:
    assert f_to_c(-40) == pytest.approx(-40)
    assert c_to_f(-40) == pytest.approx(-40)


@pytest.mark.parametrize("celsius", [-20.0, 0.0, 63.0, 107.5, 250.0])
def test_round_trip(celsius: float) -> None:
    assert f_to_c(c_to_f(celsius)) == pytest.approx(celsius)
