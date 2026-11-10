from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..models import BalanceMessage


class BalanceProtocol(ABC):
    name: str

    def startup_commands(self) -> Sequence[bytes]:
        return ()

    def shutdown_commands(self) -> Sequence[bytes]:
        return ()

    def poll_command(self) -> bytes | None:
        """Request one reading from a balance that does not stream on its own."""
        return None

    @abstractmethod
    def tare_command(self) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def parse_message(self, raw: str) -> BalanceMessage | None:
        raise NotImplementedError
