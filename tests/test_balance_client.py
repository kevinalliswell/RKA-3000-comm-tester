from types import SimpleNamespace

import pytest
from serial import SerialException

from rka_comm_tester._vendor.balance_serial import client as balance_client
from rka_comm_tester._vendor.balance_serial.client import BalanceClient
from rka_comm_tester._vendor.balance_serial.models import PortSettings
from rka_comm_tester._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
from rka_comm_tester.config import ScanConfig
from rka_comm_tester.scanner import scan, tare_test


class _TimedSerial:
    is_open = True

    def __init__(self, clock):
        self.clock = clock
        self.timeout = 10.0
        self.data = bytearray()
        self.byte_delay = 0.25
        self.read_timeouts = []
        self.fail = False

    def read(self, size):
        assert size == 1
        self.read_timeouts.append(self.timeout)
        if self.fail:
            raise OSError("serial read failed")
        if self.data and self.byte_delay <= self.timeout:
            self.clock.now += self.byte_delay
            return bytes([self.data.pop(0)])
        self.clock.now += self.timeout
        return b""


@pytest.fixture
def timed_client(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(balance_client, "time", SimpleNamespace(monotonic=lambda: clock.now))
    client = BalanceClient(PortSettings("unused"), GSCpaProtocol())
    serial_port = _TimedSerial(clock)
    client._serial = serial_port
    return client, serial_port, clock


@pytest.mark.parametrize("serial_timeout", [0.2, 10.0, None])
def test_deadline_caps_silent_read_and_restores_serial_timeout(timed_client, serial_timeout):
    client, serial_port, clock = timed_client
    serial_port.timeout = serial_timeout
    assert client.read_message(deadline=1.0) is None
    assert clock.now == min(serial_timeout or 1.0, 1.0)
    assert serial_port.timeout == serial_timeout


def test_continuous_unterminated_data_cannot_extend_deadline(timed_client):
    client, serial_port, clock = timed_client
    serial_port.data = bytearray(b"x" * 100)
    assert client.read_raw_line(deadline=0.9) == "xxx"
    assert clock.now == pytest.approx(0.9)
    assert serial_port.read_timeouts == pytest.approx([0.9, 0.65, 0.4, 0.15])
    assert serial_port.timeout == 10.0


def test_complete_frame_is_parsed_before_deadline(timed_client):
    client, serial_port, clock = timed_client
    serial_port.byte_delay = 0.01
    serial_port.data = bytearray(b"+0000.00 G S\r\nnext frame")
    message = client.read_message(deadline=1.0)
    assert (message.kind, message.stable, message.weight) == ("reading", True, 0.0)
    assert clock.now < 1.0
    assert serial_port.data == b"next frame"
    assert serial_port.timeout == 10.0


def test_expired_deadline_does_not_start_another_serial_read(timed_client):
    client, serial_port, _ = timed_client
    assert client.read_message(deadline=0.0) is None
    assert serial_port.read_timeouts == []
    assert serial_port.timeout == 10.0


def test_deadline_read_restores_serial_timeout_after_error(timed_client):
    client, serial_port, _ = timed_client
    serial_port.fail = True
    with pytest.raises(OSError, match="serial read failed"):
        client.read_message(deadline=1.0)
    assert serial_port.timeout == 10.0


class _LinePort:
    """Serves whole readline() results, the way pyserial does on a real port."""

    is_open = True

    def __init__(self, lines=()):
        self.lines = list(lines)
        self.written = bytearray()

    def __call__(self, **kwargs):  # stands in for the serial.Serial constructor
        return self

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def write(self, data):
        self.written.extend(data)

    def flush(self): ...

    def reset_input_buffer(self): ...

    def close(self): ...


def _scan(monkeypatch, lines, **changes):
    port = _LinePort(lines)
    monkeypatch.setattr("serial.Serial", port)
    params = dict(device="balance", port="COM3", balance_lines=3) | changes
    [result] = list(scan(ScanConfig(**params)))
    return result, port


def test_silent_gs_cpa_scan_points_at_o1_and_stays_listen_only(monkeypatch):
    result, port = _scan(monkeypatch, [])
    assert result.status == "offline"
    assert result.values["bytes_received"] == 0
    assert "勾选「发送 O1」" in result.detail
    assert not port.written


def test_silent_after_o1_does_not_repeat_that_advice(monkeypatch):
    result, port = _scan(monkeypatch, [], balance_start_output=True)
    assert result.status == "offline"
    assert "已发送 O1" in result.detail and "勾选" not in result.detail
    assert bytes(port.written) == b"O1\r\nO0\r\n"


def test_silent_longteng_points_at_the_balance_not_at_o1(monkeypatch):
    result, port = _scan(monkeypatch, [], balance_protocol="longteng")
    assert result.status == "offline"
    assert "龙腾" in result.detail and "O1" not in result.detail
    assert not port.written


def test_bytes_erased_by_decoding_are_not_reported_as_silence(monkeypatch):
    # Wrong baud rate: nothing here survives decode(errors="ignore").
    result, _ = _scan(monkeypatch, [b"\xff\xfe\x80", b"\r\n"])
    assert result.status == "degraded"
    assert result.values["bytes_received"] == 5
    assert result.values["last_rx_hex"] == "0d 0a"
    assert "波特率" in result.detail


def test_parseable_frames_keep_their_own_result(monkeypatch):
    result, _ = _scan(monkeypatch, [b"+0277.19 G U\r\n"])
    assert (result.status, result.detail) == ("degraded", "未收到稳定称重帧")
    assert result.values == {"observed": ["+0277.19 G U"]}


def test_traffic_is_logged_in_both_directions(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="rka_comm_tester._vendor.balance_serial.client")
    _scan(monkeypatch, [b"+1 G S\r\n"], balance_start_output=True)
    assert "发送: 4f 31 0d 0a" in caplog.text
    assert "接收: 2b 31 20 47 20 53 0d 0a" in caplog.text


def test_tare_silence_counts_only_bytes_after_the_command(monkeypatch):
    port = _LinePort([b"+0012.50 G S\r\n"])
    monkeypatch.setattr("serial.Serial", port)
    config = ScanConfig(device="balance", port="COM3", balance_lines=1, tare_timeout_s=1,
                        timeout_s=0.05)
    clock = SimpleNamespace(now=0.0)

    def monotonic():
        clock.now += 0.3
        return clock.now

    monkeypatch.setattr("rka_comm_tester.scanner.time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(balance_client, "time", SimpleNamespace(monotonic=monotonic))
    port.read = lambda size: b""
    port.timeout = 0.05
    [result] = list(tare_test(config))
    assert result.status == "offline"
    assert result.values["bytes_received"] == 0
    assert result.detail.startswith("已发送去皮命令。")


def test_port_held_by_another_program_says_so(monkeypatch):
    def refuse(**kwargs):
        raise SerialException("could not open port 'COM3': PermissionError(13, '拒绝访问。', None, 5)")

    monkeypatch.setattr("serial.Serial", refuse)
    [result] = list(scan(ScanConfig(device="balance", port="COM3")))
    assert result.status == "error"
    assert "被其他程序占用" in result.detail
    assert "PermissionError" in result.detail
