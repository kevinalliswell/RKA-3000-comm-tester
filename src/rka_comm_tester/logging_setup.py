"""Persistent diagnostic log in the user's writable directory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def default_log_path() -> Path:
    return Path.home() / ".rka-comm-tester" / "logs" / "communication.log"


def setup_logging(path: Path | None = None, *, console: bool = False) -> Path:
    log_path = path or default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    base = logging.getLogger("rka_comm_tester")
    base.setLevel(logging.DEBUG)
    for handler in list(base.handlers):
        base.removeHandler(handler)
        handler.close()
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    base.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        base.addHandler(stream)
    base.info("RKA communication tester started; log=%s", log_path)
    return log_path
