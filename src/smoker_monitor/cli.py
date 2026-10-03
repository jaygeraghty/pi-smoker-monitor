"""Command-line entry point: the `smoker` command.

Commands:
    smoker snapshot   Print current pit, fan and probe readings (milestone 1)
    smoker run        Run the monitor service (milestone 3, not yet built)

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smoker_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("snapshot", help="Print current pit, fan and probe readings")
    return parser


def handle_snapshot() -> None:
    """Print the current readings. Placeholder until the ETI Cloud source exists."""
    print("snapshot!")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        handle_snapshot()
    else:
        parser.print_help()
    return 0
