"""Tests for the screen: drawing, the framebuffer, taps, and the app loop.

Drawing is checked by size and by the colour of a few telling pixels (the
alarm bar is red, the stopped bar amber), using the sample status file.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw

from smoker_monitor.cli import main
from smoker_monitor.screen import touch
from smoker_monitor.screen.app import ScreenApp, load_status
from smoker_monitor.screen.draw import (
    alarm_outlines,
    draw_message,
    draw_screen,
    fit,
    status_age_seconds,
)
from smoker_monitor.screen.framebuffer import (
    Framebuffer,
    ScreenInfo,
    read_screen_info,
    rgb565,
    to_pixels,
)
from smoker_monitor.screen.theme import ALARM, BACKGROUND, HEIGHT, WARNING, WIDTH, font
from smoker_monitor.service.monitor import SILENCE_FILE
from smoker_monitor.service.status import STATUS_FILE

SAMPLE = Path(__file__).parent / "fixtures" / "status_sample.json"


def sample() -> dict[str, Any]:
    status: dict[str, Any] = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return status


def written_at(status: dict[str, Any], seconds_later: float = 20) -> datetime:
    """A moment `seconds_later` than the status was written."""
    return datetime.fromisoformat(status["updated_at"]) + timedelta(seconds=seconds_later)


def ringing(status: dict[str, Any]) -> dict[str, Any]:
    """The sample, with the Gateway's pit too cold and ringing."""
    status = copy.deepcopy(status)
    status["alarms"] = [
        {
            "kind": "pit_low",
            "device": "eti:GATEWAY-1",
            "source": None,
            "status": "ringing",
            "value": 92.0,
            "limit": 96.1,
            "since": status["updated_at"],
            "message": "Pit too cold: 92.0°C (limit 96.1°C)",
        }
    ]
    return status


# ----------------------------------------------------------------- drawing ---


def test_calm_screen() -> None:
    status = sample()
    image = draw_screen(status, written_at(status))
    assert image.size == (WIDTH, HEIGHT)
    assert image.getpixel((400, 20)) == BACKGROUND  # the top bar, no warning in it


def test_ringing_alarm_shows_the_red_bar() -> None:
    status = ringing(sample())
    image = draw_screen(status, written_at(status))
    assert image.getpixel((14, 40)) == ALARM


def test_stopped_monitor_shows_the_amber_bar() -> None:
    """Six minutes without an update: nothing on screen can be trusted."""
    status = ringing(sample())
    image = draw_screen(status, written_at(status, 6 * 60))
    assert image.getpixel((14, 40)) == WARNING


def test_every_kind_of_status_draws() -> None:
    """Missing and odd values mustn't crash the drawing."""
    status = sample()
    status["pits"][0].update(temp_c=None, set_c=None, high_c=None, low_c=None, fan=None)
    status["probes"][0].update(core_c=None)
    status["probes"][1].update(target_c=None)
    status["sources"][1].update(ok=False, error="down")
    status["pits"][1]["name"] = "A very long grill name indeed, far too long"
    draw_screen(status, written_at(status))
    draw_screen({**status, "pits": [], "probes": []}, written_at(status))
    draw_screen({}, written_at(status))  # an empty file: "monitor stopped"


def test_alarm_outlines() -> None:
    alarms: list[dict[str, Any]] = [
        {"device": "eti:G1", "status": "ringing"},
        {"device": "eti:P1", "status": "silenced"},
        {"device": "eti:P1", "status": "ringing"},  # ringing wins
        {"device": None, "status": "ringing"},  # about a whole source
        {"device": "eti:P2", "status": "pending"},
    ]
    assert alarm_outlines(alarms) == {"eti:G1": ALARM, "eti:P1": ALARM}


def test_status_age() -> None:
    status = sample()
    assert status_age_seconds(status, written_at(status, 90)) == 90
    assert status_age_seconds({}, written_at(status)) is None


def test_fit_shortens_long_text() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert fit(draw, "brisket", font(17), 500) == "brisket"
    short = fit(draw, "a very long probe name indeed", font(17), 100)
    assert short.endswith("…")
    assert draw.textlength(short, font=font(17)) <= 100


def test_message_screen() -> None:
    assert draw_message("Waiting").size == (WIDTH, HEIGHT)


# ------------------------------------------------------------- framebuffer ---


def fake_sys(tmp_path: Path, size: str = "480,800", bits: int = 16, stride: int = 960) -> Path:
    folder = tmp_path / "sys" / "fb0"
    folder.mkdir(parents=True)
    (folder / "virtual_size").write_text(size + "\n")
    (folder / "bits_per_pixel").write_text(f"{bits}\n")
    (folder / "stride").write_text(f"{stride}\n")
    return folder.parent


def test_read_screen_info(tmp_path: Path) -> None:
    sys_graphics = fake_sys(tmp_path, "480,1600")  # double height: double buffered
    (sys_graphics / "fb0" / "modes").write_text("U:480x800p-0\n")
    assert read_screen_info(Path("/dev/fb0"), sys_graphics) == ScreenInfo(480, 800, 16, 960)


def test_rgb565() -> None:
    image = Image.new("RGB", (4, 1))
    for x, colour in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        image.putpixel((x, 0), colour)
    assert rgb565(image) == bytes.fromhex("00f8e0071f00ffff")


def test_32_bit_pixels_are_blue_green_red() -> None:
    image = Image.new("RGB", (1, 1), (10, 20, 30))
    assert to_pixels(image, ScreenInfo(1, 1, 32, 4)) == bytes([30, 20, 10, 0])


def test_rows_are_padded_to_the_stride() -> None:
    image = Image.new("RGB", (2, 2), (255, 255, 255))
    assert to_pixels(image, ScreenInfo(2, 2, 16, 6)) == bytes.fromhex("ffffffff0000" * 2)


def test_wrong_size_is_refused() -> None:
    with pytest.raises(ValueError, match="screen is"):
        to_pixels(Image.new("RGB", (800, 480)), ScreenInfo(480, 800, 16, 960))


def test_framebuffer_turns_the_picture_for_a_portrait_screen(tmp_path: Path) -> None:
    device = tmp_path / "fb0"
    device.write_bytes(bytes(480 * 800 * 2))
    screen = Framebuffer(device, 90, fake_sys(tmp_path))
    landscape = Image.new("RGB", (WIDTH, HEIGHT), (255, 0, 0))
    landscape.putpixel((0, 0), (0, 0, 255))  # top-left corner blue
    screen.show(landscape)
    data = device.read_bytes()
    assert len(data) == 480 * 800 * 2
    # Turned a quarter anticlockwise, the top-left corner ends up bottom-left.
    bottom_left = (800 - 1) * 960
    assert data[bottom_left : bottom_left + 2] == bytes.fromhex("1f00")
    assert data[0:2] == bytes.fromhex("00f8")


# ------------------------------------------------------------------- touch ---

ECODES = SimpleNamespace(EV_KEY=1, EV_ABS=3, BTN_TOUCH=330)


def test_only_a_finger_landing_counts_as_a_tap() -> None:
    assert touch.is_touch_down(1, 330, 1, ECODES)
    assert not touch.is_touch_down(1, 330, 0, ECODES)  # lifting off
    assert not touch.is_touch_down(3, 0, 123, ECODES)  # moving


async def test_without_evdev_taps_are_just_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "evdev", None)  # importing it now fails
    taps: list[int] = []
    await touch.watch_taps(lambda: taps.append(1))  # returns instead of crashing
    assert taps == []


# --------------------------------------------------------------------- app ---


class FakeDisplay:
    def __init__(self) -> None:
        self.shown: list[Image.Image] = []

    def show(self, image: Image.Image) -> None:
        self.shown.append(image)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_app(tmp_path: Path) -> tuple[ScreenApp, FakeDisplay, Clock]:
    status = sample()
    display = FakeDisplay()
    clock = Clock(written_at(status))
    return ScreenApp(tmp_path, display, clock), display, clock


def write(tmp_path: Path, status: dict[str, Any]) -> None:
    (tmp_path / STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")


def test_waits_for_the_monitor(tmp_path: Path) -> None:
    app, display, _ = make_app(tmp_path)
    assert app.refresh()
    assert app.status is None
    assert len(display.shown) == 1


def test_redraws_only_when_something_changes(tmp_path: Path) -> None:
    app, display, clock = make_app(tmp_path)
    write(tmp_path, sample())
    assert app.refresh()
    assert not app.refresh()  # same file, same minute
    clock.now += timedelta(minutes=1)
    assert app.refresh()  # the clock moved on
    write(tmp_path, ringing(sample()))
    assert app.refresh()  # new status
    assert len(display.shown) == 3


def test_tap_silences_a_ringing_alarm(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    write(tmp_path, ringing(sample()))
    app.refresh()
    app.tap()
    assert (tmp_path / SILENCE_FILE).exists()


def test_tap_without_an_alarm_does_nothing(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    write(tmp_path, sample())
    app.refresh()
    app.tap()
    assert not (tmp_path / SILENCE_FILE).exists()


def test_broken_status_file_counts_as_missing(tmp_path: Path) -> None:
    (tmp_path / STATUS_FILE).write_text("{not json", encoding="utf-8")
    assert load_status(tmp_path / STATUS_FILE) is None


def test_screen_command_without_a_screen_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["screen", "--framebuffer", str(tmp_path / "fb9"), "--state-dir", str(tmp_path)])
    assert code == 1
    assert "Can't use the screen" in capsys.readouterr().err
