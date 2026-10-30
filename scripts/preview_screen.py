"""Draw the screen into a picture file, so you can see it without the screen.

    uv run python scripts/preview_screen.py                     # sample readings
    uv run python scripts/preview_screen.py state/status.json   # the real thing

Saves screen.png in the project folder (git-ignored).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from smoker_monitor.screen.draw import draw_screen

SAMPLE = Path("tests/fixtures/status_sample.json")
OUTPUT = Path("screen.png")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
    status = json.loads(path.read_text(encoding="utf-8"))
    draw_screen(status, datetime.now().astimezone()).save(OUTPUT)
    print(f"Drew {path} into {OUTPUT}")


if __name__ == "__main__":
    main()
