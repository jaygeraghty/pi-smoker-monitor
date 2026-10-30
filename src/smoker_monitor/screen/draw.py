"""Draw the whole screen from the status file, as a picture.

Everything here is a pure function: status in, picture out. No screen
hardware is involved, so you can preview it anywhere (scripts/preview_screen.py)
and test it like any other code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw

from smoker_monitor.screen.theme import (
    ALARM,
    BACKGROUND,
    DIM,
    GAP,
    GOOD,
    HEIGHT,
    MARGIN,
    PIT_HEIGHT,
    RADIUS,
    STALE,
    TEXT,
    TILE,
    WARNING,
    WIDTH,
    Colour,
    font,
)


def draw_screen(status: dict[str, Any], now: datetime) -> Image.Image:
    """The whole screen for this status. `now` is the local time, for the clock."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    top = draw_top_bar(draw, status, now)
    top = draw_pits(draw, status.get("pits", []), top)
    return image


def draw_top_bar(draw: ImageDraw.ImageDraw, status: dict[str, Any], now: datetime) -> int:
    """The clock on the left, and a dot per source on the right (green: working).

    Returns how far down the bar goes, so the tiles can start below it.
    """
    draw.text((16, 14), now.strftime("%H:%M"), font=font(24, bold=True), fill=TEXT)

    # Right to left, so the last source ends up nearest the edge.
    x = WIDTH - 16
    for source in reversed(status.get("sources", [])):
        label = f"{source['name']} ●"
        colour = GOOD if source.get("ok") else ALARM
        draw.text((x, 16), label, font=font(18), fill=colour, anchor="ra")
        x -= int(draw.textlength(label, font=font(18))) + 20
    return 52
    


# ------------------------------------------------------------------ pits ---

# A tile's edges, in pixels: (left, top, right, bottom).
Box = tuple[int, int, int, int]


def draw_pits(draw: ImageDraw.ImageDraw, pits: list[dict[str, Any]], top: int) -> int:
    """The pits side by side across the screen, starting at `top`.

    Returns how far down they go, so the probes can start below them.
    """
    if not pits:
        return top
    # Share the width equally: one pit fills it, two get half each.
    width = (WIDTH - 2 * MARGIN - GAP * (len(pits) - 1)) // len(pits)
    for number, pit in enumerate(pits):
        left = MARGIN + number * (width + GAP)
        draw_pit(draw, pit, (left, top, left + width, top + PIT_HEIGHT))
    return top + PIT_HEIGHT + GAP


def draw_pit(draw: ImageDraw.ImageDraw, pit: dict[str, Any], box: Box) -> None:
    """One pit tile: name, source, big temperature, set temperature, and extras."""
    left, top, right, bottom = box
    draw.rounded_rectangle(box, RADIUS, fill=TILE)
    fresh = pit.get("fresh", False)
    name_colour = TEXT if fresh else STALE
    draw.text((left + 16, top + 12), pit["name"], font=font(18, bold=True), fill=name_colour)
    draw.text((right - 16, top + 14), pit["source"], font=font(14), fill=DIM, anchor="ra")

    if not fresh:
        # Old numbers would look current, so don't show them at all.
        draw.text((left + 16, top + 60), "no recent data", font=font(24), fill=STALE)
        return

    big = font(96, bold=True)
    draw.text((left + 16, top + 40), format_temp(pit["temp_c"]), font=big, fill=TEXT)
    draw.text((left + 16, bottom - 52), set_line(pit), font=font(18), fill=set_colour(pit))
    draw.text((left + 16, bottom - 28), extra_line(pit), font=font(16), fill=DIM)


def format_temp(celsius: float | None, decimals: int = 0) -> str:
    """e.g. "112°", or "--" when there's no reading."""
    return "--" if celsius is None else f"{celsius:.{decimals}f}°"


def set_line(pit: dict[str, Any]) -> str:
    """e.g. "set 110°  (+2)": the set temperature, and how far the pit is from it."""
    if pit["set_c"] is None:
        return "no set temperature"
    line = f"set {pit['set_c']:.0f}°"
    if pit["temp_c"] is not None:
        line += f"  ({pit['temp_c'] - pit['set_c']:+.0f})"
    return line


def set_colour(pit: dict[str, Any]) -> Colour:
    """Green while inside its alarm limits, amber once outside them."""
    temp, high, low = pit["temp_c"], pit["high_c"], pit["low_c"]
    if temp is None:
        return DIM
    if (high is not None and temp >= high) or (low is not None and temp <= low):
        return WARNING
    return GOOD


def extra_line(pit: dict[str, Any]) -> str:
    """Up to two extras, e.g. "Fan pulsing · Battery 75%" or "Cooking · Pellets 70%"."""
    parts = []
    if pit.get("fan"):
        parts.append(f"Fan {pit['fan']}")
    for detail in pit.get("details", []):
        if detail["label"] == "Status":
            parts.append(detail["value"].capitalize())
        else:
            parts.append(f"{detail['label']} {detail['value']}")
    return " · ".join(parts[:2])

