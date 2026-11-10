from __future__ import annotations

import re

from ..models import BalanceMessage
from .base import BalanceProtocol

# JD series A II manual §6.1: FXXXX.XXXXKKK<CR><LF> - sign, digits, decimal point and a
# right-aligned 3-character unit field. An all-space unit field marks an unstable reading.
# A 3-character unit fills the field, so it can follow the digits without a space.
_PATTERN_FRAME = re.compile(r"^([+-]\d+(?:\.\d+)?)\s*([^\s\d.+-]{1,3})?$")
# Older upstream format, kept for balances already configured that way.
_PATTERN_ST = re.compile(r"^ST,\s*([+-]?\d+\.\d+)$")
# §4.2: the balance echoes every accepted command and answers "Err" otherwise.
_COMMAND_ECHOES = {"O", "T", "C", "M", "P"}


class LongtengProtocol(BalanceProtocol):
    name = "longteng"

    def tare_command(self) -> bytes:
        return b"T\r\n"

    def poll_command(self) -> bytes:
        # Same as the print key: one reading, no setting change. The factory output mode
        # (C5-2) sends nothing until it is pressed or this command arrives.
        return b"P\r\n"

    def parse_message(self, raw: str) -> BalanceMessage | None:
        line = raw.strip()
        if not line:
            return None

        if line in _COMMAND_ECHOES:
            return BalanceMessage(protocol=self.name, raw=line, kind="ack", code=line)
        if line == "Err":
            return BalanceMessage(protocol=self.name, raw=line, kind="error", code=line)

        match = _PATTERN_FRAME.fullmatch(line)
        if match:
            unit = match.group(2)
            return BalanceMessage(
                protocol=self.name,
                raw=line,
                kind="reading",
                weight=float(match.group(1)),
                stable=unit is not None,
                unit=unit,
            )

        match = _PATTERN_ST.fullmatch(line)
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
