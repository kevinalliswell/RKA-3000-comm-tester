from __future__ import annotations

import time
from typing import Iterator

import serial

from .models import BalanceMessage, PortSettings, TareResult
from .protocols.base import BalanceProtocol


class BalanceClient:
    def __init__(
        self,
        port_settings: PortSettings,
        protocol: BalanceProtocol,
        *,
        encoding: str = "utf-8",
        errors: str = "ignore",
    ) -> None:
        self.port_settings = port_settings
        self.protocol = protocol
        self.encoding = encoding
        self.errors = errors
        self._serial: serial.Serial | None = None

    def __enter__(self) -> "BalanceClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        if self.is_open:
            return
        self._serial = serial.Serial(**self.port_settings.to_serial_kwargs())

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def initialize_device(self) -> None:
        for command in self.protocol.startup_commands():
            self.send_bytes(command)

    def send_bytes(self, data: bytes) -> None:
        self._ensure_open()
        assert self._serial is not None
        self._serial.write(data)
        self._serial.flush()

    def send_text(self, data: str) -> None:
        self.send_bytes(data.encode(self.encoding))

    def send_tare(self) -> None:
        self.send_bytes(self.protocol.tare_command())

    def send_tare_and_wait(
        self,
        *,
        timeout: float = 5.0,
        max_messages: int = 50,
        stable_only: bool = True,
        zero_tolerance: float = 0.05,
    ) -> TareResult:
        self.send_tare()

        ack_received = False
        error_received = False
        zero_reading: BalanceMessage | None = None
        observed_messages: list[BalanceMessage] = []

        start = time.monotonic()
        while len(observed_messages) < max_messages:
            if time.monotonic() - start >= timeout:
                break

            message = self.read_message()
            if message is None:
                continue

            observed_messages.append(message)

            if message.kind == "ack":
                ack_received = True
                continue

            if message.kind == "error":
                error_received = True
                break

            if message.kind != "reading":
                continue

            if stable_only and message.stable is not True:
                continue

            if message.weight is not None and abs(message.weight) <= zero_tolerance:
                zero_reading = message
                break

        return TareResult(
            protocol=self.protocol.name,
            ack_received=ack_received,
            error_received=error_received,
            zero_reading=zero_reading,
            observed_messages=tuple(observed_messages),
        )

    def read_raw_line(self) -> str | None:
        self._ensure_open()
        assert self._serial is not None
        raw_bytes = self._serial.readline()
        if not raw_bytes:
            return None
        return raw_bytes.decode(self.encoding, errors=self.errors).strip()

    def read_message(self) -> BalanceMessage | None:
        raw = self.read_raw_line()
        if raw is None:
            return None
        return self.protocol.parse_message(raw)

    def read_reading(self, *, stable_only: bool = True) -> BalanceMessage | None:
        message = self.read_message()
        if message is None:
            return None
        if message.kind != "reading":
            return None
        if stable_only and message.stable is False:
            return None
        return message

    def iter_messages(
        self,
        *,
        duration: float | None = None,
        max_messages: int | None = None,
    ) -> Iterator[BalanceMessage]:
        start = time.monotonic()
        count = 0
        while True:
            if duration is not None and (time.monotonic() - start) >= duration:
                break
            if max_messages is not None and count >= max_messages:
                break

            message = self.read_message()
            if message is None:
                continue

            count += 1
            yield message

    def iter_readings(
        self,
        *,
        duration: float | None = None,
        max_messages: int | None = None,
        stable_only: bool = True,
    ) -> Iterator[BalanceMessage]:
        start = time.monotonic()
        count = 0
        while True:
            if duration is not None and (time.monotonic() - start) >= duration:
                break
            if max_messages is not None and count >= max_messages:
                break

            message = self.read_reading(stable_only=stable_only)
            if message is None:
                continue

            count += 1
            yield message

    def _ensure_open(self) -> None:
        if not self.is_open:
            raise RuntimeError("Serial port is not open")
