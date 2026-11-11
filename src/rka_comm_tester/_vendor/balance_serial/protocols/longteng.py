from __future__ import annotations

import re

from ..models import BalanceMessage
from .base import BalanceProtocol

# JD series A II manual §6.1: FXXXX.XXXXKKK<CR><LF> - sign, digits, decimal point and a
# right-aligned 3-character unit field. An all-space unit field marks an unstable reading.
# A 3-character unit fills the field, so it can follow the digits without a space.
_PATTERN_FRAME = re.compile(r"^([+-]\d+(?:\.\d+)?)\s*([^\s\d.+-]{1,3})?$")
# Headed frames from a second balance model that also answers P/T (captured 2026-11-11, model
# not yet identified; lines "ST,-0045.578  g" end in CR LF LF): "ST" marks a stable reading. "US" is taken as unstable by the common
# convention of this frame family; any other header stays unknown. The unit may be absent,
# as in the older upstream format "ST,12.345", which was always grams.
_PATTERN_HEADED = re.compile(r"^(ST|US),\s*([+-]?\d+(?:\.\d+)?)\s*([^\s\d.+-]{1,3})?$")
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

        match = _PATTERN_HEADED.fullmatch(line)
        if not match:
            return BalanceMessage(protocol=self.name, raw=line, kind="unknown")

        return BalanceMessage(
            protocol=self.name,
            raw=line,
            kind="reading",
            weight=float(match.group(2)),
            stable=match.group(1) == "ST",
            unit=match.group(3) or "g",
        )
