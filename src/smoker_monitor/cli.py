"""Command-line entry point: the `smoker` command.

Planned commands:
    smoker snapshot   Print current pit, fan and probe readings (milestone 1)
    smoker run        Run the monitor service: poll, evaluate alarms, notify (milestone 3)

Keep this module thin: parse arguments, load config, call into the package.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smoker_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_subparsers(dest="command", metavar="<command>")
    # TODO(milestone 1): add the `snapshot` subcommand.
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0
