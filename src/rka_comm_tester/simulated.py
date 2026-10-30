"""Frame-level simulated peers for non-destructive throughput testing."""

from __future__ import annotations

from .config import ScanConfig


class SimulatedSource:
    def __init__(self, cfg: ScanConfig) -> None:
        self.cfg = cfg
        self.opened = False
        self.sv_raw = 500
        self.mfc_sp_raw = 100

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def _taie_exchange(self, request: bytes) -> bytes:
        from ._vendor.taie_temp.modbus_rtu import append_crc, validate_crc

        validate_crc(request)
        slave, function = request[0], request[1]
        register = int.from_bytes(request[2:4], "big")
        if function == 3:
            value = self.sv_raw if register == 0x0000 else 600
            return append_crc(bytes([slave, 3, 2]) + value.to_bytes(2, "big"))
        if function == 6 and register == 0x0000:
            self.sv_raw = int.from_bytes(request[4:6], "big")
            return request
        raise ValueError("模拟器不支持的 TAIE 命令")

    def _azbil_exchange(self, request: bytes) -> bytes:
        from ._vendor.azbil_mfc.protocol import calculate_checksum

        if not request.startswith(b"\x02") or not request.endswith(b"\r\n"):
            raise ValueError("CPL 请求帧格式错误")
        body = request[:-4]
        if request[-4:-2].decode("ascii") != calculate_checksum(body):
            raise ValueError("CPL 请求校验错误")
        content = body[1:-1].decode("ascii")
        address = int(content[:2], 16)
        command = content[5:7]
        register = int(content[7:11], 16)
        if command == "RD":
            count = int(content[11:15], 16)
            registers = {0x04B6: self.mfc_sp_raw, 0x04B7: self.mfc_sp_raw,
                         0x1131: self.mfc_sp_raw, 0x03EA: 1000, 0x03EB: 3, 0x03ED: 1}
            values = "".join(f"{registers.get(register + i, 0):04X}" for i in range(count))
        elif command == "WD" and register == 0x1131:
            self.mfc_sp_raw = int(content[11:15], 16)
            values = ""
        else:
            raise ValueError("模拟器不支持的 CPL 命令")
        response = b"\x02" + f"{address:02X}00X00{values}".encode("ascii") + b"\x03"
        return response + calculate_checksum(response).encode("ascii") + b"\r\n"

    def sample(self) -> dict:
        if not self.opened:
            raise ConnectionError("模拟设备未打开")
        cfg = self.cfg
        if cfg.device == "taie":
            from ._vendor.taie_temp.modbus_rtu import append_crc, decode_tenths_signed, validate_crc

            values = {}
            for address in cfg.addresses:
                request = append_crc(bytes([address, 3, 0, 0x8A, 0, 1]))
                response = self._taie_exchange(request)
                validate_crc(response)
                values[str(address)] = {"pv_c": decode_tenths_signed(int.from_bytes(response[3:5], "big"))}
            return values
        if cfg.device == "azbil":
            from ._vendor.azbil_mfc.protocol import build_read_frame, parse_response

            values = {}
            for address in cfg.addresses:
                reply_address, _, raw = parse_response(self._azbil_exchange(build_read_frame(address, 0x04B6, 2)))
                if reply_address != address or raw is None or len(raw) != 2:
                    raise ValueError("CPL 模拟应答错误")
                values[str(address)] = {"sp_raw": raw[0], "pv_raw": raw[1]}
            return values
        from ._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
        from ._vendor.balance_serial.protocols.longteng import LongtengProtocol

        protocol = GSCpaProtocol() if cfg.balance_protocol == "gs_cpa" else LongtengProtocol()
        frame = "+0123.45 G S" if cfg.balance_protocol == "gs_cpa" else "ST,123.45"
        message = protocol.parse_message(frame)
        if message is None or not message.stable:
            raise ValueError("模拟天平帧无效")
        return {"weight_g": message.weight, "raw": message.raw}

    def write_cycle(self, number: int) -> dict:
        if not self.opened:
            raise ConnectionError("模拟设备未打开")
        cfg = self.cfg
        if cfg.device == "taie":
            from ._vendor.taie_temp.modbus_rtu import append_crc, validate_crc

            target = 500 if number % 2 else 1000
            request = append_crc(bytes([cfg.start_address, 6, 0, 0]) + target.to_bytes(2, "big"))
            echo = self._taie_exchange(request)
            validate_crc(echo)
            if echo != request:
                raise ValueError("TAIE 模拟写回显不符")
            read = self._taie_exchange(append_crc(bytes([cfg.start_address, 3, 0, 0, 0, 1])))
            validate_crc(read)
            actual = int.from_bytes(read[3:5], "big")
            if actual != target:
                raise ValueError("TAIE 模拟写回读不符")
            return {"sv_c": actual / 10}
        if cfg.device == "azbil":
            from ._vendor.azbil_mfc.protocol import (
                build_read_frame,
                build_write_frame,
                parse_response,
            )

            target = 100 if number % 2 else 200
            address = cfg.start_address
            reply_address, _, values = parse_response(self._azbil_exchange(build_write_frame(address, 0x1131, [target])))
            if reply_address != address or values:
                raise ValueError("CPL 模拟写应答不符")
            reply_address, _, values = parse_response(self._azbil_exchange(build_read_frame(address, 0x1131, 1)))
            if reply_address != address or values != [target]:
                raise ValueError("CPL 模拟 SP-0 回读不符")
            return {"sp0_raw": target}
        raise PermissionError("天平去皮不参与循环测试")
