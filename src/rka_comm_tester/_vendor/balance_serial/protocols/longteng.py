from __future__ import annotations

import re

from ..models import BalanceMessage
from .base import BalanceProtocol

_PATTERN_WEIGHT = re.compile(r"^ST,\s*([+-]?\d+\.\d+)$")


class LongtengProtocol(BalanceProtocol):
    name = "longteng"

    def tare_command(self) -> bytes:
        return b"T\r\n"

    def parse_message(self, raw: str) -> BalanceMessage | None:
        line = raw.strip()
        if not line:
            return None

        match = _PATTERN_WEIGHT.fullmatch(line)
        if not match:
            return BalanceMessage(protocol=self.name, raw=line, kind="unknown")

        weight = float(match.group(1))
        return BalanceMessage(
            protocol=self.name,
            raw=line,
            kind="reading",
            weight=weight,
            stable=True,
            unit="g",
        )
