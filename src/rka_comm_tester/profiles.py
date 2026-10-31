"""Filesystem adapter for the user's validated connection profile."""

import json
from pathlib import Path
from threading import Lock

from .config import ScanConfig


class FileProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def load(self) -> ScanConfig | None:
        with self._lock:
            if not self.path.exists():
                return None
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ScanConfig(**data)

    def save(self, config: ScanConfig) -> None:
        with self._lock:
            # Replace atomically so an interrupted write cannot truncate the profile.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
