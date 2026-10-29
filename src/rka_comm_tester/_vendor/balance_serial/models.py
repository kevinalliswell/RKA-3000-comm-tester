from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

MessageKind = Literal["reading", "ack", "error", "unknown"]


@dataclass(frozen=True)
class PortSettings:
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 1.0
    xonxoff: bool = False

    def to_serial_kwargs(self) -> dict:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "timeout": self.timeout,
            "xonxoff": self.xonxoff,
        }


@dataclass(frozen=True)
class BalanceMessage:
    protocol: str
    raw: str
    kind: MessageKind
    weight: float | None = None
    stable: bool | None = None
    unit: str | None = "g"
    code: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TareResult:
    protocol: str
    ack_received: bool
    error_received: bool
    zero_reading: BalanceMessage | None
    observed_messages: tuple[BalanceMessage, ...]

    @property
    def success(self) -> bool:
        return not self.error_received and self.zero_reading is not None
