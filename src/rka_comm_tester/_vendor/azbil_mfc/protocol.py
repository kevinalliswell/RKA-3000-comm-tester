"""Azbil MQV CPL 协议层 - 帧构建、解析与校验和计算.

CPL 帧格式:
    | STX(02H) | 机器地址(2B) | 子地址(2B,"00") | 'X'(58H) | 命令/应答数据 | ETX(03H) | 校验和(2B) | CR(0DH) | LF(0AH) |

命令类型:
    RD - 读取: RD + 地址(4 hex) + 字数(4 hex)
    WD - 写入: WD + 地址(4 hex) + 数据(每字4 hex)

校验和计算:
    1. 累加 STX 到 ETX（含）所有字节
    2. 取累加和的低 8 位
    3. 取二进制补码（按位取反 + 1）
    4. 转为 2 字符大写 ASCII HEX
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .constants import CMD_MARKER_UPPER, CMD_READ, CMD_WRITE, CR, ETX, LF, STX, SUB_ADDRESS
from .exceptions import CPLChecksumError, CPLDeviceError, CPLFrameError


def calculate_checksum(data: bytes) -> str:
    """计算 CPL 校验和.

    Args:
        data: STX 到 ETX（含）之间的所有字节

    Returns:
        2 字符大写 ASCII HEX 校验和
    """
    total = sum(data) & 0xFF
    complement = (~total + 1) & 0xFF
    return f"{complement:02X}"


def build_read_frame(address: int, reg_address: int, word_count: int = 1) -> bytes:
    """构建读取命令帧.

    Args:
        address: 设备地址 (1-127)
        reg_address: 寄存器地址
        word_count: 读取字数

    Returns:
        完整的命令帧字节串
    """
    if not 1 <= address <= 127 or not 0 <= reg_address <= 0xFFFF or not 1 <= word_count <= 0xFFFF:
        raise ValueError("invalid CPL address, register, or word count")
    addr_str = f"{address:02X}"
    cmd_data = f"{CMD_READ}{reg_address:04X}{word_count:04X}"
    payload = f"{addr_str}{SUB_ADDRESS}X{cmd_data}"

    frame_body = bytes([STX]) + payload.encode("ascii") + bytes([ETX])
    checksum = calculate_checksum(frame_body)
    return frame_body + checksum.encode("ascii") + bytes([CR, LF])


def build_write_frame(address: int, reg_address: int, values: List[int]) -> bytes:
    """构建写入命令帧.

    Args:
        address: 设备地址 (1-127)
        reg_address: 寄存器地址
        values: 要写入的数据列表（每个值为 16 位无符号整数）

    Returns:
        完整的命令帧字节串
    """
    if (not 1 <= address <= 127 or not 0 <= reg_address <= 0xFFFF
            or not values or any(not 0 <= value <= 0xFFFF for value in values)):
        raise ValueError("invalid CPL write address, register, or data")
    addr_str = f"{address:02X}"
    data_str = "".join(f"{v:04X}" for v in values)
    cmd_data = f"{CMD_WRITE}{reg_address:04X}{data_str}"
    payload = f"{addr_str}{SUB_ADDRESS}X{cmd_data}"

    frame_body = bytes([STX]) + payload.encode("ascii") + bytes([ETX])
    checksum = calculate_checksum(frame_body)
    return frame_body + checksum.encode("ascii") + bytes([CR, LF])


def parse_response(data: bytes) -> Tuple[int, str, Optional[List[int]]]:
    """解析应答帧.

    Args:
        data: 接收到的完整应答帧

    Returns:
        (设备地址, 错误码, 数据列表或None)

    Raises:
        CPLFrameError: 帧格式错误
        CPLChecksumError: 校验和不匹配
        CPLDeviceError: 设备返回非 "00" 错误码
    """
    if len(data) < 11:
        raise CPLFrameError(f"应答帧太短: {len(data)} 字节")

    if data[-2:] != bytes([CR, LF]):
        raise CPLFrameError("应答帧缺少 CRLF 结束符")
    data = data[:-2]

    # 校验和位于 ETX 之后的 2 字节
    etx_pos = data.rfind(bytes([ETX]))
    if etx_pos < 0:
        raise CPLFrameError("应答帧中未找到 ETX")
    if etx_pos != len(data) - 3:
        raise CPLFrameError("ETX 后的校验字段长度错误")

    if data[0] != STX:
        raise CPLFrameError(f"应答帧起始字节错误: 0x{data[0]:02X}")

    frame_body = data[: etx_pos + 1]
    try:
        recv_checksum = data[etx_pos + 1: etx_pos + 3].decode("ascii")
    except UnicodeDecodeError as exc:
        raise CPLFrameError("校验字段不是 ASCII") from exc
    calc_checksum = calculate_checksum(frame_body)

    if recv_checksum.upper() != calc_checksum.upper():
        raise CPLChecksumError(
            f"校验和不匹配: 接收={recv_checksum}, 计算={calc_checksum}"
        )

    # 解析帧内容 (去掉 STX 和 ETX)
    try:
        content = frame_body[1:-1].decode("ascii")
        if len(content) < 7 or content[2:4] != "00" or content[4] != "X":
            raise ValueError("invalid CPL address/subaddress/command header")

    # 机器地址 (2字符)
        device_addr = int(content[0:2], 16)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CPLFrameError(f"应答帧头部无效: {exc}") from exc
    # 子地址 (2字符, 跳过)
    # 'X' 标识 (1字符, 跳过)
    # 应答数据从第 5 字符开始
    response_data = content[5:]

    # 错误码 (前2字符)
    error_code = response_data[0:2]

    if error_code != "00":
        raise CPLDeviceError(error_code)

    # 读取应答包含数据，写入应答不包含
    values = None
    remaining = response_data[2:]
    if remaining:
        if len(remaining) % 4:
            raise CPLFrameError("应答数据长度不是完整寄存器字")
        values = []
        for i in range(0, len(remaining), 4):
            try:
                values.append(int(remaining[i: i + 4], 16))
            except ValueError as exc:
                raise CPLFrameError("应答数据包含非十六进制字符") from exc

    return device_addr, error_code, values


def frame_to_hex_string(frame: bytes) -> str:
    """将帧转换为可读的十六进制字符串（调试用）."""
    return " ".join(f"{b:02X}" for b in frame)
