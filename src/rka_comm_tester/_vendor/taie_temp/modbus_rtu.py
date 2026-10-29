"""Minimal TAIE Modbus RTU client for FA/FY temperature controllers.

This module is generated for project bootstrap and intentionally stays small:
- CRC16 (Modbus) frame helpers
- read holding registers
- write single register
- write multiple registers
- symbol-to-address lookups from ``taie_register_addresses.py``

The controller manual says this device supports function codes 03 / 06 / 10 and
allows reading or writing up to 8 registers per request.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Iterable

import serial

from .register_addresses import TAIE_REGISTER_ADDRESS_HEX


MODBUS_MAX_REGISTERS_PER_REQUEST = 8
MODBUS_EXCEPTION_CODES = {
    0x01: "Illegal function",
    0x02: "Illegal data address",
    0x03: "Illegal data value",
}


class ModbusError(Exception):
    """Base Modbus RTU error."""


class ModbusTimeoutError(ModbusError):
    """Raised when the serial port times out while reading a frame."""


class ModbusProtocolError(ModbusError):
    """Raised when the response frame is malformed."""


class ModbusExceptionResponse(ModbusError):
    """Raised when the slave returns a Modbus exception response."""

    def __init__(self, function_code: int, exception_code: int) -> None:
        self.function_code = function_code
        self.exception_code = exception_code
        meaning = MODBUS_EXCEPTION_CODES.get(exception_code, "Unknown exception")
        super().__init__(
            f"Slave returned exception 0x{exception_code:02X} for function "
            f"0x{function_code:02X}: {meaning}"
        )


@dataclass(frozen=True, slots=True)
class SerialSettings:
    port: str
    baudrate: int = 19200
    timeout: float = 1.0
    bytesize: int = serial.EIGHTBITS
    parity: str = serial.PARITY_NONE
    stopbits: float = serial.STOPBITS_ONE
    dtr: bool = False
    rts: bool = False
    open_settle_delay: float = 2.0
    post_write_delay: float = 0.2
    inter_request_delay: float = 0.3
    read_retry_attempts: int = 2


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + crc.to_bytes(2, byteorder="little")


def validate_crc(frame: bytes) -> None:
    if len(frame) < 4:
        raise ModbusProtocolError("Frame too short to validate CRC")
    expected = int.from_bytes(frame[-2:], byteorder="little")
    actual = crc16_modbus(frame[:-2])
    if expected != actual:
        raise ModbusProtocolError(
            f"CRC mismatch: expected 0x{expected:04X}, calculated 0x{actual:04X}"
        )


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized not in TAIE_REGISTER_ADDRESS_HEX:
        raise KeyError(f"Unknown register symbol: {symbol}")
    return normalized


def address_from_symbol(symbol: str) -> int:
    normalized = normalize_symbol(symbol)
    return int(TAIE_REGISTER_ADDRESS_HEX[normalized], 16)


def to_signed_16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def decode_tenths_signed(value: int) -> float:
    return to_signed_16(value) / 10.0


class TaieModbusRtuClient:
    def __init__(self, slave_id: int, settings: SerialSettings) -> None:
        if not 1 <= slave_id <= 255:
            raise ValueError("slave_id must be between 1 and 255")
        self.slave_id = slave_id
        self.settings = settings
        self._serial: serial.Serial | None = None
        self._last_transaction_completed_at: float | None = None

    def __enter__(self) -> "TaieModbusRtuClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def serial_port(self) -> serial.Serial:
        if self._serial is None or not self._serial.is_open:
            raise ModbusError("Serial port is not open")
        return self._serial

    def open(self) -> None:
        if self._serial is not None and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.settings.port,
            baudrate=self.settings.baudrate,
            timeout=self.settings.timeout,
            write_timeout=self.settings.timeout,
            bytesize=self.settings.bytesize,
            parity=self.settings.parity,
            stopbits=self.settings.stopbits,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._serial.dtr = self.settings.dtr
        self._serial.rts = self.settings.rts
        if self.settings.open_settle_delay > 0:
            time.sleep(self.settings.open_settle_delay)

    def close(self) -> None:
        if self._serial is not None and self._serial.is_open:
            self._serial.close()

    def _read_exact(self, size: int) -> bytes:
        deadline = time.monotonic() + self.settings.timeout
        chunks = bytearray()
        while len(chunks) < size and time.monotonic() < deadline:
            chunk = self.serial_port.read(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) != size:
            raise ModbusTimeoutError(f"Timed out while reading {size} bytes, got {len(chunks)} bytes")
        return bytes(chunks)

    def _transceive_once(self, request_frame: bytes, expected_function_code: int) -> bytes:
        port = self.serial_port
        if self._last_transaction_completed_at is not None:
            elapsed = time.monotonic() - self._last_transaction_completed_at
            remaining = self.settings.inter_request_delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        port.reset_input_buffer()
        port.write(request_frame)
        port.flush()
        if self.settings.post_write_delay > 0:
            time.sleep(self.settings.post_write_delay)

        head = self._read_exact(2)
        slave_id, function_code = head[0], head[1]
        if slave_id != self.slave_id:
            raise ModbusProtocolError(
                f"Unexpected slave id 0x{slave_id:02X}, expected 0x{self.slave_id:02X}"
            )

        if function_code == (expected_function_code | 0x80):
            tail = self._read_exact(3)
            frame = head + tail
            validate_crc(frame)
            self._last_transaction_completed_at = time.monotonic()
            raise ModbusExceptionResponse(expected_function_code, tail[0])

        if function_code != expected_function_code:
            raise ModbusProtocolError(
                f"Unexpected function code 0x{function_code:02X}, "
                f"expected 0x{expected_function_code:02X}"
            )

        if expected_function_code == 0x03:
            byte_count = self._read_exact(1)
            if not 0 < byte_count[0] <= 2 * MODBUS_MAX_REGISTERS_PER_REQUEST or byte_count[0] % 2:
                raise ModbusProtocolError(f"Invalid register byte count: {byte_count[0]}")
            data_and_crc = self._read_exact(byte_count[0] + 2)
            frame = head + byte_count + data_and_crc
        elif expected_function_code in (0x06, 0x10):
            frame = head + self._read_exact(6)
        else:
            raise ModbusProtocolError(
                f"Unsupported function code 0x{expected_function_code:02X}"
            )

        validate_crc(frame)
        if expected_function_code in (0x06, 0x10) and frame[2:6] != request_frame[2:6]:
            raise ModbusProtocolError("Write response does not echo requested address and value/count")
        self._last_transaction_completed_at = time.monotonic()
        return frame

    def _transceive(self, request_frame: bytes, expected_function_code: int) -> bytes:
        attempts = 1
        if expected_function_code == 0x03:
            attempts += max(self.settings.read_retry_attempts, 0)

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._transceive_once(request_frame, expected_function_code)
            except ModbusTimeoutError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                self.close()
                self.open()

        assert last_error is not None
        raise last_error

    def read_holding_registers(
        self, start_address: int, quantity: int = 1
    ) -> tuple[int, ...]:
        if not 1 <= quantity <= MODBUS_MAX_REGISTERS_PER_REQUEST:
            raise ValueError(
                f"quantity must be between 1 and {MODBUS_MAX_REGISTERS_PER_REQUEST}"
            )
        payload = bytes(
            [
                self.slave_id,
                0x03,
                (start_address >> 8) & 0xFF,
                start_address & 0xFF,
                (quantity >> 8) & 0xFF,
                quantity & 0xFF,
            ]
        )
        frame = self._transceive(append_crc(payload), expected_function_code=0x03)
        byte_count = frame[2]
        if byte_count != quantity * 2:
            raise ModbusProtocolError(f"Expected {quantity * 2} data bytes, got {byte_count}")
        data = frame[3 : 3 + byte_count]
        return tuple(
            int.from_bytes(data[index : index + 2], byteorder="big")
            for index in range(0, len(data), 2)
        )

    def write_single_register(self, address: int, value: int) -> int:
        payload = bytes(
            [
                self.slave_id,
                0x06,
                (address >> 8) & 0xFF,
                address & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ]
        )
        frame = self._transceive(append_crc(payload), expected_function_code=0x06)
        echoed_value = int.from_bytes(frame[4:6], byteorder="big")
        return echoed_value

    def write_multiple_registers(self, start_address: int, values: Iterable[int]) -> int:
        value_list = list(values)
        quantity = len(value_list)
        if not 1 <= quantity <= MODBUS_MAX_REGISTERS_PER_REQUEST:
            raise ValueError(
                f"quantity must be between 1 and {MODBUS_MAX_REGISTERS_PER_REQUEST}"
            )
        data = b"".join(value.to_bytes(2, byteorder="big") for value in value_list)
        payload = bytes(
            [
                self.slave_id,
                0x10,
                (start_address >> 8) & 0xFF,
                start_address & 0xFF,
                (quantity >> 8) & 0xFF,
                quantity & 0xFF,
                len(data),
            ]
        ) + data
        frame = self._transceive(append_crc(payload), expected_function_code=0x10)
        written_count = int.from_bytes(frame[4:6], byteorder="big")
        return written_count

    def read_by_symbol(self, symbol: str, quantity: int = 1) -> tuple[int, ...]:
        return self.read_holding_registers(address_from_symbol(symbol), quantity)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAIE Modbus RTU helper")
    parser.add_argument("--port", required=True, help="Serial port, for example COM8")
    parser.add_argument("--slave", required=True, type=int, help="Slave ID, 1-255")
    parser.add_argument("--baudrate", type=int, default=19200)
    parser.add_argument("--timeout", type=float, default=1.0)

    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="Read holding registers")
    read_group = read_parser.add_mutually_exclusive_group(required=True)
    read_group.add_argument("--symbol", help="Register symbol, for example PV")
    read_group.add_argument("--address", help="Register address, hex like 008A or 0x008A")
    read_parser.add_argument("--quantity", type=int, default=1)
    read_parser.add_argument(
        "--decode-tenths",
        action="store_true",
        help="Decode the first returned register as signed fixed-point /10",
    )

    return parser


def parse_address(address_text: str) -> int:
    return int(address_text.replace("H", "").replace("h", ""), 16)


def run_cli() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = SerialSettings(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
    )

    with TaieModbusRtuClient(slave_id=args.slave, settings=settings) as client:
        if args.command == "read":
            if args.symbol:
                start_address = address_from_symbol(args.symbol)
                values = client.read_by_symbol(args.symbol, quantity=args.quantity)
                symbol = normalize_symbol(args.symbol)
                print(f"symbol={symbol}")
            else:
                start_address = parse_address(args.address)
                values = client.read_holding_registers(start_address, quantity=args.quantity)
                print("symbol=<direct-address>")

            print(f"address_hex={start_address:04X}")
            print(f"address_dec={start_address}")
            print("values_raw=" + ",".join(f"0x{value:04X}" for value in values))
            print("values_dec=" + ",".join(str(value) for value in values))
            if args.decode_tenths and values:
                print(f"value_tenths={decode_tenths_signed(values[0]):.1f}")
            return 0

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
