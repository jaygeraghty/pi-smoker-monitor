"""Temperature unit conversion helpers (pure functions).

ETI Cloud stores readings in °F; the display defaults to °C.
"""


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32) * (5 / 9)
