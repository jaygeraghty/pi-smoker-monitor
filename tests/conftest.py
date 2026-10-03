"""Shared pytest fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a JSON file from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked `live` unless SMOKER_LIVE_TESTS=1."""
    if os.environ.get("SMOKER_LIVE_TESTS") == "1":
        return
    skip_live = pytest.mark.skip(reason="set SMOKER_LIVE_TESTS=1 to run live ETI Cloud tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
