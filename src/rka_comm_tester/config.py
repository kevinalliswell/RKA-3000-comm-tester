"""Validated, user-editable parameters for one diagnostic scan."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScanConfig:
    device: str
    port: str
    baudrate: int = 19200
    parity: str = "N"
    stopbits: float = 1
    timeout_s: float = 0.5
    start_address: int = 1
    end_address: int = 6
    balance_protocol: str = "gs_cpa"
    balance_lines: int = 10
    balance_start_output: bool = False
    temp_sv_min_c: float = 0.0
    temp_sv_max_c: float = 1000.0

    def __post_init__(self) -> None:
        if self.device not in {"taie", "azbil", "balance"}:
            raise ValueError("device must be taie, azbil or balance")
        if not self.port.strip():
            raise ValueError("请选择串口")
        if type(self.baudrate) is not int or not 300 <= self.baudrate <= 921600:
            raise ValueError("波特率须在 300–921600 之间")
        if self.parity not in {"N", "E", "O"} or self.stopbits not in {1, 2}:
            raise ValueError("校验位须为 N/E/O，停止位须为 1/2")
        if not math.isfinite(self.timeout_s) or not 0.05 <= self.timeout_s <= 10:
            raise ValueError("超时须在 0.05–10 秒之间")
        if type(self.start_address) is not int or type(self.end_address) is not int:
            raise ValueError("地址须为整数")
        maximum = 127 if self.device == "azbil" else 247
        if not 1 <= self.start_address <= self.end_address <= maximum:
            raise ValueError(f"地址范围须在 1–{maximum} 内且起点不大于终点")
        if self.device == "azbil" and (self.parity, self.stopbits) not in {("E", 1), ("N", 2)}:
            raise ValueError("Azbil CPL 串口格式仅支持 8E1 或 8N2")
        if self.balance_protocol not in {"gs_cpa", "longteng"}:
            raise ValueError("不支持的天平协议")
        if type(self.balance_lines) is not int or not 1 <= self.balance_lines <= 100:
            raise ValueError("天平读取帧数须在 1–100 之间")
        if type(self.balance_start_output) is not bool:
            raise ValueError("天平连续输出选项须为布尔值")
        if (not math.isfinite(self.temp_sv_min_c) or not math.isfinite(self.temp_sv_max_c)
                or self.temp_sv_min_c >= self.temp_sv_max_c
                or self.temp_sv_min_c < -1000 or self.temp_sv_max_c > 3000):
            raise ValueError("温控写入上下限无效")

    @property
    def addresses(self) -> range:
        return range(self.start_address, self.end_address + 1)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path) -> ScanConfig:
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
