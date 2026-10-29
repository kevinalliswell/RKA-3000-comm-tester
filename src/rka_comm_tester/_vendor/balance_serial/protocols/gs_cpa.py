from __future__ import annotations

import re

from ..models import BalanceMessage
from .base import BalanceProtocol

_PATTERN_ACK = re.compile(r"^A\d{2}\s*$")
_PATTERN_ERROR = re.compile(r"^E\d{2}\s*$")
_PATTERN_UNSTABLE = re.compile(r"\bG\s+U\b")
_PATTERN_STABLE = re.compile(r"\bG\s+S\b")
_PATTERN_WEIGHT = re.compile(r"^[+\-]?\d+(?:\.\d+)?\s+G\s+[SU]$")


class GSCpaProtocol(BalanceProtocol):
    name = "gs_cpa"

    def __init__(self, *, auto_continuous_output: bool = True) -> None:
        self.auto_continuous_output = auto_continuous_output

    def startup_commands(self) -> tuple[bytes, ...]:
        if self.auto_continuous_output:
            return (b"O1\r\n",)
        return ()

    def tare_command(self) -> bytes:
        return b"T \r\n"

    def parse_message(self, raw: str) -> BalanceMessage | None:
        line = raw.strip()
        if not line:
            return None

        if _PATTERN_ACK.match(line):
            return BalanceMessage(protocol=self.name, raw=line, kind="ack", code=line)

        if _PATTERN_ERROR.match(line):
            return BalanceMessage(protocol=self.name, raw=line, kind="error", code=line)

        match = _PATTERN_WEIGHT.fullmatch(line)
        if not match:
            return BalanceMessage(protocol=self.name, raw=line, kind="unknown")

        weight = float(line.split()[0])
        stable: bool | None
        if _PATTERN_UNSTABLE.search(line):
            stable = False
        elif _PATTERN_STABLE.search(line):
            stable = True
        else:
            stable = None

        return BalanceMessage(
            protocol=self.name,
            raw=line,
            kind="reading",
            weight=weight,
            stable=stable,
            unit="g",
        )
