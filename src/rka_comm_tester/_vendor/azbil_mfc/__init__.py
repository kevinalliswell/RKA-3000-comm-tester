"""Trimmed vendored copy of azbil_mfc (core CPL driver only).

Upstream: github.com/kevinalliswell/azbil-mfc @83ddc491 (MIT).
Dashboard/mixer/recipe/scheduler/safety modules were intentionally not vendored.
"""
from .bus import RS485Bus
from .client import MQVBusManager, MQVDevice, MQVStatus
from .constants import (
    COMM_8E1,
    COMM_8N2,
    DEFAULT_BAUDRATE,
    FULL_SCALE_COUNTS,
    MODE_CLOSE,
    MODE_CONTROL,
    MODE_OPEN,
)
from .exceptions import (
    CPLChecksumError,
    CPLConnectionError,
    CPLDeviceError,
    CPLError,
    CPLFrameError,
    CPLTimeoutError,
)
from .protocol import build_read_frame, build_write_frame, calculate_checksum, parse_response
from .registers import EEPROMAddress, RAMAddress

__all__ = [
    "RS485Bus",
    "MQVBusManager",
    "MQVDevice",
    "MQVStatus",
    "RAMAddress",
    "EEPROMAddress",
    "build_read_frame",
    "build_write_frame",
    "parse_response",
    "calculate_checksum",
    "CPLError",
    "CPLChecksumError",
    "CPLConnectionError",
    "CPLDeviceError",
    "CPLFrameError",
    "CPLTimeoutError",
    "COMM_8E1",
    "COMM_8N2",
    "DEFAULT_BAUDRATE",
    "MODE_CLOSE",
    "MODE_CONTROL",
    "MODE_OPEN",
    "FULL_SCALE_COUNTS",
]
