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


def test_silent_longteng_was_asked_for_readings_and_names_its_settings(monkeypatch):
    result, port = _scan(monkeypatch, [], balance_protocol="longteng")
    assert result.status == "offline"
    assert "打印命令 P" in result.detail and "2400" in result.detail and "O1" not in result.detail
    assert bytes(port.written) == b"P\r\n" * 4  # once up front, then after each silent read


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


class _JdBalance(_LinePort):
    """Longteng JD balance in its factory command-output mode (C5-2), per the manual and
    the 2021 debug records: echo every command, answer P with one reading, T zeroes."""

    def __init__(self, weight=b"+00100.001  g"):
        super().__init__()
        self.weight = weight
        self.buffer = bytearray()
        self.timeout = 0.05

    def write(self, data):
        super().write(data)
        command = bytes(data).strip()
        self.buffer += command + b"\r\n"
        if command == b"P":
            self.buffer += self.weight + b"\r\n"
        elif command == b"T":
            self.weight = b"+00000.000  g"

    def read(self, size=1):
        data, self.buffer = bytes(self.buffer[:size]), self.buffer[size:]
        return data

    def readline(self):
        end = self.buffer.find(b"\n")
        return self.read(len(self.buffer) if end < 0 else end + 1)

    def reset_input_buffer(self):
        self.buffer.clear()


def test_command_mode_longteng_is_read_by_asking_for_a_print(monkeypatch):
    port = _JdBalance()
    monkeypatch.setattr("serial.Serial", port)
    [result] = list(scan(ScanConfig(device="balance", port="COM3", baudrate=2400,
                                    balance_protocol="longteng")))
    assert (result.status, result.values["weight_g"]) == ("online", 100.001)
    assert set(bytes(port.written).split(b"\r\n")) <= {b"P", b""}  # never O, C or M


def test_command_mode_longteng_tare_confirms_zero(monkeypatch):
    port = _JdBalance()
    monkeypatch.setattr("serial.Serial", port)
    config = ScanConfig(device="balance", port="COM3", baudrate=2400, balance_protocol="longteng",
                        timeout_s=0.05)
    [result] = list(tare_test(config))
    assert result.status == "online"
    assert result.values["before_g"] == 100.001
    assert (result.values["ack"], result.values["after_g"]) == ("T", 0.0)
    assert set(bytes(port.written).split(b"\r\n")) <= {b"P", b"T", b""}


def test_unstable_and_non_gram_readings_are_not_online(monkeypatch):
    for weight in (b"+00100.001   ", b"+00012.000pcs", b"US,+0001.234  g"):
        port = _JdBalance(weight)
        monkeypatch.setattr("serial.Serial", port)
        [result] = list(scan(ScanConfig(device="balance", port="COM3", balance_protocol="longteng",
                                        balance_lines=4)))
        assert result.status == "degraded"


@pytest.mark.parametrize("picked, frame, advice", [
    ("gs_cpa", b"+00100.001  g\r\n", "改选龙腾协议"),
    ("longteng", b"+0277.19 G S\r\n", "改选GS-CPA协议"),
])
def test_frames_of_the_other_protocol_name_it(monkeypatch, picked, frame, advice):
    result, _ = _scan(monkeypatch, [frame] * 3, balance_protocol=picked)
    assert result.status == "degraded"
    assert advice in result.detail


# 2026-11-11 capture from the RKA-3000 balance in continuous output, byte for byte.
_CAPTURE_2026_11_11 = bytes.fromhex("53542C2D303034352E3537382020670D0A0A")


def test_field_capture_stream_reads_online_without_extra_print_requests(monkeypatch):
    # Start mid-stream on the trailing LF, as a real port opened between frames would.
    stream = b"\n" + _CAPTURE_2026_11_11 * 3
    lines = stream.splitlines(keepends=True)
    result, port = _scan(monkeypatch, lines, baudrate=2400, balance_protocol="longteng",
                         balance_lines=10)
    assert (result.status, result.values["weight_g"]) == ("online", -45.578)
    # Only the request sent on opening; the blank lines of CR LF LF are not silence.
    assert bytes(port.written) == b"P\r\n"


class _RkaContinuous(_JdBalance):
    """RKA-3000 balance in continuous output, replaying the 2026-11-11 15:38 capture:
    P only adds an echo line; the first T echo collided with a frame ("TT,-0045.565  g"),
    output paused, then zero frames followed."""

    def __init__(self):
        super().__init__()
        self.buffer += b"ST,-0045.565  g\r\n\n" * 3

    def write(self, data):
        _LinePort.write(self, data)
        command = bytes(data).strip()
        if command == b"P":
            self.buffer += b"P\r\n\n"
        elif command == b"T":
            self.buffer += b"TT,-0045.565  g\r\n\n" + b"ST,+0000.000  g\r\n\n" * 3


def test_continuous_rka_balance_scan_ignores_print_echo(monkeypatch):
    port = _RkaContinuous()
    monkeypatch.setattr("serial.Serial", port)
    [result] = list(scan(ScanConfig(device="balance", port="COM3", baudrate=2400,
                                    balance_protocol="longteng")))
    assert (result.status, result.values["weight_g"]) == ("online", -45.565)
    assert bytes(port.written) == b"P\r\n"


def test_tare_survives_echo_colliding_with_a_frame(monkeypatch):
    port = _RkaContinuous()
    monkeypatch.setattr("serial.Serial", port)
    config = ScanConfig(device="balance", port="COM3", baudrate=2400, balance_protocol="longteng",
                        timeout_s=0.05)
    [result] = list(tare_test(config))
    assert result.status == "online"
    assert (result.values["before_g"], result.values["after_g"]) == (-45.565, 0.0)
    assert "ack" not in result.values  # the only T echo arrived glued to a frame
    assert bytes(port.written) == b"P\r\nT\r\n"
