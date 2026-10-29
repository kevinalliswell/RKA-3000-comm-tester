"""High-level TAIE temperature controller driver built on Modbus RTU."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from .modbus_rtu import (
    ModbusTimeoutError,
    SerialSettings,
    TaieModbusRtuClient,
    decode_tenths_signed,
)
from .register_addresses import TAIE_REGISTER_ADDRESS_HEX


PROGRAM_CURVE_SV_SYMBOLS = frozenset(
    symbol for symbol in TAIE_REGISTER_ADDRESS_HEX if symbol.startswith("SV_")
)
PROGRAM_CURVE_OUT_SYMBOLS = frozenset(
    symbol
    for symbol in TAIE_REGISTER_ADDRESS_HEX
    if symbol.startswith("OUT")
    and symbol not in {"OUTL", "OUT%", "OUTY"}
)
TENTHS_SCALED_SYMBOLS = frozenset(
    {
        "PV",
        "SV",
        "OUTL",
        "OUT%",
        "AL1",
        "AL2",
        "AL3",
        "SVOS",
        "PVOS",
        "LSPL",
        "USPL",
        "WAIT",
        "HYSA",
        "HYS1",
        "HYS2",
        "GAP1",
        "GAP2",
        *PROGRAM_CURVE_SV_SYMBOLS,
        *PROGRAM_CURVE_OUT_SYMBOLS,
    }
)

WRITABLE_TENTHS_SYMBOLS = frozenset({"SV", "OUTL"})
OBIT_FLAG_NAMES = {
    15: "display_nnn2",
    14: "display_uuu2",
    13: "display_nnn1",
    12: "display_uuu1",
    11: "display_in2e",
    10: "display_cjce",
    9: "display_adcf",
    8: "display_in1e",
    7: "man_lamp",
    6: "program_lamp",
    5: "alarm3_lamp",
    4: "alarm2_lamp",
    3: "alarm1_lamp",
    2: "at_lamp",
    1: "out2_lamp",
    0: "out1_lamp",
}
PID_SYMBOLS = {
    1: {
        "p": "P1",
        "i": "I1",
        "d": "D1",
        "cycle_time": "CYT1",
        "hysteresis": "HYS1",
        "gap": "GAP1",
        "dead_band": "DB1",
    },
    2: {
        "p": "P2",
        "i": "I2",
        "d": "D2",
        "cycle_time": "CYT2",
        "hysteresis": "HYS2",
        "gap": "GAP2",
    },
}


@dataclass(frozen=True, slots=True)
class ControllerStatusSnapshot:
    pv: float | None
    sv: float | None
    output_percent: float | None
    slave_id: int
    port: str
    read_latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ProgramSegment:
    group: int
    segment: int
    sv: float
    duration_raw: int
    output_limit: float


@dataclass(frozen=True, slots=True)
class PidParameters:
    output_channel: int
    p_raw: int
    i_raw: int
    d_raw: int
    cycle_time_raw: int
    hysteresis_raw: int
    gap_raw: int | None = None
    dead_band_raw: int | None = None


@dataclass(frozen=True, slots=True)
class ProgramRuntimeSnapshot:
    pv: float
    sv: float
    output_percent: float
    program_group: int
    program_segment: int
    remaining_time_raw: int
    obit_raw: int
    obit_flags: dict[str, bool]
    ct_current_raw: int
    slave_id: int
    port: str
    read_latency_ms: float | None = None


class TaieTemperatureController:
    """Business-friendly TAIE controller wrapper.

    Notes:
    - This wrapper uses the register map extracted from the controller manual.
    - Only a small set of high-confidence business methods is exposed by default.
    - Write helpers are limited to known writable tenths-scaled registers.
    """

    def __init__(
        self,
        slave_id: int,
        settings: SerialSettings,
        *,
        read_attempts: int = 5,
    ) -> None:
        self._client = TaieModbusRtuClient(slave_id=slave_id, settings=settings)
        self.slave_id = slave_id
        self.settings = settings
        self.read_attempts = max(read_attempts, 1)

    def __enter__(self) -> "TaieTemperatureController":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        self._client.open()

    def close(self) -> None:
        self._client.close()

    def _read_registers(self, start_address: int, quantity: int) -> tuple[int, ...]:
        self._client.open()
        last_error: Exception | None = None
        for attempt in range(self.read_attempts):
            try:
                return self._client.read_holding_registers(start_address, quantity=quantity)
            except ModbusTimeoutError as exc:
                last_error = exc
                if attempt == self.read_attempts - 1:
                    break
                self._client.close()
                self._client.open()
        assert last_error is not None
        raise last_error

    @staticmethod
    def _curve_symbols(group: int, segment: int) -> tuple[str, str, str]:
        if group not in (1, 2):
            raise ValueError("group must be 1 or 2")
        if not 1 <= segment <= 8:
            raise ValueError("segment must be between 1 and 8")
        if group == 1:
            return (f"SV_{segment}", f"TM_{segment}", f"OUT{segment}")
        suffix = f"{segment}2"
        return (f"SV_{suffix}", f"TM_{suffix}", f"OUT{suffix}")

    @staticmethod
    def _decode_obit_flags(obit_raw: int) -> dict[str, bool]:
        return {
            flag_name: bool((obit_raw >> bit_index) & 0x1)
            for bit_index, flag_name in OBIT_FLAG_NAMES.items()
        }

    def read_raw(self, symbol: str) -> int:
        address = int(TAIE_REGISTER_ADDRESS_HEX[symbol], 16)
        return self._read_registers(address, quantity=1)[0]

    def write_raw(self, symbol: str, value: int) -> int:
        self._client.open()
        address = int(TAIE_REGISTER_ADDRESS_HEX[symbol], 16)
        return self._client.write_single_register(address, value)

    def read_tenths(self, symbol: str) -> float:
        normalized = symbol.strip().upper()
        if normalized not in TENTHS_SCALED_SYMBOLS:
            raise ValueError(f"{normalized} is not configured as a tenths-scaled symbol")
        return decode_tenths_signed(self.read_raw(normalized))

    def write_tenths(self, symbol: str, value: float) -> float:
        normalized = symbol.strip().upper()
        if normalized not in WRITABLE_TENTHS_SYMBOLS:
            raise ValueError(f"{normalized} is not enabled for safe tenths writes")
        raw_value = int(round(value * 10))
        echoed = self.write_raw(normalized, raw_value)
        return decode_tenths_signed(echoed)

    def write_curve_tenths(self, symbol: str, value: float) -> float:
        normalized = symbol.strip().upper()
        if normalized not in TENTHS_SCALED_SYMBOLS:
            raise ValueError(f"{normalized} is not configured as a tenths-scaled symbol")
        raw_value = int(round(value * 10))
        echoed = self.write_raw(normalized, raw_value)
        return decode_tenths_signed(echoed)

    def read_pv(self) -> float:
        return self.read_tenths("PV")

    def read_sv(self) -> float:
        return self.read_tenths("SV")

    def read_output_percent(self) -> float:
        return self.read_tenths("OUT%")

    def read_output_limit(self) -> float:
        return self.read_tenths("OUTL")

    def write_sv(self, value: float) -> float:
        return self.write_tenths("SV", value)

    def write_output_limit(self, value: float) -> float:
        return self.write_tenths("OUTL", value)

    def read_status(self) -> ControllerStatusSnapshot:
        started_at = monotonic()
        sv_raw = self._read_registers(int(TAIE_REGISTER_ADDRESS_HEX["SV"], 16), quantity=1)[0]
        out_percent_raw, _obit_raw, _cv_raw, pv_raw = self._read_registers(
            int(TAIE_REGISTER_ADDRESS_HEX["OUT%"], 16),
            quantity=4,
        )
        return ControllerStatusSnapshot(
            pv=decode_tenths_signed(pv_raw),
            sv=decode_tenths_signed(sv_raw),
            output_percent=decode_tenths_signed(out_percent_raw),
            slave_id=self.slave_id,
            port=self.settings.port,
            read_latency_ms=(monotonic() - started_at) * 1000.0,
        )

    def read_runtime_snapshot(self) -> ProgramRuntimeSnapshot:
        started_at = monotonic()
        sv_raw = self._read_registers(int(TAIE_REGISTER_ADDRESS_HEX["SV"], 16), quantity=1)[0]
        ptn_raw, seg_raw, timr_raw = self._read_registers(
            int(TAIE_REGISTER_ADDRESS_HEX["PTN"], 16),
            quantity=3,
        )
        out_percent_raw, obit_raw, ct_current_raw, pv_raw = self._read_registers(
            int(TAIE_REGISTER_ADDRESS_HEX["OUT%"], 16),
            quantity=4,
        )
        return ProgramRuntimeSnapshot(
            pv=decode_tenths_signed(pv_raw),
            sv=decode_tenths_signed(sv_raw),
            output_percent=decode_tenths_signed(out_percent_raw),
            program_group=ptn_raw,
            program_segment=seg_raw,
            remaining_time_raw=timr_raw,
            obit_raw=obit_raw,
            obit_flags=self._decode_obit_flags(obit_raw),
            ct_current_raw=ct_current_raw,
            slave_id=self.slave_id,
            port=self.settings.port,
            read_latency_ms=(monotonic() - started_at) * 1000.0,
        )

    def read_program_group(self) -> int:
        return self.read_raw("PTN")

    def write_program_group(self, group: int) -> int:
        if not 0 <= group <= 2:
            raise ValueError("program group must be between 0 and 2")
        return self.write_raw("PTN", group)

    def read_program_segment(self) -> int:
        return self.read_raw("SEG")

    def read_remaining_time_raw(self) -> int:
        return self.read_raw("TIMR")

    def read_program_curve_segment(self, group: int, segment: int) -> ProgramSegment:
        sv_symbol, tm_symbol, out_symbol = self._curve_symbols(group, segment)
        start_address = int(TAIE_REGISTER_ADDRESS_HEX[sv_symbol], 16)
        sv_raw, duration_raw, out_raw = self._read_registers(start_address, quantity=3)
        return ProgramSegment(
            group=group,
            segment=segment,
            sv=decode_tenths_signed(sv_raw),
            duration_raw=duration_raw,
            output_limit=decode_tenths_signed(out_raw),
        )

    def read_program_curve(self, group: int) -> tuple[ProgramSegment, ...]:
        return tuple(
            self.read_program_curve_segment(group=group, segment=segment)
            for segment in range(1, 9)
        )

    def write_program_curve_segment(
        self,
        *,
        group: int,
        segment: int,
        sv: float | None = None,
        duration_raw: int | None = None,
        output_limit: float | None = None,
    ) -> ProgramSegment:
        sv_symbol, tm_symbol, out_symbol = self._curve_symbols(group, segment)
        if sv is not None:
            self.write_curve_tenths(sv_symbol, sv)
        if duration_raw is not None:
            self.write_raw(tm_symbol, duration_raw)
        if output_limit is not None:
            self.write_curve_tenths(out_symbol, output_limit)
        return self.read_program_curve_segment(group=group, segment=segment)

    def read_pid_parameters(self, output_channel: int) -> PidParameters:
        if output_channel not in PID_SYMBOLS:
            raise ValueError("output_channel must be 1 or 2")
        symbols = PID_SYMBOLS[output_channel]
        return PidParameters(
            output_channel=output_channel,
            p_raw=self.read_raw(symbols["p"]),
            i_raw=self.read_raw(symbols["i"]),
            d_raw=self.read_raw(symbols["d"]),
            cycle_time_raw=self.read_raw(symbols["cycle_time"]),
            hysteresis_raw=self.read_raw(symbols["hysteresis"]),
            gap_raw=self.read_raw(symbols["gap"]) if "gap" in symbols else None,
            dead_band_raw=self.read_raw(symbols["dead_band"]) if "dead_band" in symbols else None,
        )

    def write_pid_parameters(
        self,
        *,
        output_channel: int,
        p_raw: int | None = None,
        i_raw: int | None = None,
        d_raw: int | None = None,
        cycle_time_raw: int | None = None,
        hysteresis_raw: int | None = None,
        gap_raw: int | None = None,
        dead_band_raw: int | None = None,
    ) -> PidParameters:
        if output_channel not in PID_SYMBOLS:
            raise ValueError("output_channel must be 1 or 2")
        symbols = PID_SYMBOLS[output_channel]
        updates = {
            symbols["p"]: p_raw,
            symbols["i"]: i_raw,
            symbols["d"]: d_raw,
            symbols["cycle_time"]: cycle_time_raw,
            symbols["hysteresis"]: hysteresis_raw,
        }
        if "gap" in symbols:
            updates[symbols["gap"]] = gap_raw
        if "dead_band" in symbols:
            updates[symbols["dead_band"]] = dead_band_raw
        for symbol, value in updates.items():
            if value is not None:
                self.write_raw(symbol, value)
        return self.read_pid_parameters(output_channel)

    def supports_remote_run_control(self) -> bool:
        return False

    def start_program(self) -> None:
        raise NotImplementedError(
            "The manual used in this workspace does not expose a confirmed Modbus register "
            "for remote start control."
        )

    def stop_program(self) -> None:
        raise NotImplementedError(
            "The manual used in this workspace does not expose a confirmed Modbus register "
            "for remote stop control."
        )

    def hold_program(self) -> None:
        raise NotImplementedError(
            "The manual used in this workspace does not expose a confirmed Modbus register "
            "for remote hold control."
        )


def create_default_controller(
    *,
    port: str,
    slave_id: int,
    baudrate: int = 19200,
    timeout: float = 2.0,
    open_settle_delay: float = 2.0,
    read_attempts: int = 5,
) -> TaieTemperatureController:
    settings = SerialSettings(
        port=port,
        baudrate=baudrate,
        timeout=timeout,
        open_settle_delay=open_settle_delay,
    )
    return TaieTemperatureController(
        slave_id=slave_id,
        settings=settings,
        read_attempts=read_attempts,
    )
