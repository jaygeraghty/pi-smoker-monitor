# The screen

`smoker screen` shows the monitor's status on the Pi's touchscreen: the pits
across the top, the probes along the bottom, and a red bar with SILENCE when
an alarm rings. While an alarm rings, tap anywhere on the screen to silence it.

It's a separate program from the monitor (`smoker run`), on purpose: if the
screen ever breaks, the alarms keep working. The screen only reads
`state/status.json`, which the monitor writes every poll. If that file stops
changing for 2 minutes, the screen says **"Monitor stopped: no alarms!"** in
amber and greys out every reading.

It draws straight to the Linux framebuffer (`/dev/fb0`), so it suits
Raspberry Pi OS **Lite** (no desktop). With a desktop running, the desktop
owns the screen and the two will fight over it.

## Setting up the Pi (once)

These steps are for a Pimoroni **HyperPixel 4.0** (rectangular). Other
screens work too, as long as they give you a `/dev/fb0`.

1. **Turn the screen on.** If you haven't already, add this line to the
   bottom of `/boot/firmware/config.txt` (`sudo nano /boot/firmware/config.txt`),
   then `sudo reboot`:

   ```
   dtoverlay=vc4-kms-dpi-hyperpixel4
   ```

   Check it's there: `cat /sys/class/graphics/fb0/virtual_size` should say
   `480,800` (the HyperPixel is portrait by nature; the app turns its
   picture to suit).

2. **Let your user draw and read taps** (then log out and back in):

   ```
   sudo usermod -aG video,input $USER
   ```

3. **Stop the text console drawing over it.** The login prompt and a
   blinking cursor live on the same screen. Stop the login prompt there:

   ```
   sudo systemctl disable --now getty@tty1
   ```

   and hide the cursor, by adding this to the end of the single line in
   `/boot/firmware/cmdline.txt` (same line, a space before it), then reboot:

   ```
   vt.global_cursor_default=0 consoleblank=0
   ```

   (`consoleblank=0` stops the screen going blank after 10 minutes.)

4. **Install the touch library:**

   ```
   uv sync --extra pi
   ```

   If that fails building `evdev`, install the build tools and try again:
   `sudo apt install build-essential python3-dev`.

## Running it

In one Termius session, the monitor:

```
uv run smoker run
```

In another, the screen:

```
uv run smoker screen
```

Options:
- `--rotate 270` if the picture comes out upside down (default 90).
- `--state-dir PATH` if the monitor uses a different state folder.
- `--framebuffer /dev/fb1` if the screen isn't the first framebuffer.

Starting both automatically when the Pi boots is the next job.

## Seeing it without the screen

`scripts/preview_screen.py` draws the same picture into `screen.png`:

```
uv run python scripts/preview_screen.py                     # sample readings
uv run python scripts/preview_screen.py state/status.json   # the real thing
```

## How it's built

| File | Job |
| --- | --- |
| `screen/theme.py` | Sizes, colours and fonts, in one place |
| `screen/draw.py` | Status in, picture out: pure, so easy to test and preview |
| `screen/framebuffer.py` | Puts the picture on `/dev/fb0` (16 or 32-bit, turned for portrait) |
| `screen/touch.py` | Notices a finger landing on the touchscreen (`evdev`) |
| `screen/app.py` | The loop: redraw when the status or the minute changes; tap to silence |
