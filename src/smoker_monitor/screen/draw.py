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
    ALARM_BAR_HEIGHT,
    BACKGROUND,
    BAR_TRACK,
    DIM,
    GAP,
    GOOD,
    HEIGHT,
    MARGIN,
    PIT_HEIGHT,
    RADIUS,
    RING_COLOURS,
    STALE,
    TEXT,
    TILE,
    TOP_BAR_HEIGHT,
    WARNING,
    WIDTH,
    Colour,
    Font,
    font,
)

# If the status file is older than this, the monitor itself has stopped.
STALE_STATUS_SECONDS = 120

# Width of the dot beside each source's name in the top bar.
DOT = 14


def draw_screen(status: dict[str, Any], now: datetime) -> Image.Image:
    """The whole screen for this status. `now` is the local time, for the clock.

    While an alarm rings, a red bar replaces the clock along the top. Each
    device with an alarm gets an outline: red while ringing, amber once
    silenced. If the monitor itself has stopped writing the status, an amber
    bar says so and every reading is greyed out, because none can be trusted
    and no alarm can ring.
    """
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    alarms = status.get("alarms", [])
    ringing = [alarm for alarm in alarms if alarm.get("status") == "ringing"]
    age = status_age_seconds(status, now)
    if age is None or age > STALE_STATUS_SECONDS:
        top = draw_stopped_bar(draw, age)
        status = with_nothing_fresh(status)
    elif ringing:
        top = draw_alarm_bar(draw, ringing)
    else:
        top = draw_top_bar(draw, status, now)
    outlines = alarm_outlines(alarms)
    top = draw_pits(draw, status.get("pits", []), top, outlines)
    draw_probes(draw, status.get("probes", []), top, outlines)
    return image


def alarm_outlines(alarms: list[dict[str, Any]]) -> dict[str, Colour]:
    """Outline colour for each device with an alarm: red ringing, amber silenced."""
    outlines: dict[str, Colour] = {}
    for alarm in alarms:
        device, state = alarm.get("device"), alarm.get("status")
        if device and state == "ringing":
            outlines[device] = ALARM
        elif device and state == "silenced":
            outlines.setdefault(device, WARNING)
    return outlines


def with_nothing_fresh(status: dict[str, Any]) -> dict[str, Any]:
    """A copy of `status` with every pit and probe marked as not fresh."""
    return {
        **status,
        "pits": [{**pit, "fresh": False} for pit in status.get("pits", [])],
        "probes": [{**probe, "fresh": False} for probe in status.get("probes", [])],
    }


def draw_message(message: str) -> Image.Image:
    """A screen with just a message, e.g. while waiting for the monitor to start."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((WIDTH // 2, HEIGHT // 2), message, font=font(26), fill=DIM, anchor="mm")
    return image


def draw_top_bar(draw: ImageDraw.ImageDraw, status: dict[str, Any], now: datetime) -> int:
    """The clock on the left, and a dot per source on the right (green: working).

    In between, a warning if the monitor has stopped updating, or a note of
    any silenced alarms. Returns how far down the bar goes, so the tiles can
    start below it.
    """
    draw.text((16, 14), now.strftime("%H:%M"), font=font(24, bold=True), fill=TEXT)

    # Right to left, so the last source ends up nearest the edge.
    x = WIDTH - 16
    for source in reversed(status.get("sources", [])):
        name, colour = source["name"], GOOD if source.get("ok") else ALARM
        # The dot is drawn, not typed: a missing font would turn "●" into a box.
        draw.ellipse((x - DOT, 22, x, 22 + DOT), fill=colour)
        draw.text((x - DOT - 8, 16), name, font=font(18), fill=colour, anchor="ra")
        x -= int(draw.textlength(name, font=font(18))) + DOT + 28

    note = silenced_note(status)
    if note:
        middle = fit(draw, note, font(17, bold=True), x - 120)
        draw.text((110, 18), middle, font=font(17, bold=True), fill=WARNING)
    return TOP_BAR_HEIGHT


def silenced_note(status: dict[str, Any]) -> str | None:
    """e.g. "1 alarm silenced", for the top bar, or None."""
    silenced = sum(1 for alarm in status.get("alarms", []) if alarm.get("status") == "silenced")
    if silenced:
        return "1 alarm silenced" if silenced == 1 else f"{silenced} alarms silenced"
    return None


def status_age_seconds(status: dict[str, Any], now: datetime) -> float | None:
    """How long ago the monitor wrote this status, or None if it doesn't say."""
    try:
        written = datetime.fromisoformat(status["updated_at"])
        return (now - written).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def draw_alarm_bar(draw: ImageDraw.ImageDraw, ringing: list[dict[str, Any]]) -> int:
    """The red bar shown while alarms ring: what's wrong, and how to silence it."""
    box = (MARGIN, MARGIN, WIDTH - MARGIN, ALARM_BAR_HEIGHT - MARGIN)
    draw.rounded_rectangle(box, 12, fill=ALARM)
    button = (WIDTH - 200, MARGIN + 10, WIDTH - MARGIN - 10, ALARM_BAR_HEIGHT - MARGIN - 10)
    draw.rounded_rectangle(button, 10, fill=TEXT)
    centre = ((button[0] + button[2]) // 2, (button[1] + button[3]) // 2)
    draw.text(centre, "SILENCE", font=font(22, bold=True), fill=ALARM, anchor="mm")

    message = fit(draw, ringing[0]["message"], font(21, bold=True), button[0] - 40)
    draw.text((24, MARGIN + 10), message, font=font(21, bold=True), fill=TEXT)
    more = len(ringing) - 1
    hint = "Tap anywhere to silence"
    if more:
        hint = f"+{more} more · {hint.lower()}"
    draw.text((24, MARGIN + 38), hint, font=font(15), fill=TEXT)
    return ALARM_BAR_HEIGHT


def draw_stopped_bar(draw: ImageDraw.ImageDraw, age: float | None) -> int:
    """The amber bar shown when the monitor has stopped updating the status."""
    box = (MARGIN, MARGIN, WIDTH - MARGIN, ALARM_BAR_HEIGHT - MARGIN)
    draw.rounded_rectangle(box, 12, fill=WARNING)
    headline = "Monitor stopped: no alarms!"
    draw.text((24, MARGIN + 10), headline, font=font(21, bold=True), fill=BACKGROUND)
    when = "a while ago" if age is None else f"{age / 60:.0f} min ago"
    draw.text((24, MARGIN + 38), f"Last update {when}", font=font(15), fill=BACKGROUND)
    return ALARM_BAR_HEIGHT


def fit(draw: ImageDraw.ImageDraw, text: str, text_font: Font, width: float) -> str:
    """`text`, shortened with "…" if needed to fit in `width` pixels."""
    if draw.textlength(text, font=text_font) <= width:
        return text
    while text and draw.textlength(text + "…", font=text_font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


# ------------------------------------------------------------------ pits ---

# A tile's edges, in pixels: (left, top, right, bottom).
Box = tuple[int, int, int, int]


def draw_pits(
    draw: ImageDraw.ImageDraw, pits: list[dict[str, Any]], top: int, outlines: dict[str, Colour]
) -> int:
    """The pits side by side across the screen, starting at `top`.

    `outlines` gives the outline colour of any pit with an alarm. Returns how
    far down they go, so the probes can start below them.
    """
    if not pits:
        return top
    # Share the width equally: one pit fills it, two get half each.
    width = (WIDTH - 2 * MARGIN - GAP * (len(pits) - 1)) // len(pits)
    for number, pit in enumerate(pits):
        left = MARGIN + number * (width + GAP)
        box = (left, top, left + width, top + PIT_HEIGHT)
        draw_pit(draw, pit, box, outlines.get(pit["id"]))
    return top + PIT_HEIGHT + GAP


def draw_pit(
    draw: ImageDraw.ImageDraw, pit: dict[str, Any], box: Box, outline: Colour | None = None
) -> None:
    """One pit tile: name, source, big temperature, set temperature, and extras."""
    left, top, right, bottom = box
    draw_tile(draw, box, outline)
    fresh = pit.get("fresh", False)
    name_colour = TEXT if fresh else STALE
    source_width = draw.textlength(pit["source"], font=font(14))
    name = fit(draw, pit["name"], font(18, bold=True), right - left - source_width - 44)
    draw.text((left + 16, top + 12), name, font=font(18, bold=True), fill=name_colour)
    draw.text((right - 16, top + 14), pit["source"], font=font(14), fill=DIM, anchor="ra")

    if pit.get("is_on") is False:
        # A grill that is off isn't "missing": say what it is doing.
        mode = str(pit.get("mode") or "off").capitalize()
        draw.text((left + 16, top + 56), mode, font=font(40, bold=True), fill=DIM)
        return

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


def draw_tile(draw: ImageDraw.ImageDraw, box: Box, outline: Colour | None) -> None:
    """A tile's background, with an outline if it has an alarm."""
    if outline:
        draw.rounded_rectangle(box, RADIUS, fill=TILE, outline=outline, width=4)
    else:
        draw.rounded_rectangle(box, RADIUS, fill=TILE)


# ---------------------------------------------------------------- probes ---


def draw_probes(
    draw: ImageDraw.ImageDraw, probes: list[dict[str, Any]], top: int, outlines: dict[str, Colour]
) -> None:
    """The probes in a strip across the bottom, from `top` down to the edge.

    Probes with recent data come first; ones in the drawer go to the end.
    """
    if not probes:
        return
    in_order = sorted(probes, key=lambda probe: not probe.get("fresh", False))
    width = (WIDTH - 2 * MARGIN - GAP * (len(probes) - 1)) // len(probes)
    for number, probe in enumerate(in_order):
        left = MARGIN + number * (width + GAP)
        box = (left, top, left + width, HEIGHT - MARGIN)
        draw_probe(draw, probe, box, outlines.get(probe["id"]))


def draw_probe(
    draw: ImageDraw.ImageDraw, probe: dict[str, Any], box: Box, outline: Colour | None = None
) -> None:
    """One probe tile: rings and name, temperature, target, and progress towards it."""
    left, top, right, bottom = box
    draw_tile(draw, box, outline)
    fresh = probe.get("fresh", False)

    # A dot per ring colour, then the name.
    x = left + 14
    for ring in probe.get("rings", []):
        colour = RING_COLOURS.get(ring, DIM)
        draw.ellipse((x, top + 16, x + 14, top + 30), fill=colour)
        x += 18
    name = fit(draw, probe["name"], font(17, bold=True), right - x - 14)
    draw.text((x + 4, top + 12), name, font=font(17, bold=True), fill=TEXT if fresh else STALE)

    if not fresh:
        draw.text((left + 14, top + 46), "no recent data", font=font(17), fill=STALE)
        return

    temp, target = probe.get("core_c"), probe.get("target_c")
    draw.text((left + 14, top + 42), format_temp(temp, 1), font=font(44, bold=True), fill=TEXT)
    if target is None:
        draw.text((right - 14, bottom - 26), "no target", font=font(15), fill=DIM, anchor="rb")
        return
    draw.text(
        (right - 14, bottom - 26), f"target {target:.0f}°", font=font(17), fill=DIM, anchor="rb"
    )
    draw_progress(draw, (left + 14, bottom - 20, right - 14, bottom - 10), temp, target)


def draw_progress(draw: ImageDraw.ImageDraw, box: Box, temp: float | None, target: float) -> None:
    """A bar filling up as the probe's temperature rises towards its target."""
    left, top, right, bottom = box
    draw.rounded_rectangle(box, 5, fill=BAR_TRACK)
    if temp is None or target <= 0:
        return
    done = max(0.0, min(1.0, temp / target))
    if done > 0:
        draw.rounded_rectangle((left, top, left + int((right - left) * done), bottom), 5, fill=GOOD)
