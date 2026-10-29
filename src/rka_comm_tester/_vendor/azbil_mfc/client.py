"""High-level client API for Azbil MQV devices."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .bus import RS485Bus
from .constants import MODE_CLOSE, MODE_CONTROL, MODE_OPEN
from .device_catalog import DeviceProfile, FlowScale, FlowScaleAudit, read_flow_scale
from .registers import EEPROMAddress, RAMAddress

logger = logging.getLogger(__name__)

STATUS_BLOCK_REFRESH_INTERVAL = 10
STATUS_BLOCK_RETRY_DELAY_S = 15.0
STATUS_CACHE_TTL_S = 0.8
STATUS_STALE_FALLBACK_S = 15.0
STATUS_FAILURE_BACKOFF_BASE_S = 1.0
STATUS_FAILURE_BACKOFF_MAX_S = 5.0


@dataclass
class MQVStatus:
    """Runtime snapshot for a single MQV device."""

    sp: int
    pv: int
    valve_drive: int
    mode: Optional[int]
    alarm: Optional[int]
    sp_number: Optional[int]

    @property
    def mode_name(self) -> str:
        if self.mode is None:
            return "未知"
        return {0: "全闭", 1: "控制", 2: "全开"}.get(self.mode, f"未知({self.mode})")


class MQVDevice:
    """Represents a single MQV device on the RS-485 bus."""

    def __init__(self, bus: RS485Bus, address: int):
        if not 1 <= address <= 127:
            raise ValueError(f"设备地址必须在 1-127 之间，当前: {address}")
        self.bus = bus
        self.address = address
        self._last_mode: Optional[int] = None
        self._last_alarm: Optional[int] = None
        self._last_sp_number: Optional[int] = None
        self._status_reads_since_block = STATUS_BLOCK_REFRESH_INTERVAL
        self._next_status_block_retry_at = 0.0
        self._last_status: Optional[MQVStatus] = None
        self._last_status_at = 0.0
        self._status_failure_count = 0
        self._next_status_retry_at = 0.0

    def read_pv(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.PV_CURRENT, 1)
        return values[0]

    def read_sp(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.SP_CURRENT, 1)
        return values[0]

    def read_pv_sp(self) -> tuple[int, int]:
        values = self.bus.read_registers(self.address, RAMAddress.SP_CURRENT, 2)
        return values[0], values[1]

    def read_valve_drive(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.VALVE_DRIVE, 1)
        return values[0]

    def read_totalizer(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.TOTALIZER_LOW, 2)
        return (values[1] << 16) | values[0]

    def read_operation_mode(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.OPERATION_MODE, 1)
        self._last_mode = values[0]
        return values[0]

    def read_alarm_status(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.ALARM_STATUS, 1)
        self._last_alarm = values[0]
        return values[0]

    def read_sp_number(self) -> int:
        values = self.bus.read_registers(self.address, RAMAddress.SP_NUMBER, 1)
        self._last_sp_number = values[0]
        return values[0]

    def _cache_status(self, status: MQVStatus, now: float) -> MQVStatus:
        self._last_status = status
        self._last_status_at = now
        return status

    def _invalidate_status_cache(self) -> None:
        self._last_status_at = 0.0

    def _reset_status_backoff(self) -> None:
        self._status_failure_count = 0
        self._next_status_retry_at = 0.0

    def _record_status_failure(self, now: float) -> float:
        self._status_failure_count += 1
        delay = min(
            STATUS_FAILURE_BACKOFF_BASE_S * (2 ** (self._status_failure_count - 1)),
            STATUS_FAILURE_BACKOFF_MAX_S,
        )
        self._next_status_retry_at = now + delay
        return delay

    def read_status(self) -> MQVStatus:
        """Read a hardware-compatible status snapshot."""

        now = time.monotonic()
        if self._last_status is not None and (now - self._last_status_at) <= STATUS_CACHE_TTL_S:
            return self._last_status
        if self._last_status is not None and now < self._next_status_retry_at:
            return self._last_status

        should_refresh_metadata = (
            self._last_mode is None
            or self._last_alarm is None
            or self._last_sp_number is None
            or self._status_reads_since_block >= STATUS_BLOCK_REFRESH_INTERVAL
        )

        if should_refresh_metadata and now >= self._next_status_block_retry_at:
            try:
                values = self.bus.read_registers(self.address, RAMAddress.ALARM_STATUS, 8)
                self._last_alarm = values[0]
                self._last_mode = values[3]
                self._last_sp_number = values[4]
                self._status_reads_since_block = 0
                self._next_status_block_retry_at = 0.0
                self._reset_status_backoff()
                return self._cache_status(
                    MQVStatus(
                        sp=values[5],
                        pv=values[6],
                        valve_drive=values[7],
                        mode=self._last_mode,
                        alarm=self._last_alarm,
                        sp_number=self._last_sp_number,
                    ),
                    now,
                )
            except Exception as exc:
                self._next_status_block_retry_at = now + STATUS_BLOCK_RETRY_DELAY_S
                logger.warning(
                    "设备 %d: 连续状态块读取失败，%.0f 秒后重试，当前回退到过程量读取: %s",
                    self.address,
                    STATUS_BLOCK_RETRY_DELAY_S,
                    exc,
                )

        try:
            values = self.bus.read_registers(self.address, RAMAddress.SP_CURRENT, 3)
        except Exception as exc:
            retry_delay = self._record_status_failure(now)
            if self._last_status is not None and (now - self._last_status_at) <= STATUS_STALE_FALLBACK_S:
                logger.warning(
                    "设备 %d: 过程量读取失败，沿用 %.1f 秒前缓存状态，%.1f 秒后重试: %s",
                    self.address,
                    now - self._last_status_at,
                    retry_delay,
                    exc,
                )
                return self._last_status
            raise

        self._status_reads_since_block += 1
        self._reset_status_backoff()
        return self._cache_status(
            MQVStatus(
                sp=values[0],
                pv=values[1],
                valve_drive=values[2],
                mode=self._last_mode,
                alarm=self._last_alarm,
                sp_number=self._last_sp_number,
            ),
            time.monotonic(),
        )

    def write_sp(self, sp_number: int, value: int) -> None:
        if not 0 <= sp_number <= 7:
            raise ValueError(f"SP 编号必须在 0-7 之间，当前: {sp_number}")

        reg_addr = EEPROMAddress.SP_0 + sp_number
        self.bus.write_registers(self.address, reg_addr, [value])
        self._invalidate_status_cache()
        logger.info("设备 %d: 写入 SP-%d = %d", self.address, sp_number, value)

    def set_operation_mode(self, mode: int) -> None:
        if mode not in (MODE_CLOSE, MODE_CONTROL, MODE_OPEN):
            raise ValueError(f"无效的动作模式: {mode}")
        self.bus.write_registers(self.address, RAMAddress.OPERATION_MODE, [mode])
        self._last_mode = mode
        self._invalidate_status_cache()
        logger.info(
            "设备 %d: 设置动作模式 = %s",
            self.address,
            {0: "全闭", 1: "控制", 2: "全开"}[mode],
        )

    def read_gas_type(self) -> int:
        values = self.bus.read_registers(self.address, EEPROMAddress.GAS_TYPE_1, 1)
        return values[0]

    def read_comm_address(self) -> int:
        values = self.bus.read_registers(self.address, EEPROMAddress.COMM_ADDR, 1)
        return values[0]

    def read_all_sp(self) -> List[int]:
        return self.bus.read_registers(self.address, EEPROMAddress.SP_0, 8)

    def __repr__(self) -> str:
        return f"MQVDevice(address={self.address})"


class MQVBusManager:
    """Tracks devices discovered on a shared RS-485 bus."""

    def __init__(self, bus: RS485Bus):
        self.bus = bus
        self._devices: Dict[int, MQVDevice] = {}
        self._flow_scales: Dict[int, FlowScale] = {}
        self._flow_scale_audits: Dict[int, FlowScaleAudit] = {}
        self._flow_scale_lock = threading.Lock()

    def add_device(self, address: int) -> MQVDevice:
        device = MQVDevice(self.bus, address)
        self._devices[address] = device
        return device

    def get_device(self, address: int) -> Optional[MQVDevice]:
        return self._devices.get(address)

    def get_flow_scale(
        self,
        address: int,
        profile: Optional[DeviceProfile] = None,
        refresh: bool = False,
    ) -> Optional[FlowScale]:
        if not refresh:
            with self._flow_scale_lock:
                cached = self._flow_scales.get(address)
            if cached is not None:
                return cached

        try:
            scale = read_flow_scale(self.bus, address)
        except Exception as exc:
            logger.debug("设备 %d 量程读取失败: %s", address, exc)
            if refresh:
                return None
        else:
            with self._flow_scale_lock:
                self._flow_scales[address] = scale
            return scale

        with self._flow_scale_lock:
            cached = self._flow_scales.get(address)
        if cached is not None:
            return cached
        if profile is not None:
            return profile.flow_scale
        return None

    def audit_flow_scale(
        self,
        address: int,
        profile: Optional[DeviceProfile] = None,
        refresh: bool = False,
    ) -> FlowScaleAudit:
        live_scale = None
        if not refresh:
            with self._flow_scale_lock:
                live_scale = self._flow_scales.get(address)

        if live_scale is None:
            try:
                live_scale = read_flow_scale(self.bus, address)
            except Exception as exc:
                logger.debug("设备 %d 量程自检读取失败，沿用缓存/配置: %s", address, exc)
            else:
                with self._flow_scale_lock:
                    self._flow_scales[address] = live_scale

        profile_scale = profile.flow_scale if profile is not None else None
        resolved_scale = live_scale or profile_scale

        if live_scale is not None:
            scale_source = "live"
        elif profile_scale is not None:
            scale_source = "profile_fallback"
        else:
            scale_source = "unavailable"

        if profile_scale is None:
            scale_status = "no_profile"
        elif live_scale is None:
            scale_status = "live_unavailable"
        elif live_scale.same_as(profile_scale):
            scale_status = "match"
        else:
            scale_status = "mismatch"

        audit = FlowScaleAudit(
            address=address,
            live_scale=live_scale,
            profile_scale=profile_scale,
            resolved_scale=resolved_scale,
            scale_source=scale_source,
            scale_status=scale_status,
        )
        with self._flow_scale_lock:
            self._flow_scale_audits[address] = audit
        return audit

    def audit_all_flow_scales(
        self,
        profile_resolver=None,
        refresh: bool = False,
    ) -> Dict[int, FlowScaleAudit]:
        results: Dict[int, FlowScaleAudit] = {}
        for addr in self._devices:
            profile = profile_resolver(addr) if callable(profile_resolver) else None
            results[addr] = self.audit_flow_scale(addr, profile=profile, refresh=refresh)
        return results

    def get_flow_scale_audit(self, address: int) -> Optional[FlowScaleAudit]:
        with self._flow_scale_lock:
            return self._flow_scale_audits.get(address)

    def prime_flow_scales(self) -> None:
        for addr in self._devices:
            self.get_flow_scale(addr, refresh=True)

    def scan_and_add(self, start: int = 1, end: int = 127) -> List[MQVDevice]:
        found_addresses = self.bus.scan_devices(start, end)
        return [self.add_device(addr) for addr in found_addresses]

    @property
    def devices(self) -> dict:
        return dict(self._devices)

    def read_all_pv(self) -> dict:
        results = {}
        for addr, device in self._devices.items():
            try:
                results[addr] = device.read_pv()
            except Exception as exc:
                logger.warning("设备 %d 读取 PV 失败: %s", addr, exc)
                results[addr] = None
        return results

    def read_all_status(self) -> dict:
        results = {}
        for addr, device in self._devices.items():
            try:
                results[addr] = device.read_status()
            except Exception as exc:
                logger.warning("设备 %d 读取状态失败: %s", addr, exc)
                results[addr] = None
        return results
