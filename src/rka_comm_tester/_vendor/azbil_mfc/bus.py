"""RS-485 bus manager for Azbil MQV devices."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional

import serial

from .constants import COMM_8E1, DEFAULT_BAUDRATE, DEFAULT_TIMEOUT, DEFAULT_WRITE_TIMEOUT
from .exceptions import CPLConnectionError, CPLFrameError, CPLTimeoutError
from .protocol import build_read_frame, build_write_frame, parse_response
from .registers import RAMAddress

logger = logging.getLogger(__name__)

DEFAULT_COMMAND_RETRIES = 1
DEFAULT_RETRY_DELAY_S = 0.05
DEFAULT_MIN_COMMAND_INTERVAL_S = 0.02
DEFAULT_BACKGROUND_COOLDOWN_S = 0.15


class RS485Bus:
    """Thread-safe RS-485 bus manager."""

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        comm_format: int = COMM_8E1,
        timeout: float = DEFAULT_TIMEOUT,
        command_retries: int = DEFAULT_COMMAND_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY_S,
        min_command_interval: float = DEFAULT_MIN_COMMAND_INTERVAL_S,
        background_cooldown: float = DEFAULT_BACKGROUND_COOLDOWN_S,
    ):
        self.port = port
        self.baudrate = baudrate
        self.comm_format = comm_format
        self.timeout = timeout
        self.command_retries = max(0, int(command_retries))
        self.retry_delay = max(0.0, retry_delay)
        self.min_command_interval = max(0.0, min_command_interval)
        self.background_cooldown = max(0.0, background_cooldown)
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._last_io_at = 0.0
        self._transaction_depth = 0
        self._background_pause_until = 0.0

    def open(self) -> None:
        if self._serial and self._serial.is_open:
            return

        if self.comm_format == COMM_8E1:
            parity = serial.PARITY_EVEN
            stopbits = serial.STOPBITS_ONE
        else:
            parity = serial.PARITY_NONE
            stopbits = serial.STOPBITS_TWO

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=parity,
                stopbits=stopbits,
                timeout=self.timeout,
                write_timeout=DEFAULT_WRITE_TIMEOUT,
            )
            logger.info(
                "串口已打开: %s @ %d bps, %s",
                self.port,
                self.baudrate,
                "8E1" if self.comm_format == COMM_8E1 else "8N2",
            )
        except serial.SerialException as exc:
            raise CPLConnectionError(f"无法打开串口 {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("串口已关闭: %s", self.port)

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def transaction_active(self) -> bool:
        with self._state_lock:
            return self._transaction_depth > 0

    @property
    def background_io_allowed(self) -> bool:
        with self._state_lock:
            return self._transaction_depth == 0 and time.monotonic() >= self._background_pause_until

    def pause_background_io(self, duration_s: float) -> None:
        if duration_s <= 0:
            return

        with self._state_lock:
            self._background_pause_until = max(
                self._background_pause_until,
                time.monotonic() + duration_s,
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the bus lock across a multi-step device transaction."""

        with self._state_lock:
            self._transaction_depth += 1

        try:
            with self._lock:
                yield
        finally:
            with self._state_lock:
                self._transaction_depth -= 1
                self._background_pause_until = max(
                    self._background_pause_until,
                    time.monotonic() + self.background_cooldown,
                )

    def _wait_for_turnaround_unlocked(self) -> None:
        if self.min_command_interval <= 0:
            return

        elapsed = time.monotonic() - self._last_io_at
        if elapsed < self.min_command_interval:
            time.sleep(self.min_command_interval - elapsed)

    def _reset_buffers_unlocked(self) -> None:
        self._serial.reset_input_buffer()
        try:
            self._serial.reset_output_buffer()
        except AttributeError:
            pass

    def _send_and_receive_unlocked(self, frame: bytes) -> bytes:
        if not self.is_open:
            raise CPLConnectionError("串口未打开，请先调用 open()")

        self._wait_for_turnaround_unlocked()
        self._reset_buffers_unlocked()
        logger.debug("发送: %s", frame.hex(" "))
        self._serial.write(frame)
        self._serial.flush()

        response = self._serial.read_until(b"\n")
        self._last_io_at = time.monotonic()
        if not response or not response.endswith(b"\n"):
            raise CPLTimeoutError("接收应答超时")

        logger.debug("接收: %s", response.hex(" "))
        return response

    def _exchange(self, frame: bytes, *, retries: int | None = None) -> tuple[int, str, Optional[List[int]]]:
        last_exc: Exception | None = None
        max_retries = self.command_retries if retries is None else retries

        with self._lock:
            for attempt in range(max_retries + 1):
                try:
                    response = self._send_and_receive_unlocked(frame)
                    return parse_response(response)
                except (CPLTimeoutError, CPLFrameError) as exc:
                    last_exc = exc

                if attempt >= max_retries:
                    assert last_exc is not None
                    raise last_exc

                logger.debug(
                    "RS-485 通信失败，准备重试 (%d/%d): %s",
                    attempt + 1,
                    max_retries + 1,
                    last_exc,
                )
                self._reset_buffers_unlocked()
                self._last_io_at = time.monotonic()
                self.pause_background_io(self.retry_delay + self.background_cooldown)
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)

        assert last_exc is not None
        raise last_exc

    def read_registers(
        self,
        address: int,
        reg_address: int,
        word_count: int = 1,
    ) -> List[int]:
        frame = build_read_frame(address, reg_address, word_count)
        response_address, _, values = self._exchange(frame)
        if response_address != address or values is None or len(values) != word_count:
            raise CPLFrameError(f"unexpected MFC read response from {response_address}: {values}")
        return values

    def write_registers(
        self,
        address: int,
        reg_address: int,
        values: List[int],
    ) -> None:
        frame = build_write_frame(address, reg_address, values)
        response_address, _, response_values = self._exchange(frame, retries=0)
        if response_address != address or response_values:
            raise CPLFrameError(f"unexpected MFC write response from {response_address}")
        self.pause_background_io(self.background_cooldown)

    def scan_devices(
        self,
        start: int = 1,
        end: int = 127,
        retries: int = 3,
        probe_delay: float = 0.1,
        scan_timeout: float = 0.5,
    ) -> List[int]:
        """Scan bus addresses using a paced, retry-based probe.

        Real MQV devices can miss back-to-back probes even when wiring is fine.
        The scan therefore retries each address and inserts a short inter-probe
        delay, which improves stability on real hardware.
        """
        if not self.is_open:
            raise CPLConnectionError("串口未打开，请先调用 open()")

        found = []
        original_timeout = self._serial.timeout
        self._serial.timeout = scan_timeout

        try:
            for addr in range(start, end + 1):
                online = False
                for attempt in range(retries):
                    try:
                        self.read_registers(addr, RAMAddress.PV_CURRENT, 1)
                        found.append(addr)
                        logger.info("发现设备: 地址 %d (attempt %d/%d)", addr, attempt + 1, retries)
                        online = True
                        break
                    except (CPLTimeoutError, CPLFrameError, CPLConnectionError) as exc:
                        logger.debug(
                            "扫描地址 %d 失败 (attempt %d/%d): %s",
                            addr,
                            attempt + 1,
                            retries,
                            exc,
                        )
                        if attempt + 1 < retries and probe_delay > 0:
                            time.sleep(probe_delay)

                if probe_delay > 0 and (online or addr < end):
                    time.sleep(probe_delay)
        finally:
            self._serial.timeout = original_timeout

        return found

    def __enter__(self) -> "RS485Bus":
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()
