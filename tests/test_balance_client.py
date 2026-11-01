from types import SimpleNamespace

import pytest

from rka_comm_tester._vendor.balance_serial import client as balance_client
from rka_comm_tester._vendor.balance_serial.client import BalanceClient
from rka_comm_tester._vendor.balance_serial.models import PortSettings
from rka_comm_tester._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol


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
