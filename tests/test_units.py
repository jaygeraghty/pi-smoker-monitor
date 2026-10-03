import pytest
from smoker_monitor.domain.units import f_to_c

def test_boiling_point() -> None:
    assert f_to_c(212) == pytest.approx(100)
    
def test_32f() -> None:
    assert f_to_c(32) == pytest.approx(0)

def test_154f() -> None:
    assert f_to_c(154) == pytest.approx(67.777)

