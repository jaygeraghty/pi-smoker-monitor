from __future__ import annotations

import argparse
from collections.abc import Sequence

from smoker_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    # Step 1: create the parser (the "menu")
    parser = argparse.ArgumentParser(prog="smoker", description="BBQ smoker monitor and alarm")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Step 2: describe what's allowed.
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    # CHANGE 2: ...so we can add "snapshot" to it (add_parser, not add_argument)
    commands.add_parser("snapshot", help="Print current pit, fan and probe readings")
    return parser


def handle_snapshot() -> None:
    # The actual job. Later this fetches from ETI Cloud; for now, prove the wiring works.
    print("snapshot!")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # Step 3: read what the user typed
    if args.command == "snapshot":
        handle_snapshot()
    else:
        parser.print_help()
    return 0
