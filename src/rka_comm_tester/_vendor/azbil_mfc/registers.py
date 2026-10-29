"""Azbil MQV 寄存器地址定义.

地址分为两类:
- RAM 地址: 运行时数据，掉电丢失
- EEPROM 地址: 持久化存储，需写入确认
"""


class RAMAddress:
    """RAM 寄存器地址（运行时数据）."""

    SP_CURRENT = 0x04B6      # 1206 - 当前使用中的 SP 值 (设定流量)
    PV_CURRENT = 0x04B7      # 1207 - 当前 PV 值 (瞬时流量)
    VALVE_DRIVE = 0x04B8     # 1208 - 阀驱动电流
    OPERATION_MODE = 0x04B4  # 1204 - 动作模式 (0:全闭, 1:控制, 2:全开)
    SP_NUMBER = 0x04B5       # 1205 - 当前 SP 编号
    ALARM_STATUS = 0x04B1    # 1201 - 报警状态
    TOTALIZER_LOW = 0x0643   # 1603 - 累积流量 (低位)
    TOTALIZER_HIGH = 0x0644  # 1604 - 累积流量 (高位)


class EEPROMAddress:
    """EEPROM 寄存器地址（持久化数据）."""

    # SP 设定值 (SP-0 ~ SP-7)
    SP_0 = 0x1131   # 4401 - SP-0 设定流量值
    SP_1 = 0x1132   # 4402 - SP-1 设定流量值
    SP_2 = 0x1133   # 4403 - SP-2 设定流量值
    SP_3 = 0x1134   # 4404 - SP-3 设定流量值
    SP_4 = 0x1135   # 4405 - SP-4 设定流量值
    SP_5 = 0x1136   # 4406 - SP-5 设定流量值
    SP_6 = 0x1137   # 4407 - SP-6 设定流量值
    SP_7 = 0x1138   # 4408 - SP-7 设定流量值

    # 功能设定
    KEY_LOCK = 0x1389       # 5001 - C-01 键锁设定
    RUN_MODE = 0x138A       # 5002 - C-02 RUN 键动作模式
    SP_METHOD = 0x138B      # 5003 - C-03 流量设定方式选择
    SP_COUNT = 0x138C       # 5004 - C-04 SP 数选择
    ANALOG_RANGE = 0x138D   # 5005 - C-05 模拟输入量程选择
    ANALOG_OUTPUT = 0x138E  # 5006 - C-06 模拟输出种类/量程选择
    EVENT1_TYPE = 0x138F    # 5007 - C-07 事件1输出种类分配
    EVENT2_TYPE = 0x1390    # 5008 - C-08 事件2输出种类分配
    MODE_INPUT = 0x1391     # 5009 - C-09 外部3段切换输入功能分配
    DI1_FUNC = 0x1392       # 5010 - C-10 外部接点输入1功能分配
    DI2_FUNC = 0x1393       # 5011 - C-11 外部接点输入2功能分配
    DI3_FUNC = 0x1394       # 5012 - C-12 外部接点输入3功能分配
    AUTO_SHUTOFF = 0x1395   # 5013 - C-13 自动切断功能
    TOTAL_RESET = 0x1396    # 5014 - C-14 累积复位功能
    ALARM_TYPE = 0x1397     # 5015 - C-15 报警设定种类
    ALARM_ACTION = 0x1398   # 5016 - 报警动作选择
    SLOW_START = 0x1399     # 5017 - C-17 慢启动设定
    GAS_TYPE_1 = 0x139A     # 5018 - C-18 气体种类设定1
    TEMP_BASE = 0x139B      # 5019 - C-19 温度基准条件
    VALVE_ALARM = 0x139C    # 5020 - C-20 阀驱动电流报警
    DIRECT_SET = 0x139D     # 5021 - C-21 直接设定功能
    DEAD_ZONE = 0x139E      # 5022 - 控制不感带设定
    PV_FILTER = 0x139F      # 5023 - C-23 PV 滤波
    RANGE_1 = 0x13A0        # 5024 - C-24 流量量程设定1
    RANGE_2 = 0x13A1        # 5025 - C-25 流量量程设定2
    GAS_TYPE_2 = 0x13A2     # 5026 - C-26 气体种类设定2
    SP_RAMP = 0x13A3        # 5027 - C-27 SP 斜坡控制功能
    FREE_SCALE = 0x13A4     # 5028 - C-28 模拟任意定标功能
    PV_ZERO = 0x13A5        # 5029 - C-29 PV 强制置零功能
    COMM_ADDR = 0x13A6      # 5030 - C-30 仪表地址设定 (1~127)
    COMM_BAUD = 0x13A7      # 5031 - C-31 传送速度选择
    COMM_FORMAT = 0x13A8    # 5032 - C-32 通讯条件选择
    SP_LIMIT = 0x13AB       # 5035 - C-35 SP 限幅功能
    DIFF_PRESS = 0x13AC     # 5036 - C-36 动作差压设定
    UNIT_CHANGE = 0x13AD    # 5037 - C-37 流量显示单位变更
    DECIMAL_SHIFT = 0x13AE  # 5038 - C-38 PV 小数点位置变更

    # 参数设定
    FLOW_OK_RANGE = 0x1451    # 5201 - 流量 OK 判定范围
    FLOW_OK_HYST = 0x1452     # 5202 - 流量 OK 判定回差
    ALARM_HI = 0x1453         # 5203 - 流量偏差上限报警
    ALARM_HI_HYST = 0x1454    # 5204 - 流量偏差上限报警回差
    ALARM_LO = 0x1455         # 5205 - 流量偏差下限报警
    ALARM_LO_HYST = 0x1456    # 5206 - 流量偏差下限报警回差
    ALARM_DELAY = 0x1457      # 5207 - 报警延迟时间
    EVENT1_DELAY = 0x1458     # 5208 - 事件输出1延迟时间
    EVENT2_DELAY = 0x1459     # 5209 - 事件输出2延迟时间
    USER_CF = 0x145A          # 5210 - 用户转换系数
    VALVE_ALARM_HI = 0x145B   # 5211 - 阀驱动电流上限报警
    VALVE_ALARM_LO = 0x145C   # 5212 - 阀驱动电流下限报警
    EVENT1_SP_HI = 0x145D     # 5213 - 事件输出1上下限流量设定
    EVENT1_SP_LO = 0x145D     # 兼容旧命名；PDF 中该项为单地址
    EVENT2_SP_HI = 0x145E     # 5214 - 事件输出2上下限流量设定
    EVENT2_SP_LO = 0x145E     # 兼容旧命名；PDF 中该项为单地址
    RAMP_RATE_1 = 0x145F      # 5215 - SP 斜坡控制斜率1
    RAMP_RATE_2 = 0x1460      # 5216 - SP 斜坡控制斜率2
    FREE_SCALE_VAL = 0x1461   # 5217 - 模拟任意定标流量
    TOTAL_EVENT_LO = 0x1462   # 5218 - 累积事件流量 (低4位)
    TOTAL_EVENT_HI = 0x1463   # 5219 - 累积事件流量 (高4位)
    PV_ZERO_DELAY = 0x1464    # 5220 - PV 强制置零延迟时间
    SP_UPPER_LIMIT = 0x1465   # 5221 - SP 上限限幅流量
    SP_LOWER_LIMIT = 0x1466   # 5222 - SP 下限限幅流量
