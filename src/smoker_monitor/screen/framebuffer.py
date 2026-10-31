"""Putting a picture on the Pi's screen, through the Linux framebuffer.

The framebuffer (/dev/fb0) is the screen's memory: write pixels to it and
they appear. No desktop is needed, which suits Raspberry Pi OS Lite.

Two things vary from screen to screen, so they're read from the system:
- its size and way round. The HyperPixel 4.0 is portrait (480 x 800) by
  nature, so our landscape picture (800 x 480) is turned a quarter before
  being shown.
- how each pixel is stored: 16 bits (RGB565, 5 bits red, 6 green, 5 blue)
  or 32 bits (blue, green, red, unused).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

SYS_GRAPHICS = Path("/sys/class/graphics")


@dataclass(frozen=True)
class ScreenInfo:
    """What the framebuffer looks like."""

    width: int  # pixels across, as the screen is mounted by default
    height: int
    bits_per_pixel: int  # 16 or 32
    stride: int  # bytes from the start of one row to the next


def read_screen_info(device: Path, sys_graphics: Path = SYS_GRAPHICS) -> ScreenInfo:
    """Read the framebuffer's size and pixel format, e.g. for /dev/fb0."""
    folder = sys_graphics / device.name
    bits = int((folder / "bits_per_pixel").read_text())
    width, height = (int(n) for n in (folder / "virtual_size").read_text().split(","))
    # The visible size, if it says (the memory can be taller, for double buffering).
    modes = folder / "modes"
    if modes.exists():
        match = re.search(r"(\d+)x(\d+)", modes.read_text())
        if match:
            width, height = int(match[1]), int(match[2])
    stride_file = folder / "stride"
    stride = int(stride_file.read_text()) if stride_file.exists() else width * bits // 8
    return ScreenInfo(width, height, bits, stride)


def to_pixels(image: Image.Image, info: ScreenInfo) -> bytes:
    """The picture as the framebuffer stores it. Its size must match the screen's."""
    if image.size != (info.width, info.height):
        raise ValueError(f"Picture is {image.size}, screen is {(info.width, info.height)}")
    rgb = image.convert("RGB")
    if info.bits_per_pixel == 32:
        data = rgb.tobytes("raw", "BGRX")
    elif info.bits_per_pixel == 16:
        data = rgb565(rgb)
    else:
        raise ValueError(f"Unsupported screen format: {info.bits_per_pixel} bits per pixel")
    row = info.width * info.bits_per_pixel // 8
    if info.stride == row:
        return data
    # Each row is padded out to the stride.
    padding = bytes(info.stride - row)
    return b"".join(data[start : start + row] + padding for start in range(0, len(data), row))


def rgb565(image: Image.Image) -> bytes:
    """The picture as 16-bit pixels: rrrrrggg gggbbbbb, low byte first.

    Done with whole-picture operations (fast), not pixel by pixel (slow):
    the low byte is the bottom 3 bits of green and the top 5 of blue; the
    high byte the top 5 bits of red and the top 3 of green. Their bits don't
    overlap, so adding them together combines them.
    """
    red, green, blue = image.split()
    low = ImageChops.add(green.point(lambda v: (v << 3) & 0xE0), blue.point(lambda v: v >> 3))
    high = ImageChops.add(red.point(lambda v: v & 0xF8), green.point(lambda v: v >> 5))
    return Image.merge("LA", (low, high)).tobytes()


class Framebuffer:
    """The real screen. `show()` puts a picture on it."""

    def __init__(
        self,
        device: Path = Path("/dev/fb0"),
        rotate: int = 90,
        sys_graphics: Path = SYS_GRAPHICS,
    ) -> None:
        """`rotate`: degrees anticlockwise to turn our picture when the screen is
        portrait (try 270 if it comes out upside down)."""
        self.device = device
        self.rotate = rotate
        self.info = read_screen_info(device, sys_graphics)

    def show(self, image: Image.Image) -> None:
        if image.size != (self.info.width, self.info.height):
            image = image.rotate(self.rotate, expand=True)
        with self.device.open("r+b") as screen:
            screen.write(to_pixels(image, self.info))
