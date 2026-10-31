# Web config page — decisions and build plan

A phone-friendly page served by the Pi for changing alarm settings without
editing `config.toml`. Mock-up: [`mockups/alarm-rules.html`](mockups/alarm-rules.html)
(open it in a browser; it is not connected to the Pi yet). Screenshot:
[`mockups/alarm-rules-review.png`](mockups/alarm-rules-review.png).

## Decisions (agreed)

| # | Decision |
|---|----------|
| 1 | **End cook clears Pi-set probe targets** back to the ETI value. A target set here belongs to the cook. |
| 2 | **No PIN**, but the page must **not be reachable from the internet**. It listens on the home network only. If remote access is wanted later, use a VPN such as Tailscale, never port forwarding. |
| 3 | **°F is an option** (°C/°F switch, display only; stored values stay in °C). |
| 4 | **Alarm rules is built first.** Per-probe targets, presets, End cook and a live view come later. |
| 5 | **Explicit Save / Discard**, not instant changes (safer for alarms; several edits can be reviewed). |
| 6 | **Only switching an alarm off asks for confirmation.** The safe choice ("Keep it on") is the default. |
| 7 | **An alarm that is off is always visible**: banner on the page, and later on the touchscreen. |
| 8 | **Ranges are enforced**: "gone quiet" 1–60 min, batteries and pellets 5–90 %, grill off-target 3–50 °C. |
| 9 | **Brand sections hide** when that brand isn't configured (no Traeger, no Traeger section). |
| 10 | `config.toml` stays for secrets and fixed settings and is **never written by the page**. |

## Architecture

- New small program `smoker web` (aiohttp), separate from the monitor and the
  screen. Three services on the Pi: monitor, screen, web.
- One HTML page plus a small API: `GET /api/status`, `GET`/`PUT /api/settings`,
  `POST /api/silence`, `POST /api/end-cook`.
- New `state/settings.json` holds changeable settings (atomic write).
  `config.toml [alarms]` become the defaults; settings.json overrides them.
- The monitor re-reads settings and applies them at the next poll, no restart.
- Per-device limits (later) are keyed by device id (`"{source}:{serial}"`) and
  go to `evaluate(..., custom=...)`, which already accepts them.
- The status file shows each limit's origin (`"eti"` or `"pi"`). Adding fields
  needs no schema bump.
- End cook is a request file the monitor watches (like the silence file). It
  clears Pi-set probe targets and forgets live probes.

## Build order (small PRs)

1. Settings store (`settings.py`) and the monitor re-reading it. Tests.
2. `smoker web` server and the Alarm rules page (from the mock-up), home network only.
3. Show "alarm off" on the touchscreen too.
4. Later: per-probe targets with presets (brisket 93, pork 95, chicken 74 °C),
   probe nicknames, End cook, live view, polling interval.
5. Then: systemd units for monitor, screen and web (start on boot), and move
   `status.json` to RAM (tmpfs) to spare the SD card.

## UX standards followed, and what is unverified

Followed: 44 pt touch targets, Settings-style grouped lists with switches and
steppers, Dynamic Type, light/dark, WCAG 2.2 AA contrast (text 4.5:1, controls
3:1), no colour-only meaning, inline error messages, reduced motion, safe-area
insets, thumb-reach Save bar.

**Not verified** (flag these when reviewing):
- Apple's design guidelines site was unreachable from the sandbox; figures are
  from knowledge and secondary summaries.
- WCAG 2.2 was checked via secondary sources only.
- IEC 60601-1-8 (medical alarms) is paywalled; only a summary was read. We
  borrow its principles; we do not claim compliance.
- Contrast ratios were chosen to pass but not run through a checker.
- Not tested on a real iPhone, with large text sizes, or with VoiceOver.

## Other open items (not web)

- **PR #41 (screen app)** is open: mark ready, squash-merge, delete the branch.
  Then deploy to the Pi per `docs/screen.md` (HyperPixel overlay, groups,
  `uv sync --extra pi`, `smoker run` + `smoker screen`, `--rotate 270` if upside down).
- Run `uv lock` on the Pi and commit `uv.lock`.
- Traeger: need a **cooking capture** (`uv run python scripts/capture_traeger.py
  cooking --seconds 120`, send the redacted file only) to confirm lit-mode codes
  and plugged-in probe format. Then consider a "grill reports an error" alarm.
- Later: speaker and alarm sound, night dimming for the screen.
