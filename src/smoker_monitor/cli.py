"""Command-line entry point: the `smoker` command.

Commands:
    smoker snapshot   Print current readings and any active alarms
    smoker run        Run the monitor: poll, check alarms, ring until silenced
    smoker silence    Silence whatever a running monitor has ringing
    smoker screen     Show the running monitor's status on the Pi's screen

Exit codes for `smoker snapshot`:
    0  all fine
    1  couldn't run (config problem, or no source could be reached)
    2  readings fetched, and at least one alarm is active

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from smoker_monitor import __version__
from smoker_monitor.config import ConfigError, load_config
from smoker_monitor.domain.alarms import evaluate
from smoker_monitor.domain.models import Snapshot, SourceHealth
from smoker_monitor.notifiers.console import ConsoleNotifier
from smoker_monitor.report import format_snapshot
from smoker_monitor.service.combine import combine
from smoker_monitor.service.monitor import Monitor, request_silence
from smoker_monitor.sources.base import Source, SourceError
from smoker_monitor.sources.factory import build_sources

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALARMS = 2

# Where the running monitor keeps its status file and silence requests.
DEFAULT_STATE_DIR = Path("state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    snapshot = commands.add_parser(
        "snapshot", help="Print current readings and any active alarms (exit code 2 if any)"
    )
    add_config_option(snapshot)

    run = commands.add_parser("run", help="Run the monitor until stopped with Ctrl+C")
    add_config_option(run)
    add_state_dir_option(run)

    silence = commands.add_parser("silence", help="Silence a running monitor's alarms")
    add_state_dir_option(silence)

    screen = commands.add_parser("screen", help="Show the monitor's status on the Pi's screen")
    add_state_dir_option(screen)
    screen.add_argument(
        "--framebuffer",
        type=Path,
        default=Path("/dev/fb0"),
        help="The screen's framebuffer device (default: /dev/fb0)",
    )
    screen.add_argument(
        "--rotate",
        type=int,
        choices=(90, 270),
        default=90,
        help="Turn the picture this many degrees on a portrait screen (default: 90)",
    )
    return parser


def add_config_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config file (default: config.toml)",
    )


def add_state_dir_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Folder for the status file and silence requests (default: state)",
    )


def handle_snapshot(config_path: Path) -> int:
    """Fetch the latest readings, check them against the alarm rules, and print both.

    A one-off snapshot has no memory of earlier polls, so it can't tell that a
    probe has gone quiet mid-cook; that alarm needs the long-running service.
    Every other alarm is checked. Returns an exit code (see the module docstring).
    """
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return EXIT_FAILED

    now = datetime.now(UTC)
    snapshot = asyncio.run(fetch_all(build_sources(config), now))
    for source in snapshot.sources:
        if source.error is not None:
            print(f"Couldn't get readings from {source.name}: {source.error}", file=sys.stderr)
    if all(source.error is not None for source in snapshot.sources):
        return EXIT_FAILED

    alarms = evaluate(snapshot, now, config.alarms)
    print(format_snapshot(snapshot, now, alarms, config.alarms))
    return EXIT_ALARMS if alarms else EXIT_OK


async def fetch_all(sources: Sequence[Source], now: datetime) -> Snapshot:
    """Fetch from every source at once, close them, and combine the results.

    A source that fails adds no devices; its error is in the Snapshot's sources.
    """
    results = await asyncio.gather(*(fetch_once(source) for source in sources))
    parts: list[tuple[SourceHealth, Snapshot | None]] = []
    for source, result in zip(sources, results, strict=True):
        health = SourceHealth(source.key, source.name, source.expects_gateway)
        if isinstance(result, SourceError):
            health = replace(health, error=str(result), failing_since=now)
            parts.append((health, None))
        else:
            parts.append((health, result))
    return combine(now, parts)


async def fetch_once(source: Source) -> Snapshot | SourceError:
    """Fetch one Snapshot (or the error saying why not), then close the source."""
    try:
        return await source.fetch()
    except SourceError as e:
        return e
    finally:
        await source.close()


def handle_run(config_path: Path, state_dir: Path) -> int:
    """Run the monitor until Ctrl+C. Returns an exit code."""
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return EXIT_FAILED

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sources = build_sources(config)
    monitor = Monitor(
        sources=sources,
        rules=config.alarms,
        notifier=ConsoleNotifier(),
        state_dir=state_dir,
    )
    watching = ", ".join(f"{s.name} every {s.poll_seconds} s" for s in sources)
    print(f"Monitoring {watching}. Ctrl+C to stop.")
    try:
        asyncio.run(monitor.run_forever())
    except KeyboardInterrupt:
        print("Stopped.")
    return EXIT_OK


def handle_silence(state_dir: Path) -> int:
    """Ask a running monitor to silence its alarms."""
    request_silence(state_dir)
    print("Silence requested: a running monitor will go quiet within a second or two.")
    return EXIT_OK


def handle_screen(state_dir: Path, framebuffer: Path, rotate: int) -> int:
    """Keep the Pi's screen showing the status, until Ctrl+C. Returns an exit code."""
    # Imported here so the other commands don't need the screen's libraries.
    from smoker_monitor.screen.app import ScreenApp
    from smoker_monitor.screen.framebuffer import Framebuffer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        display = Framebuffer(framebuffer, rotate)
    except OSError as e:
        print(f"Can't use the screen at {framebuffer}: {e}", file=sys.stderr)
        return EXIT_FAILED
    print(f"Showing {state_dir / 'status.json'} on {framebuffer}. Ctrl+C to stop.")
    try:
        asyncio.run(ScreenApp(state_dir, display).run())
    except KeyboardInterrupt:
        print("Stopped.")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        return handle_snapshot(args.config)
    if args.command == "run":
        return handle_run(args.config, args.state_dir)
    if args.command == "silence":
        return handle_silence(args.state_dir)
    if args.command == "screen":
        return handle_screen(args.state_dir, args.framebuffer, args.rotate)
    parser.print_help()
    return EXIT_OK
