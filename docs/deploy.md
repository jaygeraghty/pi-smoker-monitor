# Running it on the Pi, all the time

Three services start when the Pi boots and are restarted if they fail:

| Service | What it does |
| --- | --- |
| `smoker-monitor` | Polls ETI/Traeger, checks the alarms, writes the status |
| `smoker-screen` | Shows the status on the touchscreen |
| `smoker-web` | The settings page for your phone |

## Install (once, and again after updates)

```
cd ~/pi-smoker-monitor
git pull
sudo apt install -y fonts-dejavu-core     # nicer screen text
sudo ./deploy/install-services.sh
```

The script fills in your user name and folder in the unit files, installs them,
and starts them. Stop any copies you started by hand first (Ctrl+C in Termius).

Check: `systemctl status smoker-monitor smoker-screen smoker-web`
Log: `journalctl -u smoker-monitor -f`

## What keeps it going

- **Crash:** `Restart=always`, after 5 seconds, with no limit on retries.
- **Stuck:** the monitor tells systemd it is alive every few seconds
  (`Type=notify`, `WatchdogSec=120`). Silent for 2 minutes, it is restarted.
- **Same folder everywhere:** once the services are installed, `smoker silence`, `screen` and `web` use `/run/smoker` by default, so they reach the service's monitor.
- **No data:** if the status file is older than 2 minutes the screen and the
  web page show "Monitor stopped: no alarms!".

## Status in memory

The status file is rewritten every poll, so it lives in `/run/smoker` (memory)
rather than on the SD card. It is rebuilt within a poll of every boot. Settings
(`state/settings.json`) stay on the card because they must survive a reboot.

## Not covered

Nothing here alerts you if the Pi itself dies or loses power, and no alarm makes
a sound yet (only the screen and log). Treat it as a supervised tool for now.
