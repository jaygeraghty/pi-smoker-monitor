"""Command-line entry point: the `smoker` command.

Commands:
    smoker snapshot   Print current readings and any active alarms
    smoker run        Run the monitor: poll, check alarms, ring until silenced
    smoker silence    Silence whatever a running monitor has ringing

Exit codes for `smoker snapshot`:
    0  all fine
    1  couldn't run (config problem, or ETI Cloud unreachable)
    2  readings fetched, and at least one alarm is active

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from smoker_monitor import __version__
from smoker_monitor.config import ConfigError, load_config
from smoker_monitor.domain.alarms import evaluate
from smoker_monitor.notifiers.console import ConsoleNotifier
from smoker_monitor.report import format_snapshot
from smoker_monitor.service.monitor import Monitor, request_silence
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.eti_cloud import EtiCloudSource

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

    source = EtiCloudSource(config.eti_cloud)
    try:
        snapshot = asyncio.run(source.fetch())
    except SourceError as e:
        print(f"Couldn't get readings: {e}", file=sys.stderr)
        return EXIT_FAILED

    now = datetime.now(UTC)
    alarms = evaluate(snapshot, now, config.alarms)
    print(format_snapshot(snapshot, now, alarms, config.alarms))
    return EXIT_ALARMS if alarms else EXIT_OK


def handle_run(config_path: Path, state_dir: Path) -> int:
    """Run the monitor until Ctrl+C. Returns an exit code."""
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return EXIT_FAILED

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    monitor = Monitor(
        source=EtiCloudSource(config.eti_cloud),
        rules=config.alarms,
        notifier=ConsoleNotifier(),
        state_dir=state_dir,
    )
    print(f"Monitoring every {config.polling.interval_seconds} s. Ctrl+C to stop.")
    try:
        asyncio.run(monitor.run_forever(config.polling.interval_seconds))
    except KeyboardInterrupt:
        print("Stopped.")
    return EXIT_OK


def handle_silence(state_dir: Path) -> int:
    """Ask a running monitor to silence its alarms."""
    request_silence(state_dir)
    print("Silence requested: a running monitor will go quiet within a second or two.")
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
    parser.print_help()
    return EXIT_OK
