"""Bounded serial probes. No setpoint, tare or arbitrary register writes."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Event
from typing import Callable, Iterator

from serial.tools import list_ports

from .config import ScanConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str


@dataclass(frozen=True)
class ScanResult:
    device: str
    port: str
    address: int | None
    status: str
    values: dict = field(default_factory=dict)
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def available_ports() -> list[PortInfo]:
    """Enumerate OS serial ports; this does not open or probe them."""
    ports = [PortInfo(p.device, p.description, p.hwid) for p in list_ports.comports()]
    logger.info("serial ports: %s", [asdict(p) for p in ports])
    return sorted(ports, key=lambda p: p.device)


def scan(
    cfg: ScanConfig,
    cancelled: Event | None = None,
    on_result: Callable[[ScanResult], None] | None = None,
) -> Iterator[ScanResult]:
    """Probe one configured port in a worker thread; check cancellation between addresses."""
    stop = cancelled or Event()
    logger.info("scan start: %s", cfg.to_dict())
    probe = {"taie": _scan_taie, "azbil": _scan_azbil, "balance": _scan_balance}[cfg.device]
    try:
        for result in probe(cfg, stop):
            if stop.is_set():
                break
            logger.info("scan result: %s", result.to_dict())
            if on_result:
                on_result(result)
            yield result
    except Exception as exc:  # port open and unexpected driver errors become visible results
        result = ScanResult(cfg.device, cfg.port, None, "error", detail=f"{type(exc).__name__}: {exc}")
        logger.exception("scan failed")
        if on_result:
            on_result(result)
        yield result
    finally:
        logger.info("scan finish: cancelled=%s", stop.is_set())


def _scan_taie(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    from ._vendor.taie_temp.modbus_rtu import (
        ModbusTimeoutError,
        SerialSettings,
        TaieModbusRtuClient,
        decode_tenths_signed,
    )

    settings = SerialSettings(
        port=cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
        stopbits=cfg.stopbits, timeout=cfg.timeout_s, read_retry_attempts=0,
        open_settle_delay=0.2, post_write_delay=0.05, inter_request_delay=0.05,
    )
    with TaieModbusRtuClient(cfg.start_address, settings) as client:
        for address in cfg.addresses:
            if stop.is_set():
                break
            client.slave_id = address
            try:
                pv_raw = client.read_holding_registers(0x008A, 1)[0]
            except ModbusTimeoutError as exc:
                yield ScanResult(cfg.device, cfg.port, address, "offline", detail=str(exc))
                continue
            except Exception as exc:
                yield ScanResult(cfg.device, cfg.port, address, "error", detail=str(exc))
                continue
            values = {"pv_raw": pv_raw, "pv_c": decode_tenths_signed(pv_raw)}
            try:
                sv_raw = client.read_holding_registers(0x0000, 1)[0]
                values.update(sv_raw=sv_raw, sv_c=decode_tenths_signed(sv_raw))
            except Exception as exc:
                # PV is the required liveness signal; SV is useful diagnostics but
                # some controllers do not expose it consistently.
                yield ScanResult(
                    cfg.device,
                    cfg.port,
                    address,
                    "online",
                    values,
                    f"PV 有效；SV 读取失败，仅完成基础在线判定: {exc}",
                )
            else:
                yield ScanResult(cfg.device, cfg.port, address, "online", values, "PV/SV 有效")


def _scan_azbil(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    from ._vendor.azbil_mfc.bus import RS485Bus
    from ._vendor.azbil_mfc.constants import COMM_8E1, COMM_8N2
    from ._vendor.azbil_mfc.device_catalog import read_flow_scale
    from ._vendor.azbil_mfc.exceptions import CPLTimeoutError
    from ._vendor.azbil_mfc.registers import RAMAddress

    comm_format = COMM_8E1 if cfg.parity == "E" else COMM_8N2
    bus = RS485Bus(cfg.port, baudrate=cfg.baudrate, comm_format=comm_format,
                   timeout=cfg.timeout_s, command_retries=0)
    with bus:
        for address in cfg.addresses:
            if stop.is_set():
                break
            try:
                pv_raw = bus.read_registers(address, RAMAddress.PV_CURRENT, 1)[0]
            except CPLTimeoutError as exc:
                yield ScanResult(cfg.device, cfg.port, address, "offline", detail=str(exc))
                continue
            except Exception as exc:
                yield ScanResult(cfg.device, cfg.port, address, "error", detail=str(exc))
                continue
            values = {"pv_raw": pv_raw}
            try:
                sp_raw = bus.read_registers(address, RAMAddress.SP_CURRENT, 1)[0]
                scale = read_flow_scale(bus, address)
                if not 1 <= scale.full_scale_raw <= 0xFFFF or not 1 <= scale.decimal_position <= 6 or scale.flow_unit_code not in (0, 1):
                    raise ValueError("设备返回的量程元数据无效")
                if not 0 <= pv_raw <= scale.full_scale_raw:
                    raise ValueError(f"PV 原始值 {pv_raw} 超出量程 0..{scale.full_scale_raw}")
                if not 0 <= sp_raw <= scale.full_scale_raw:
                    raise ValueError(f"SP 原始值 {sp_raw} 超出量程 0..{scale.full_scale_raw}")
                values.update(sp_raw=sp_raw, pv_lpm=round(scale.raw_to_flow(pv_raw), 4),
                              sp_lpm=round(scale.raw_to_flow(sp_raw), 4), scale=scale.to_dict())
            except Exception as exc:
                yield ScanResult(cfg.device, cfg.port, address, "degraded", values, f"PV 已响应，SP/量程校验失败: {exc}")
            else:
                yield ScanResult(cfg.device, cfg.port, address, "online", values, "PV/SP 与量程有效")


def _scan_balance(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    from ._vendor.balance_serial import BalanceClient, PortSettings
    from ._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
    from ._vendor.balance_serial.protocols.longteng import LongtengProtocol

    protocol = (GSCpaProtocol(auto_continuous_output=cfg.balance_start_output)
                if cfg.balance_protocol == "gs_cpa" else LongtengProtocol())
    settings = PortSettings(cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
                            stopbits=cfg.stopbits, timeout=cfg.timeout_s)
    client = BalanceClient(settings, protocol)
    output_started = False
    try:
        client.open()
        if cfg.balance_start_output and cfg.balance_protocol == "gs_cpa":
            client.initialize_device()
            output_started = True
            logger.info("balance startup command sent: O1")
        observed: list[str] = []
        for _ in range(cfg.balance_lines):
            if stop.is_set():
                break
            message = client.read_message()
            if message is None:
                continue
            observed.append(message.raw)
            if message.kind == "reading" and message.stable is True and message.weight is not None:
                yield ScanResult(cfg.device, cfg.port, None, "online",
                                 {"weight_g": message.weight, "raw": message.raw}, "稳定称重帧有效")
                return
            if message.kind == "error":
                yield ScanResult(cfg.device, cfg.port, None, "error", {"raw": message.raw}, "天平返回错误帧")
                return
        status = "degraded" if observed else "offline"
        detail = "未收到稳定称重帧" if observed else "未收到天平数据"
        yield ScanResult(cfg.device, cfg.port, None, status, {"observed": observed[-5:]}, detail)
    finally:
        if output_started:
            try:
                client.shutdown_device()
                logger.info("balance shutdown command sent: O0")
            except Exception:
                logger.exception("balance shutdown command failed")
        client.close()
