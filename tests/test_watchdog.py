"""systemd watchdog messages, and the deploy files that rely on them."""

from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

import pytest

from smoker_monitor.service.watchdog import PING_EVERY_SECONDS, NoWatchdog, SystemdWatchdog

DEPLOY = Path(__file__).parent.parent / "deploy"


@pytest.fixture
def listener(tmp_path: Path):
    path = tmp_path / "notify.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    sock.settimeout(1)
    yield sock, str(path)
    sock.close()


def test_ready_and_ping_reach_systemd(listener: tuple[socket.socket, str]) -> None:
    sock, path = listener
    watchdog = SystemdWatchdog(path)
    watchdog.ready()
    watchdog.ping()
    assert sock.recv(64) == b"READY=1"
    assert sock.recv(64) == b"WATCHDOG=1"


def test_pings_are_throttled(listener: tuple[socket.socket, str]) -> None:
    sock, path = listener
    now = [100.0]
    watchdog = SystemdWatchdog(path, clock=lambda: now[0])
    watchdog.ping()
    now[0] += PING_EVERY_SECONDS - 1
    watchdog.ping()  # too soon: not sent
    now[0] += 2
    watchdog.ping()
    assert sock.recv(64) == b"WATCHDOG=1"
    assert sock.recv(64) == b"WATCHDOG=1"
    sock.settimeout(0.1)
    with pytest.raises(TimeoutError):
        sock.recv(64)


def test_does_nothing_outside_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    watchdog = SystemdWatchdog()
    assert not watchdog.active
    watchdog.ready()
    watchdog.ping()
    NoWatchdog().ping()


def test_a_dead_socket_never_troubles_the_monitor(tmp_path: Path) -> None:
    watchdog = SystemdWatchdog(str(tmp_path / "nobody-home.sock"))
    watchdog.ready()
    watchdog.ping()  # no exception


def test_units_use_the_watchdog_and_memory_folder() -> None:
    monitor = (DEPLOY / "systemd" / "smoker-monitor.service").read_text()
    assert "Type=notify" in monitor
    assert re.search(r"^WatchdogSec=\d+", monitor, re.MULTILINE)
    for name in ("monitor", "screen", "web"):
        unit = (DEPLOY / "systemd" / f"smoker-{name}.service").read_text()
        assert "--run-dir /run/smoker" in unit
        assert "Restart=always" in unit
        assert "__USER__" in unit
        assert "__DIR__" in unit


def test_installer_is_valid_shell() -> None:
    script = DEPLOY / "install-services.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
