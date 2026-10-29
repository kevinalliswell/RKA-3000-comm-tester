from __future__ import annotations

from .protocols.base import BalanceProtocol
from .protocols.gs_cpa import GSCpaProtocol
from .protocols.longteng import LongtengProtocol


def create_protocol(name: str, **kwargs) -> BalanceProtocol:
    normalized = name.strip().lower()
    if normalized == "gs_cpa":
        return GSCpaProtocol(**kwargs)
    if normalized == "longteng":
        return LongtengProtocol(**kwargs)
    raise ValueError(f"Unsupported protocol: {name}")
