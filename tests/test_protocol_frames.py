import pytest

from rka_comm_tester._vendor.azbil_mfc.bus import RS485Bus
from rka_comm_tester._vendor.azbil_mfc.client import MQVBusManager
from rka_comm_tester._vendor.azbil_mfc.device_catalog import FlowScale
from rka_comm_tester._vendor.azbil_mfc.exceptions import (
    CPLChecksumError,
    CPLFrameError,
)
from rka_comm_tester._vendor.azbil_mfc.protocol import (
    build_read_frame,
    calculate_checksum,
    parse_response,
)
from rka_comm_tester._vendor.balance_serial.client import BalanceClient
from rka_comm_tester._vendor.balance_serial.models import PortSettings
from rka_comm_tester._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
from rka_comm_tester._vendor.balance_serial.protocols.longteng import LongtengProtocol
from rka_comm_tester._vendor.taie_temp.modbus_rtu import (
    ModbusProtocolError,
    ModbusTimeoutError,
    SerialSettings,
    TaieModbusRtuClient,
    append_crc,
)


def _cpl_reply(address: int, data: str = "00320168") -> bytes:
    body = b"\x02" + f"{address:02X}00X00{data}".encode() + b"\x03"
    return body + calculate_checksum(body).encode() + b"\r\n"


def test_cpl_reference_frame_and_rejects_bad_responses():
    assert build_read_frame(1, 0x04B6, 2).hex(" ").upper() == (
        "02 30 31 30 30 58 52 44 30 34 42 36 30 30 30 32 03 41 45 0D 0A"
    )
    assert parse_response(_cpl_reply(1)) == (1, "00", [0x32, 0x168])
    with pytest.raises(CPLChecksumError):
        parse_response(_cpl_reply(1)[:-4] + b"00\r\n")
    with pytest.raises(CPLFrameError):
        parse_response(_cpl_reply(1)[:-1])
    with pytest.raises(CPLFrameError):
        parse_response(_cpl_reply(1, "003"))


def test_cpl_bus_rejects_wrong_address_and_short_data(monkeypatch):
    bus = RS485Bus("unused")
    monkeypatch.setattr(bus, "_exchange", lambda _frame: (2, "00", [1]))
    with pytest.raises(CPLFrameError):
        bus.read_registers(1, 0x04B6, 1)
    monkeypatch.setattr(bus, "_exchange", lambda _frame: (1, "00", [1]))
    with pytest.raises(CPLFrameError):
        bus.read_registers(1, 0x04B6, 2)


def test_cpl_write_never_retries(monkeypatch):
    bus = RS485Bus("unused", command_retries=3)
    observed = []
    monkeypatch.setattr(bus, "_exchange", lambda _frame, *, retries=None: observed.append(retries) or (1, "00", None))
    bus.write_registers(1, 0x1131, [25])
    assert observed == [0]


def test_forced_live_scale_refresh_does_not_use_old_cache(monkeypatch):
    from rka_comm_tester._vendor.azbil_mfc import client as azbil_client

    manager = MQVBusManager(RS485Bus("unused"))
    manager._flow_scales[1] = FlowScale(500, 3, 1)

    def _offline(_bus, _address):
        raise OSError("scale metadata unavailable")

    monkeypatch.setattr(azbil_client, "read_flow_scale", _offline)
    assert manager.get_flow_scale(1) == FlowScale(500, 3, 1)
    assert manager.get_flow_scale(1, refresh=True) is None


class _FragmentedSerial:
    is_open = True

    def __init__(self, response: bytes):
        self.data = bytearray(response)

    def reset_input_buffer(self): ...

    def write(self, _frame): ...

    def flush(self): ...

    def read(self, _size):
        return bytes([self.data.pop(0)]) if self.data else b""


def _temp_client(response: bytes) -> TaieModbusRtuClient:
    client = TaieModbusRtuClient(
        1, SerialSettings("unused", timeout=0.01, post_write_delay=0,
                          inter_request_delay=0, read_retry_attempts=0),
    )
    client._serial = _FragmentedSerial(response)
    return client


def test_taie_reference_crc_fragmented_response_and_faults():
    assert append_crc(bytes.fromhex("01 03 00 8A 00 01")) == bytes.fromhex("01 03 00 8A 00 01 A5 E0")
    response = bytes.fromhex("01 03 02 03 E8 B8 FA")
    assert _temp_client(response).read_holding_registers(0x008A) == (1000,)
    with pytest.raises(ModbusProtocolError, match="slave id"):
        _temp_client(bytes.fromhex("02 03 02 03 E8 B8 FA")).read_holding_registers(0x008A)
    with pytest.raises(ModbusProtocolError, match="CRC"):
        _temp_client(response[:-1] + b"\x00").read_holding_registers(0x008A)
    with pytest.raises(ModbusTimeoutError):
        _temp_client(response[:3]).read_holding_registers(0x008A)


def test_taie_rejects_wrong_write_echo():
    response = append_crc(bytes.fromhex("01 06 00 01 00 64"))
    with pytest.raises(ModbusProtocolError, match="echo"):
        _temp_client(response).write_single_register(0x0000, 100)


def test_taie_rejects_mismatched_register_count():
    response = append_crc(bytes.fromhex("01 03 04 00 01 00 02"))
    with pytest.raises(ModbusProtocolError, match="Expected 2"):
        _temp_client(response).read_holding_registers(0x008A, 1)


def test_balance_parsers_accept_only_documented_reading_frames():
    gs = GSCpaProtocol()
    assert gs.parse_message("+0277.19 G S").stable is True
    assert gs.parse_message("+0277.19 G U").stable is False
    assert gs.parse_message("A01").kind == "ack"
    assert gs.parse_message("E01").kind == "error"
    assert gs.parse_message("ERROR 277.19").kind == "unknown"
    longteng = LongtengProtocol()
    assert longteng.parse_message("ST,12.345").stable is True
    assert longteng.parse_message("ERR 12.345").kind == "unknown"


def test_balance_tare_requires_stable_zero_not_ack_or_unstable_zero():
    client = BalanceClient(PortSettings("unused"), GSCpaProtocol())
    messages = iter([
        GSCpaProtocol().parse_message("A01"),
        GSCpaProtocol().parse_message("+0000.00 G U"),
        GSCpaProtocol().parse_message("+0000.00 G S"),
    ])
    client.send_tare = lambda: None
    client.read_message = lambda: next(messages)
    result = client.send_tare_and_wait(timeout=0.1)
    assert result.success
    assert result.zero_reading.stable is True
    assert len(result.observed_messages) == 3
