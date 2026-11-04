"""Bounded serial probes. Scans never write setpoints, registers or tare commands; the
only device-changing path is the explicit, operator-confirmed balance tare test."""

from __future__ import annotations

import logging
import time
from contextlib import closing, contextmanager
from dataclasses import asdict
from threading import Event
from typing import Callable, Iterator

from serial.tools import list_ports

from .config import ScanConfig
from .models import PortInfo, ScanResult

logger = logging.getLogger(__name__)


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
    probe = {"taie": _scan_taie, "azbil": _scan_azbil, "azbil_temp": _scan_azbil_temp,
             "balance": _scan_balance}[cfg.device]
    return _run_probe("scan", probe, cfg, cancelled, on_result)


def tare_test(
    cfg: ScanConfig,
    cancelled: Event | None = None,
    on_result: Callable[[ScanResult], None] | None = None,
) -> Iterator[ScanResult]:
    """Send one tare command to the configured balance and confirm a stable zero reading."""
    if cfg.device != "balance":
        raise ValueError("去皮测试仅适用于电子天平")
    return _run_probe("tare", _tare_balance, cfg, cancelled, on_result)


def _run_probe(
    operation: str,
    probe: Callable[[ScanConfig, Event], Iterator[ScanResult]],
    cfg: ScanConfig,
    cancelled: Event | None,
    on_result: Callable[[ScanResult], None] | None,
) -> Iterator[ScanResult]:
    stop = cancelled or Event()
    logger.info("%s start: %s", operation, cfg.to_dict())
    try:
        with closing(probe(cfg, stop)) as results:
            for result in results:
                if stop.is_set():
                    break
                logger.info("%s result: %s", operation, result.to_dict())
                if on_result:
                    on_result(result)
                yield result
    except Exception as exc:  # port open and unexpected driver errors become visible results
        result = ScanResult(cfg.device, cfg.port, None, "error", detail=f"{type(exc).__name__}: {exc}")
        logger.exception("%s failed", operation)
        if on_result:
            on_result(result)
        yield result
    finally:
        logger.info("%s finish: cancelled=%s", operation, stop.is_set())


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


# Azbil C2/C3 word addresses from manual CP-SP-1478C chapter 10. Everything except the control
# status stays readable while the instrument's communication lock (C.LOc) is on.
_C23_PV = 0x238D
_C23_SP = 0x238E
_C23_MV = 0x2391
_C23_PV_DECIMALS = 0x1454
_C23_INPUT_ALARM = 0x23F1
_C23_CONTROL_STATUS = 0x23F4
_C23_INPUT_ALARMS = {
    0: "AL01 PV 超量程", 1: "AL02 PV 欠量程", 2: "AL03 基准结补偿/热电阻输入异常",
    4: "AL05 RSP 超量程", 5: "AL06 RSP 欠量程", 6: "AL07 MFB 输入异常",
    9: "AL10 马达调整异常", 10: "AL11 CT1/CT 输入异常", 11: "AL12 CT2/VT 输入异常",
}


@contextmanager
def _open_azbil_temp(cfg: ScanConfig):
    """Yield (read_word(address, register), timeout_error) for the instrument's protocol."""
    if cfg.azbil_temp_protocol == "cpl":
        from ._vendor.azbil_mfc.bus import RS485Bus
        from ._vendor.azbil_mfc.exceptions import CPLTimeoutError

        bus = RS485Bus(cfg.port, baudrate=cfg.baudrate, timeout=cfg.timeout_s, command_retries=0,
                       serial_format=(cfg.parity, cfg.stopbits))
        with bus:
            yield (lambda address, register: bus.read_registers(address, register, 1)[0],
                   CPLTimeoutError)
        return

    from ._vendor.taie_temp.modbus_rtu import (
        ModbusTimeoutError,
        SerialSettings,
        TaieModbusRtuClient,
    )

    settings = SerialSettings(
        port=cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
        stopbits=cfg.stopbits, timeout=cfg.timeout_s, read_retry_attempts=0,
        open_settle_delay=0.2, post_write_delay=0.05, inter_request_delay=0.12,
    )
    # The client is plain Modbus RTU; only its register symbols are TAIE specific.
    with TaieModbusRtuClient(cfg.start_address, settings) as client:
        def read_word(address: int, register: int) -> int:
            client.slave_id = address
            return client.read_holding_registers(register, 1)[0]

        yield read_word, ModbusTimeoutError


def _scan_azbil_temp(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    from ._vendor.taie_temp.modbus_rtu import to_signed_16

    with _open_azbil_temp(cfg) as (read_word, timeout_error):
        for address in cfg.addresses:
            if stop.is_set():
                break
            try:
                pv_raw = read_word(address, _C23_PV)
            except timeout_error as exc:
                yield ScanResult(cfg.device, cfg.port, address, "offline", detail=str(exc))
                continue
            except Exception as exc:
                yield ScanResult(cfg.device, cfg.port, address, "error", detail=str(exc))
                continue
            values = {"pv_raw": pv_raw}
            try:
                sp_raw = read_word(address, _C23_SP)
                mv_raw = read_word(address, _C23_MV)
                decimals = read_word(address, _C23_PV_DECIMALS)
                input_alarm = read_word(address, _C23_INPUT_ALARM)
                if not 0 <= decimals <= 3:
                    raise ValueError(f"PV 小数点位置 {decimals} 无效")
            except Exception as exc:
                yield ScanResult(cfg.device, cfg.port, address, "degraded", values,
                                 f"PV 已响应，SP/MV/小数点/报警校验失败: {exc}")
                continue
            # PV and SP share the live PV decimal position; MV always has one decimal.
            values.update(
                sp_raw=sp_raw, mv_raw=mv_raw, pv_decimals=decimals, input_alarm=input_alarm,
                pv=to_signed_16(pv_raw) / 10**decimals, sp=to_signed_16(sp_raw) / 10**decimals,
                mv_pct=to_signed_16(mv_raw) / 10,
            )
            note = ""
            try:
                control = read_word(address, _C23_CONTROL_STATUS)
                values.update(run_ready="READY" if control & 0x02 else "RUN",
                              auto_manual="MANUAL" if control & 0x01 else "AUTO")
            except Exception as exc:
                note = f"；控制状态不可读（仪表可能处于通讯锁定）: {exc}"
            if input_alarm:
                # A PV alarm pins PV at its display limit, so a readable PV alone proves nothing.
                alarms = [name for bit, name in _C23_INPUT_ALARMS.items() if input_alarm >> bit & 1]
                yield ScanResult(cfg.device, cfg.port, address, "degraded", values,
                                 f"仪表输入报警: {'、'.join(alarms) or f'0x{input_alarm:04X}'}{note}")
            else:
                yield ScanResult(cfg.device, cfg.port, address, "online", values,
                                 f"PV/SP/MV 与小数点有效，无输入报警{note}")


def _open_balance(cfg: ScanConfig):
    from ._vendor.balance_serial import BalanceClient, PortSettings
    from ._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
    from ._vendor.balance_serial.protocols.longteng import LongtengProtocol

    protocol = (GSCpaProtocol(auto_continuous_output=cfg.balance_start_output)
                if cfg.balance_protocol == "gs_cpa" else LongtengProtocol())
    settings = PortSettings(cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
                            stopbits=cfg.stopbits, timeout=cfg.timeout_s)
    return BalanceClient(settings, protocol)


def _is_stable_weight(message) -> bool:
    return message.kind == "reading" and message.stable is True and message.weight is not None


def _start_balance_output(cfg: ScanConfig, client) -> bool:
    if cfg.balance_start_output and cfg.balance_protocol == "gs_cpa":
        client.initialize_device()
        logger.info("balance startup command sent: O1")
        return True
    return False


def _close_balance(client, output_started: bool) -> None:
    if output_started:
        try:
            client.shutdown_device()
            logger.info("balance shutdown command sent: O0")
        except Exception:
            logger.exception("balance shutdown command failed")
    client.close()


def _scan_balance(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    client = _open_balance(cfg)
    output_started = False
    try:
        client.open()
        output_started = _start_balance_output(cfg, client)
        observed: list[str] = []
        for _ in range(cfg.balance_lines):
            if stop.is_set():
                break
            message = client.read_message()
            if message is None:
                continue
            observed.append(message.raw)
            if _is_stable_weight(message):
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
        _close_balance(client, output_started)


def _tare_balance(cfg: ScanConfig, stop: Event) -> Iterator[ScanResult]:
    client = _open_balance(cfg)
    output_started = False
    try:
        client.open()
        output_started = _start_balance_output(cfg, client)
        # Best-effort baseline so the operator can see how much weight was tared off.
        values: dict = {}
        for _ in range(cfg.balance_lines):
            if stop.is_set():
                return
            message = client.read_message()
            if message is not None and _is_stable_weight(message):
                values["before_g"] = message.weight
                break
        if stop.is_set():
            return
        # A buffered pre-tare zero must not be mistaken for confirmation.
        client.reset_input_buffer()
        client.send_tare()
        logger.info("balance tare command sent: %r", client.protocol.tare_command())
        observed: list[str] = []
        deadline = time.monotonic() + cfg.tare_timeout_s
        while time.monotonic() < deadline:
            if stop.is_set():
                return
            message = client.read_message(deadline=deadline)
            if message is not None:
                observed.append(message.raw)
            # A read (or driver scheduling) may finish at/after the deadline.
            # Keep any response for diagnostics, but never confirm a late zero.
            if time.monotonic() >= deadline:
                break
            if message is None:
                continue
            if message.kind == "ack":
                values["ack"] = message.code
            elif message.kind == "error":
                values["raw"] = message.raw
                yield ScanResult(cfg.device, cfg.port, None, "error", values,
                                 f"天平拒绝去皮命令（{message.code}）")
                return
            elif message.kind == "reading" and message.weight is not None:
                values["after_g"] = message.weight
                if message.stable is True and abs(message.weight) <= cfg.tare_zero_tolerance_g:
                    values["raw"] = message.raw
                    yield ScanResult(cfg.device, cfg.port, None, "online", values,
                                     f"去皮成功：稳定零点（容差 ±{cfg.tare_zero_tolerance_g:g} g）")
                    return
        values["observed"] = observed[-5:]
        if "after_g" in values:
            status, detail = "degraded", f"已发送去皮命令，但 {cfg.tare_timeout_s:g} 秒内未读到稳定零点"
        elif "ack" in values:
            status, detail = "degraded", "天平已应答去皮命令，但未收到称重帧；可启用 O1 连续输出后重试"
        elif observed:
            status, detail = "degraded", f"天平已响应，但 {cfg.tare_timeout_s:g} 秒内未收到可确认的稳定零点"
        else:
            status, detail = "offline", "已发送去皮命令，但未收到天平数据"
        yield ScanResult(cfg.device, cfg.port, None, status, values, detail)
    finally:
        _close_balance(client, output_started)
