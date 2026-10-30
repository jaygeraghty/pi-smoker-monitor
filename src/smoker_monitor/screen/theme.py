"""How the screen looks: size, spacing, colours and fonts, all in one place."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PIL import ImageFont

# The screen, used landscape.
WIDTH = 800
HEIGHT = 480

# Spacing, in pixels.
MARGIN = 8  # around the edge of the screen
GAP = 10  # between tiles
RADIUS = 14  # how rounded the tiles' corners are
PIT_HEIGHT = 220  # the big pit tiles across the top

# Colours, as (red, green, blue).
Colour = tuple[int, int, int]
BACKGROUND = (14, 16, 20)
TILE = (30, 34, 42)
TEXT = (235, 237, 240)
DIM = (140, 147, 160)  # less important text
GOOD = (90, 200, 120)
WARNING = (240, 170, 60)
ALARM = (225, 60, 60)
STALE = (95, 100, 110)  # devices with no recent data

# DejaVu comes with Raspberry Pi OS (and most Linux).
FONT_FOLDER = Path("/usr/share/fonts/truetype/dejavu")

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@cache
def font(size: int, bold: bool = False) -> Font:
    """The text font at `size` pixels. Falls back to Pillow's own if DejaVu is missing."""
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_FOLDER / name), size)
    except OSError:
        return ImageFont.load_default(size)
