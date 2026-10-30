"""Per-run evidence and export bundle."""

from __future__ import annotations

import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import ScanConfig
from .paths import data_dir

logger = logging.getLogger(__name__)


class RunStore:
    def __init__(self, mode: str, cfg: ScanConfig, root: Path | None = None) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = (root or data_dir() / "runs") / f"{stamp}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.events = (self.path / "events.jsonl").open("w", encoding="utf-8", buffering=1)
        (self.path / "config.json").write_text(
            json.dumps({"mode": mode, "profile": cfg.to_dict(), "app_version": __version__},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    def event(self, payload: dict) -> None:
        self.events.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def finish(self, summary: dict) -> None:
        self.events.flush()
        self.events.close()
        (self.path / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8",
        )
        logger.info("run saved: %s", self.path)


def export_bundle(run_dir: Path, destination: Path, log_path: Path) -> Path:
    """Export run files plus a snapshot of the rolling application log."""
    if not (run_dir / "summary.json").is_file():
        raise ValueError("测试尚未结束，不能导出")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(run_dir.iterdir()):
            if item.is_file():
                archive.write(item, item.name)
        if log_path.is_file():
            archive.write(log_path, "communication.log")
    return destination
