"""Known real-device metadata and flow scaling helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .bus import RS485Bus

MACHINE_FULL_SCALE_FLOW = 0x03EA
MACHINE_DECIMAL_POSITION = 0x03EB
MACHINE_FLOW_UNIT = 0x03ED

UNIT_ML_PER_MIN = 0
UNIT_L_PER_MIN = 1


@dataclass(frozen=True)
class FlowScale:
    """Converts between device raw values and engineering flow units."""

    full_scale_raw: int
    decimal_position: int
    flow_unit_code: int

    @property
    def decimal_places(self) -> int:
        return max(0, self.decimal_position - 1)

    @property
    def unit_name(self) -> str:
        return {
            UNIT_ML_PER_MIN: "mL/min",
            UNIT_L_PER_MIN: "L/min",
        }.get(self.flow_unit_code, "L/min")

    @property
    def unit_to_lpm_factor(self) -> float:
        return 0.001 if self.flow_unit_code == UNIT_ML_PER_MIN else 1.0

    @property
    def max_flow_rate_lpm(self) -> float:
        return self.raw_to_flow(self.full_scale_raw)

    @property
    def max_raw(self) -> int:
        return self.full_scale_raw

    def raw_to_flow(self, raw: int) -> float:
        scaled = raw / (10 ** self.decimal_places)
        return scaled * self.unit_to_lpm_factor

    def flow_to_raw(self, flow_lpm: float) -> int:
        if flow_lpm <= 0:
            return 0
        unit_value = flow_lpm / self.unit_to_lpm_factor
        raw = round(unit_value * (10 ** self.decimal_places))
        return max(0, min(int(raw), self.full_scale_raw))

    def raw_to_percent(self, raw: int) -> float:
        if self.full_scale_raw <= 0:
            return 0.0
        return raw / self.full_scale_raw * 100.0

    def same_as(self, other: Optional["FlowScale"]) -> bool:
        if other is None:
            return False
        return (
            self.full_scale_raw == other.full_scale_raw
            and self.decimal_position == other.decimal_position
            and self.flow_unit_code == other.flow_unit_code
        )

    def to_dict(self) -> dict:
        return {
            "full_scale_raw": self.full_scale_raw,
            "decimal_position": self.decimal_position,
            "flow_unit": self.unit_name,
            "max_flow_rate_lpm": round(self.max_flow_rate_lpm, 4),
        }

    @classmethod
    def infer_for_lpm(cls, max_flow_rate_lpm: float) -> "FlowScale":
        digits_before_decimal = max(1, len(str(int(math.floor(max_flow_rate_lpm or 0)))))
        decimal_places = max(0, min(3, 4 - digits_before_decimal))
        full_scale_raw = int(round(max_flow_rate_lpm * (10 ** decimal_places)))
        return cls(
            full_scale_raw=max(1, full_scale_raw),
            decimal_position=decimal_places + 1,
            flow_unit_code=UNIT_L_PER_MIN,
        )


@dataclass(frozen=True)
class DeviceProfile:
    address: int
    channel_name: str
    gas_name: str
    gas_type: int
    max_flow_rate_sccm: int
    flow_scale: Optional[FlowScale] = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "flow_scale",
            self.flow_scale or FlowScale.infer_for_lpm(self.max_flow_rate_sccm / 1000.0),
        )

    @property
    def max_flow_rate_lpm(self) -> float:
        return self.flow_scale.max_flow_rate_lpm

    @property
    def gas_display_name(self) -> str:
        return f"{self.gas_name} (0-{self.max_flow_rate_lpm:g} {self.flow_scale.unit_name})"

    def flow_to_raw(self, flow_lpm: float) -> int:
        return self.flow_scale.flow_to_raw(flow_lpm)

    def raw_to_flow(self, raw: int) -> float:
        return self.flow_scale.raw_to_flow(raw)


@dataclass(frozen=True)
class FlowScaleAudit:
    address: int
    live_scale: Optional[FlowScale]
    profile_scale: Optional[FlowScale]
    resolved_scale: Optional[FlowScale]
    scale_source: str
    scale_status: str

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "scale_source": self.scale_source,
            "scale_status": self.scale_status,
            "live_scale": self.live_scale.to_dict() if self.live_scale else None,
            "profile_scale": self.profile_scale.to_dict() if self.profile_scale else None,
        }


def _lpm_scale(full_scale_raw: int, decimal_position: int) -> FlowScale:
    return FlowScale(
        full_scale_raw=full_scale_raw,
        decimal_position=decimal_position,
        flow_unit_code=UNIT_L_PER_MIN,
    )


KNOWN_DEVICE_PROFILES = {
    1: DeviceProfile(1, "CO", "CO", 0, 20_000, flow_scale=_lpm_scale(200, 2)),
    2: DeviceProfile(2, "O2", "O2", 2, 20_000, flow_scale=_lpm_scale(200, 2)),
    3: DeviceProfile(3, "CH4", "CH4", 7, 20_000, flow_scale=_lpm_scale(200, 2)),
    4: DeviceProfile(4, "N2", "N2", 1, 20_000, flow_scale=_lpm_scale(200, 2)),
    5: DeviceProfile(5, "CO2", "CO2", 4, 12_000, flow_scale=_lpm_scale(1200, 3)),
    6: DeviceProfile(6, "H2", "H2", 3, 10_000, flow_scale=_lpm_scale(1000, 3)),
}


def get_device_profile(address: int) -> Optional[DeviceProfile]:
    return KNOWN_DEVICE_PROFILES.get(address)


def read_flow_scale(bus: RS485Bus, address: int) -> FlowScale:
    """Read live flow scaling metadata from the device."""

    full_scale_raw = bus.read_registers(address, MACHINE_FULL_SCALE_FLOW, 1)[0]
    decimal_position = bus.read_registers(address, MACHINE_DECIMAL_POSITION, 1)[0]
    flow_unit_code = bus.read_registers(address, MACHINE_FLOW_UNIT, 1)[0]
    return FlowScale(
        full_scale_raw=full_scale_raw,
        decimal_position=decimal_position,
        flow_unit_code=flow_unit_code,
    )


def resolve_flow_scale(
    bus: Optional[RS485Bus],
    address: int,
    profile: Optional[DeviceProfile] = None,
) -> Optional[FlowScale]:
    """Prefer live device scaling and fall back to the known profile."""

    if bus is not None:
        try:
            return read_flow_scale(bus, address)
        except Exception:
            pass

    if profile is not None:
        return profile.flow_scale
    return None
