"""Temperature unit conversion helpers (pure functions).

ETI Cloud stores readings in °F; the display defaults to °C.
"""


def f_to_c(fahrenheit: float) -> float:
    """Convert degrees Fahrenheit to degrees Celsius."""
    return (fahrenheit - 32) * 5 / 9


def c_to_f(celsius: float) -> float:
    """Convert degrees Celsius to degrees Fahrenheit."""
    return celsius * 9 / 5 + 32
