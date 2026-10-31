# The settings page (`smoker web`)

A page for your phone, served by the Pi, for changing alarms without editing
`config.toml`. Decisions and reasoning: [web-config.md](web-config.md).

## Running it

```bash
uv run smoker web            # then open http://<the Pi's name>.local:8080
```

It prints the address. Options: `--port 8080`, `--state-dir state`,
`--config config.toml` (read only for your alarm defaults and which brands you
have; the page never sees or shows a password), `--allow-host NAME` for another
name you reach the Pi by.

It must use the **same `--state-dir` as `smoker run`**: that is how the two
programs talk (the monitor writes `status.json`; the page writes
`settings.json`). On an iPhone, Share → Add to Home Screen makes it look like an app.

## The three pages

| Page | What it does |
| --- | --- |
| **Now** | Readings for every pit and probe, anything ringing (with a Silence button), and **End cook**. Updates every 5 seconds. |
| **Probes** | For each probe and pit: a name, and the alarm limits. Each limit is **ETI app** (the default), **Set here** (your own number, with quick picks Brisket 93, Pork shoulder 95, Chicken 74 °C), or **Off**. |
| **Alarm rules** | Which alarms are on and their thresholds (how long before "gone quiet" rings, battery and pellet levels, how far a Traeger may stray), plus °C/°F. Sections for brands you don't have are hidden. |

Changes on Probes and Alarm rules are saved together with **Save** (or thrown away
with **Discard**). The monitor picks them up within a second: alarms you switch
off stop at once, without waiting for the next poll.

**End cook** clears the limits you set here (names and rules stay) and makes the
monitor stop waiting for probes you've taken out.

## How it fits together

```
phone ──► smoker web ──writes──► state/settings.json ──read by──► smoker run (monitor)
              ▲                                                        │
              └────────reads──── state/status.json ◄───writes──────────┘
              └────────asks────► state/silence, state/end-cook (request files)
```

- `settings.json` holds only what you changed from `config.toml`'s `[alarms]`
  values. The monitor never writes it, and the page is the only writer. Each save
  bumps a `revision`; a second phone saving from an old copy is told so instead of
  silently overwriting (see `service/settings.py`).
- Your own limits replace the ETI app's for that probe, side by side
  (target and too-cold are independent). `status.json` says which is in force
  (`target_origin`/`low_origin`: `"eti"` or `"pi"`), so the screen shows the right
  target.
- A nickname is applied to the readings before anything else, so the screen,
  the log and the alarm message all use it.
- A damaged `settings.json` can't stop alarms: the monitor logs it and keeps
  the last good settings. The page says so and tells you to delete the file.

## What it will and won't let in

There is **no PIN**, so protection is in where requests may come from
(`web/security.py`, tested in `tests/web/`):

- Only home-network addresses: this Pi, 192.168.x.x, 10.x.x.x, 172.16–31.x.x,
  link-local, and Tailscale (100.64.0.0/10). Anything else gets 403, even if a
  router port is forwarded by mistake.
- Only home names in the address bar (an IP address, `localhost`, a single word
  such as `raspberrypi`, or `.local`, `.lan`, `.home.arpa`, `.ts.net`). This
  blocks "DNS rebinding", where a website tricks your browser into talking to
  your Pi.
- Changes must be JSON sent from the page itself, so a web page on another site
  can't switch your alarms off while your phone is on the same Wi-Fi.
- A strict content policy: only the page's own scripts and styles run.

**Don't forward a port to the Pi.** For use away from home, use a VPN such as
Tailscale. Anyone on your home Wi-Fi can use the page, because there is no PIN.

## The files (everything for the page is in `src/smoker_monitor/web/`)

| File | What |
| --- | --- |
| `server.py` | The small web server (Python's standard library: nothing extra to install) |
| `api.py` | What each address does; no networking, so it is fully tested without a server |
| `security.py` | Who may ask |
| `static/` | The page: `index.html`, `app.css`, `app.js`, icons |

The only things shared with the rest of the program are `service/settings.py`
(the settings file format, which the monitor also reads) and the status file.

## Design standards the page follows

- **Apple's iOS guidance:** 44 pt touch targets, grouped lists, switches and
  steppers, text size follows the iPhone's setting (Dynamic Type), light and dark,
  safe-area insets, Save bar in thumb reach, Add to Home Screen.
- **WCAG 2.2 AA:** text contrast 4.5:1 and control contrast 3:1 (all checked by
  script, light and dark), nothing shown by colour alone, visible keyboard focus,
  errors next to the field saying how to fix them, no sideways scrolling at
  320 px width with text at 200 %, reduced-motion respected, forced-colours mode.
- **Alarm safety (principles from IEC 60601-1-8, not a claim of compliance):**
  only switching an alarm *off* asks first, with the safe choice as the default;
  an alarm that is off is always shown in a banner and in words; values are kept
  inside sensible ranges; one tap resets to recommended; the monitor being
  stopped is shown at the top of every page.

Not verified: Apple's own site and the paid IEC standard could not be read from
the build environment; the page hasn't been tried on a real iPhone or with
VoiceOver. Please try both, and report anything awkward.

## Limits

- `smoker snapshot` still uses `config.toml` only (it is a one-off check).
- The Pi screen doesn't yet show the "alarms off" warning (`alarms_off` is in
  the status file, ready for it).
- A probe's own ETI limit is only shown while the ETI app's value is in force;
  after you choose "Set here" the page remembers it only until reloaded.
