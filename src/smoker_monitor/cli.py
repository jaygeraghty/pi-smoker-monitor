"""Command-line entry point: the `smoker` command.

Commands:
    smoker snapshot   Print current pit, fan and probe readings (milestone 1)
    smoker run        Run the monitor service (milestone 3, not yet built)

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from smoker_monitor import __version__
from smoker_monitor.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    snapshot = commands.add_parser("snapshot", help="Print current pit, fan and probe readings")
    snapshot.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config file (default: config.toml)",
    )
    return parser


def handle_snapshot(config_path: Path) -> int:
    """Load config and report it. Will print live readings once the ETI source exists."""
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return 1
    print(
        f"Config OK: polling every {config.polling.interval_seconds}s as {config.eti_cloud.email}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        return handle_snapshot(args.config)
    parser.print_help()
    return 0
