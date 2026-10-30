"""Persistent read sessions for real hardware and frame-level simulators."""

from __future__ import annotations

from .config import ScanConfig
from .stress import StressConfig


def open_source(cfg: StressConfig):
    if cfg.mode.startswith("sim_"):
        from .simulated import SimulatedSource

        return SimulatedSource(cfg.profile)
    return RealReadSource(cfg.profile)


class RealReadSource:
    def __init__(self, cfg: ScanConfig) -> None:
        self.cfg = cfg
        self.device = None
        self.scales = {}

    def open(self) -> None:
        self.close()
        cfg = self.cfg
        if cfg.device == "taie":
            from ._vendor.taie_temp.modbus_rtu import SerialSettings, TaieModbusRtuClient

            settings = SerialSettings(
                cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
                stopbits=cfg.stopbits, timeout=cfg.timeout_s, read_retry_attempts=0,
                open_settle_delay=0.2, post_write_delay=0.05, inter_request_delay=0.05,
            )
            self.device = TaieModbusRtuClient(cfg.start_address, settings)
            self.device.open()
        elif cfg.device == "azbil":
            from ._vendor.azbil_mfc.bus import RS485Bus
            from ._vendor.azbil_mfc.constants import COMM_8E1, COMM_8N2
            from ._vendor.azbil_mfc.device_catalog import read_flow_scale

            self.device = RS485Bus(cfg.port, baudrate=cfg.baudrate,
                                   comm_format=COMM_8E1 if cfg.parity == "E" else COMM_8N2,
                                   timeout=cfg.timeout_s, command_retries=0)
            self.device.open()
            self.scales = {address: read_flow_scale(self.device, address) for address in cfg.addresses}
            if any(not 1 <= scale.full_scale_raw <= 0xFFFF or not 1 <= scale.decimal_position <= 6
                   or scale.flow_unit_code not in (0, 1) for scale in self.scales.values()):
                raise ValueError("MFC 量程元数据无效")
        else:
            from ._vendor.balance_serial import BalanceClient, PortSettings
            from ._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
            from ._vendor.balance_serial.protocols.longteng import LongtengProtocol

            protocol = (GSCpaProtocol(auto_continuous_output=cfg.balance_start_output)
                        if cfg.balance_protocol == "gs_cpa" else LongtengProtocol())
            settings = PortSettings(cfg.port, baudrate=cfg.baudrate, parity=cfg.parity,
                                    stopbits=cfg.stopbits, timeout=cfg.timeout_s)
            self.device = BalanceClient(settings, protocol)
            self.device.open()
            if cfg.balance_start_output and cfg.balance_protocol == "gs_cpa":
                self.device.initialize_device()

    def close(self) -> None:
        if self.device is not None:
            self.device.close()
            self.device = None
        self.scales = {}

    def sample(self) -> dict:
        if self.device is None:
            raise ConnectionError("串口未连接")
        cfg = self.cfg
        if cfg.device == "taie":
            from ._vendor.taie_temp.modbus_rtu import decode_tenths_signed

            values = {}
            for address in cfg.addresses:
                self.device.slave_id = address
                pv = self.device.read_holding_registers(0x008A, 1)[0]
                sv = self.device.read_holding_registers(0x0000, 1)[0]
                values[str(address)] = {"pv_c": decode_tenths_signed(pv),
                                        "sv_c": decode_tenths_signed(sv)}
            return values
        if cfg.device == "azbil":
            from ._vendor.azbil_mfc.registers import RAMAddress

            values = {}
            for address in cfg.addresses:
                sp, pv = self.device.read_registers(address, RAMAddress.SP_CURRENT, 2)
                scale = self.scales[address]
                values[str(address)] = {"pv_lpm": round(scale.raw_to_flow(pv), 4),
                                        "sp_lpm": round(scale.raw_to_flow(sp), 4)}
            return values
        for _ in range(cfg.balance_lines):
            message = self.device.read_message()
            if message is None:
                continue
            if message.kind == "error":
                raise ValueError(f"天平错误帧: {message.raw}")
            if message.kind == "reading" and message.stable is True and message.weight is not None:
                return {"weight_g": message.weight, "raw": message.raw}
        raise TimeoutError("未收到稳定称重帧")

    def write_cycle(self, number: int) -> dict:
        raise PermissionError("真机自动循环写入被禁用")
