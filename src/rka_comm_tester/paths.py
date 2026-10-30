"""Writable application data locations; never use the installed program directory."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "RKACommTester"
    return Path.home() / ".rka-comm-tester"
