"""Draw the whole screen from the status file, as a picture.

Everything here is a pure function: status in, picture out. No screen
hardware is involved, so you can preview it anywhere (scripts/preview_screen.py)
and test it like any other code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw

from smoker_monitor.screen.theme import ALARM, BACKGROUND, GOOD, HEIGHT, TEXT, WIDTH, font


def draw_screen(status: dict[str, Any], now: datetime) -> Image.Image:
    """The whole screen for this status. `now` is the local time, for the clock."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw_top_bar(draw, status, now)
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
