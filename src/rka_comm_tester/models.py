"""Protocol-independent domain values; no UI, HTTP or serial dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str


@dataclass(frozen=True)
class ScanResult:
    device: str
    port: str
    address: int | None
    status: str
    values: dict = field(default_factory=dict)
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)
