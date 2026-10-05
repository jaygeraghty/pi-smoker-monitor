"""Command-line entry point: the `smoker` command.

Commands:
    smoker snapshot   Print current readings and any active alarms
    smoker run        Run the monitor service (milestone 3, not yet built)

Exit codes for `smoker snapshot`:
    0  all fine
    1  couldn't run (config problem, or ETI Cloud unreachable)
    2  readings fetched, and at least one alarm is active

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from smoker_monitor import __version__
from smoker_monitor.config import ConfigError, load_config
from smoker_monitor.domain.alarms import evaluate
from smoker_monitor.report import format_snapshot
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.eti_cloud import EtiCloudSource

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALARMS = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    snapshot = commands.add_parser(
        "snapshot", help="Print current readings and any active alarms (exit code 2 if any)"
    )
    snapshot.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config file (default: config.toml)",
    )
    return parser


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
    print(format_snapshot(snapshot, now, alarms))
    return EXIT_ALARMS if alarms else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        return handle_snapshot(args.config)
    parser.print_help()
    return EXIT_OK
