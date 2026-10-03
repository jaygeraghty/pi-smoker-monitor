"""Temperature unit conversion helpers (pure functions).

ETI Cloud stores readings in °F; the display defaults to °C.
"""

# TODO(milestone 1): f_to_c, c_to_f, and a formatter for display.
def f_to_c(fahrenheit: float) -> float:
    return((fahrenheit - 32) * (5/9))
