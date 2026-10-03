import pytest
from smoker_monitor.domain.units import f_to_c

def test_boiling_point() -> None:
    assert f_to_c(212) == pytest.approx(100)

