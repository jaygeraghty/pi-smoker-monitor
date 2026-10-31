#!/usr/bin/env bash
# Install the monitor, screen and web page as services that start on boot.
# Run on the Pi, from anywhere:  sudo ./deploy/install-services.sh
# Safe to run again (after a git pull, say): it just reinstalls and restarts them.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo:  sudo $0" >&2
    exit 1
fi

user="${SUDO_USER:-}"
if [[ -z "$user" || "$user" == root ]]; then
    echo "Run it with sudo from your normal login, not as root, so the services run as you." >&2
    exit 1
fi

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for needed in "$dir/.venv/bin/smoker" "$dir/config.toml"; do
    if [[ ! -e "$needed" ]]; then
        echo "Missing $needed: set the project up first (see the README)." >&2
        exit 1
    fi
done
sudo -u "$user" mkdir -p "$dir/state"

services=(smoker-monitor smoker-screen smoker-web)
for name in "${services[@]}"; do
    sed -e "s|__USER__|$user|g" -e "s|__DIR__|$dir|g" \
        "$dir/deploy/systemd/$name.service" > "/etc/systemd/system/$name.service"
done

# Replace the old single-service unit from earlier versions, if it is there.
if [[ -e /etc/systemd/system/pi-smoker-monitor.service ]]; then
    systemctl disable --now pi-smoker-monitor 2>/dev/null || true
    rm -f /etc/systemd/system/pi-smoker-monitor.service
fi

systemctl daemon-reload
systemctl enable "${services[@]}"
systemctl restart "${services[@]}"

echo
echo "Installed and started: ${services[*]}"
echo "They will start by themselves whenever the Pi boots."
echo "Check:  systemctl status smoker-monitor smoker-screen smoker-web"
echo "Log:    journalctl -u smoker-monitor -f"
