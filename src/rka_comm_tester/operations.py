"""Fixed, authenticated one-shot device writes with readback. No automatic write retry."""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .auth import AdminSession
from .config import ScanConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WritePreview:
    device: str
    port: str
    address: int | None
    current: float
    unit: str
    min_value: float
    max_value: float
    active_sp: float | None = None
    detail: str = ""
    resolution: float = 0.0001

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WriteResult:
    device: str
    port: str
    address: int | None
    target: float
    before: float | None
    after: float | None
    active_sp: float | None
    status: str
    detail: str
    username: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class DeviceRejected(Exception):
    """An explicit device error response confirms rejection."""


class WriteUncertain(Exception):
    """The command may have reached the device, but confirmation is absent."""


class _TaieSession:
    def __init__(self, cfg: ScanConfig, address: int) -> None:
        from ._vendor.taie_temp.modbus_rtu import SerialSettings, TaieModbusRtuClient

        self.cfg = cfg
        self.address = address
        settings = SerialSettings(
            cfg.port, baudrate=cfg.baudrate, parity=cfg.parity, stopbits=cfg.stopbits,
            timeout=cfg.timeout_s, read_retry_attempts=0, open_settle_delay=0.2,
            post_write_delay=0.05, inter_request_delay=0.05,
        )
        self.client = TaieModbusRtuClient(address, settings)

    def __enter__(self):
        self.client.open()
        return self

    def __exit__(self, *args):
        self.client.close()

    def preview(self) -> WritePreview:
        from ._vendor.taie_temp.modbus_rtu import decode_tenths_signed

        raw = self.client.read_holding_registers(0x0000, 1)[0]
        return WritePreview("taie", self.cfg.port, self.address, decode_tenths_signed(raw),
                            "°C", self.cfg.temp_sv_min_c, self.cfg.temp_sv_max_c,
                            resolution=0.1)

    def write_once(self, target: float) -> WritePreview | None:
        raw = int(round(target * 10)) & 0xFFFF
        try:
            echo = self.client.write_single_register(0x0000, raw)
        except Exception as exc:
            from ._vendor.taie_temp.modbus_rtu import ModbusExceptionResponse

            if isinstance(exc, ModbusExceptionResponse):
                raise DeviceRejected(str(exc)) from exc
            raise WriteUncertain(str(exc)) from exc
        if echo != raw:
            raise WriteUncertain(f"SV 写入回显不符: {echo} != {raw}")
        return None


class _AzbilSession:
    def __init__(self, cfg: ScanConfig, address: int) -> None:
        from ._vendor.azbil_mfc.bus import RS485Bus
        from ._vendor.azbil_mfc.constants import COMM_8E1, COMM_8N2

        self.cfg = cfg
        self.address = address
        self.bus = RS485Bus(cfg.port, baudrate=cfg.baudrate,
                            comm_format=COMM_8E1 if cfg.parity == "E" else COMM_8N2,
                            timeout=cfg.timeout_s, command_retries=0)
        self.scale = None

    def __enter__(self):
        self.bus.open()
        return self

    def __exit__(self, *args):
        self.bus.close()

    def preview(self) -> WritePreview:
        from ._vendor.azbil_mfc.device_catalog import read_flow_scale
        from ._vendor.azbil_mfc.registers import EEPROMAddress, RAMAddress

        self.scale = read_flow_scale(self.bus, self.address)
        if (not 1 <= self.scale.full_scale_raw <= 0xFFFF
                or not 1 <= self.scale.decimal_position <= 6
                or self.scale.flow_unit_code not in (0, 1)):
            raise ValueError("设备量程元数据无效，禁止写入")
        stored_raw = self.bus.read_registers(self.address, EEPROMAddress.SP_0, 1)[0]
        active_raw = self.bus.read_registers(self.address, RAMAddress.SP_CURRENT, 1)[0]
        return WritePreview(
            "azbil", self.cfg.port, self.address,
            round(self.scale.raw_to_flow(stored_raw), 4), "L/min", 0.0,
            self.scale.max_flow_rate_lpm,
            active_sp=round(self.scale.raw_to_flow(active_raw), 4),
            detail="SP-0 为 EEPROM；当前生效 SP 可能属于其他槽位",
            resolution=self.scale.raw_to_flow(1),
        )

    def write_once(self, target: float) -> WritePreview | None:
        from ._vendor.azbil_mfc.exceptions import CPLDeviceError
        from ._vendor.azbil_mfc.registers import EEPROMAddress

        assert self.scale is not None
        raw = self.scale.flow_to_raw(target)
        try:
            self.bus.write_registers(self.address, EEPROMAddress.SP_0, [raw])
        except CPLDeviceError as exc:
            raise DeviceRejected(str(exc)) from exc
        except Exception as exc:
            raise WriteUncertain(str(exc)) from exc
        return None


class _BalanceSession:
    def __init__(self, cfg: ScanConfig, address: int | None) -> None:
        from ._vendor.balance_serial import BalanceClient, PortSettings
        from ._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
        from ._vendor.balance_serial.protocols.longteng import LongtengProtocol

        self.cfg = cfg
        protocol = (GSCpaProtocol(auto_continuous_output=cfg.balance_start_output)
                    if cfg.balance_protocol == "gs_cpa" else LongtengProtocol())
        settings = PortSettings(cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
                                stopbits=cfg.stopbits, timeout=cfg.timeout_s)
        self.client = BalanceClient(settings, protocol)

    def __enter__(self):
        self.client.open()
        if self.cfg.balance_start_output and self.cfg.balance_protocol == "gs_cpa":
            self.client.initialize_device()
        return self

    def __exit__(self, *args):
        self.client.close()

    def preview(self) -> WritePreview:
        for _ in range(self.cfg.balance_lines):
            message = self.client.read_message()
            if message and message.kind == "reading" and message.stable is True and message.weight is not None:
                return WritePreview("balance", self.cfg.port, None, message.weight, "g", 0.0, 0.0,
                                    resolution=0.05)
        raise ValueError("没有稳定称重帧，不能去皮")

    def write_once(self, target: float) -> WritePreview | None:
        try:
            result = self.client.send_tare_and_wait(
                timeout=max(2.0, self.cfg.timeout_s * self.cfg.balance_lines),
                max_messages=self.cfg.balance_lines, zero_tolerance=0.05,
            )
        except Exception as exc:
            raise WriteUncertain(str(exc)) from exc
        if result.error_received:
            raise DeviceRejected("天平返回错误帧")
        if not result.success:
            raise WriteUncertain("去皮命令已发送，但未收到稳定零值")
        assert result.zero_reading is not None and result.zero_reading.weight is not None
        return WritePreview("balance", self.cfg.port, None, result.zero_reading.weight, "g", 0.0, 0.0,
                            resolution=0.05)


def _open_device(cfg: ScanConfig, address: int | None):
    if cfg.device == "balance":
        return _BalanceSession(cfg, None)
    if address is None or not 1 <= address <= (127 if cfg.device == "azbil" else 247):
        raise ValueError("请选择有效设备地址")
    return _TaieSession(cfg, address) if cfg.device == "taie" else _AzbilSession(cfg, address)


def preview_write(cfg: ScanConfig, address: int | None) -> WritePreview:
    with _open_device(cfg, address) as device:
        return device.preview()


def execute_write(
    cfg: ScanConfig, address: int | None, target: float, *, session: AdminSession,
    offline_confirmed: bool, expected_before: float,
) -> WriteResult:
    session.touch()
    if not offline_confirmed:
        raise PermissionError("请先确认装置已脱机并按现场 SOP 隔离")
    if not math.isfinite(target):
        raise ValueError("目标值须为有限数字")
    started = datetime.now(timezone.utc).isoformat()
    status = "unknown"
    detail = "未取得设备应答"
    before = None
    after = None
    active_sp = None
    with _open_device(cfg, address) as device:
        current = device.preview()
        before = current.current
        if abs(before - expected_before) > (0.05 if cfg.device == "balance" else 0.0001):
            raise ValueError("当前值已变化，请重新预览后再写入")
        if cfg.device == "balance":
            if target != 0:
                raise ValueError("天平仅允许去皮目标 0 g")
        elif not current.min_value <= target <= current.max_value:
            raise ValueError(f"目标值超出允许范围 {current.min_value:g}–{current.max_value:g} {current.unit}")
        logger.warning("write attempt user=%s device=%s port=%s address=%s before=%s target=%s",
                       session.username, cfg.device, cfg.port, address, before, target)
        try:
            observed = device.write_once(target)
            confirmed = observed if observed is not None else device.preview()
            after = confirmed.current
            active_sp = confirmed.active_sp
            tolerance = max(0.0001, current.resolution / 2 + 0.00001)
            if abs(after - target) > tolerance:
                status, detail = "failed", "写入应答后回读不符"
            elif cfg.device == "azbil" and active_sp is not None and abs(active_sp - target) > tolerance:
                status, detail = "stored_only", "SP-0 已存储，当前生效 SP 不同"
            else:
                status, detail = "success", "写入与回读一致"
        except DeviceRejected as exc:
            status, detail = "failed", f"设备明确拒绝: {exc}"
        except Exception as exc:
            status, detail = "unknown", f"写入或回读未确认: {exc}"
    result = WriteResult(cfg.device, cfg.port, address, target, before, after, active_sp,
                         status, detail, session.username, started)
    logger.warning("write result: %s", result.to_dict())
    return result
