"""Azbil MQV CPL 协议常量定义."""

# CPL 帧定界符
STX = 0x02  # 帧起始
ETX = 0x03  # 帧结束
CR = 0x0D
LF = 0x0A

# 命令标识符
CMD_MARKER_UPPER = 0x58  # 'X' - 大写命令标识
CMD_MARKER_LOWER = 0x78  # 'x' - 小写命令标识

# 命令类型
CMD_READ = "RD"   # 读取数据
CMD_WRITE = "WD"  # 写入数据

# 子地址（固定为 "00"）
SUB_ADDRESS = "00"

# 波特率选项 (功能设定 C-31)
BAUDRATE_38400 = 0   # 38400 bps
BAUDRATE_19200 = 1   # 19200 bps
BAUDRATE_9600 = 2    # 9600 bps
BAUDRATE_4800 = 3    # 4800 bps
BAUDRATE_2400 = 4    # 2400 bps

BAUDRATE_MAP = {
    0: 38400,
    1: 19200,
    2: 9600,
    3: 4800,
    4: 2400,
}

# 通讯条件选项 (功能设定 C-32)
COMM_8E1 = 0  # 8位数据, 偶校验, 1停止位
COMM_8N2 = 1  # 8位数据, 无校验, 2停止位

# 设备地址范围
ADDRESS_MIN = 1
ADDRESS_MAX = 127

# 默认通讯参数
DEFAULT_BAUDRATE = 19200
DEFAULT_TIMEOUT = 1.0  # 秒
DEFAULT_WRITE_TIMEOUT = 1.0  # 秒

# 应答错误码
ERROR_OK = "00"  # 正常

# 气体种类
GAS_TYPE = {
    0: "用户设定 C.F.",
    1: "空气/氮气",
    2: "氧气",
    3: "氢气",
    4: "二氧化碳 (CO2)",
    5: "城市煤气 13A (LNG:46MJ/m³)",
    6: "丙烷 100%",
    7: "甲烷 100%",
    8: "丁烷 100%",
    11: "城市煤气 13A (LNG:45MJ/m³)",
}

# 动作模式
MODE_CLOSE = 0   # 全闭模式 (OFF)
MODE_CONTROL = 1  # 控制模式 (ON)
MODE_OPEN = 2     # 全开模式 (FULL)

# 仿真用默认满量程计数值。
# 真机不再假设固定 50000，而是按设备单位/小数位/满量程换算。
FULL_SCALE_COUNTS = 50000
