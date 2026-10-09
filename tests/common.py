"""Helpers shared by the tests."""

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a synthetic fixture by file stem, e.g. ``load_fixture("state")``."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
