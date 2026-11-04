"""Azbil C2/C3 probe driven end to end through a simulated instrument on a fake serial port."""

from threading import Event

import pytest

from rka_comm_tester._vendor.azbil_mfc.protocol import calculate_checksum
from rka_comm_tester._vendor.taie_temp.modbus_rtu import append_crc
from rka_comm_tester.config import ScanConfig
from rka_comm_tester.scanner import scan

PV, SP, MV, DECIMALS, INPUT_ALARM, CONTROL = 0x238D, 0x238E, 0x2391, 0x1454, 0x23F1, 0x23F4
HEALTHY = {PV: 0xFFCE, SP: 1205, MV: 455, DECIMALS: 1, INPUT_ALARM: 0, CONTROL: 0}


class Instrument:
    """Answers RD (CPL) or 03H (Modbus RTU) reads; every other frame fails the test."""

    def __init__(self, protocol, registers, *, station=1, denied=()):
        self.protocol = protocol
        self.registers = registers
        self.station = station
        self.denied = set(denied)
        self.requests = []
        self.opened = {}

    def __call__(self, **kwargs):  # stands in for the serial.Serial constructor
        self.opened = kwargs
        self.reply = bytearray()
        self.is_open = True
        return self

    def write(self, frame):
        self.requests.append(bytes(frame))
        self.reply = bytearray(self._cpl(frame) if self.protocol == "cpl" else self._modbus(frame))

    def _modbus(self, frame):
        assert frame == append_crc(frame[:-2]) and frame[1] == 0x03 and frame[4:6] == b"\x00\x01"
        station, register = frame[0], int.from_bytes(frame[2:4], "big")
        if station != self.station:
            return b""
        if register in self.denied:
            return append_crc(bytes([station, 0x83, 0x02]))
        return append_crc(bytes([station, 3, 2]) + self.registers[register].to_bytes(2, "big"))

    def _cpl(self, frame):
        text = frame[1:frame.index(3)].decode("ascii")
        station, command = int(text[:2], 16), text[5:]
        assert text[2:5] == "00X" and command[:2] == "RD" and command[6:] == "0001"
        if station != self.station:
            return b""
        register = int(command[2:6], 16)
        data = "41" if register in self.denied else f"00{self.registers[register]:04X}"
        body = b"\x02" + f"{station:02X}00X{data}".encode() + b"\x03"
        return body + calculate_checksum(body).encode() + b"\r\n"

    def read(self, size=1):
        data, self.reply = bytes(self.reply[:size]), self.reply[size:]
        return data

    def read_until(self, terminator):
        return self.read(len(self.reply))

    def flush(self): ...

    def reset_input_buffer(self): ...

    def reset_output_buffer(self): ...

    def close(self):
        self.is_open = False


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def run_scan(instrument, **changes):
        monkeypatch.setattr("serial.Serial", instrument)
        params = dict(device="azbil_temp", port="COM7", baudrate=9600, parity="E",
                      azbil_temp_protocol=instrument.protocol, start_address=1, end_address=1,
                      timeout_s=0.05)
        return list(scan(ScanConfig(**params | changes)))

    return run_scan


@pytest.mark.parametrize("protocol", ["modbus_rtu", "cpl"])
def test_scan_scales_by_live_decimal_position_and_only_reads(run, protocol):
    instrument = Instrument(protocol, HEALTHY)
    [result] = run(instrument)
    assert result.status == "online"
    assert result.values["pv"] == -5.0  # 0xFFCE is -50 with one decimal
    assert result.values["sp"] == 120.5
    assert result.values["mv_pct"] == 45.5
    assert (result.values["run_ready"], result.values["auto_manual"]) == ("RUN", "AUTO")
    assert len(instrument.requests) == 6
    assert not instrument.is_open


def test_wire_frames_use_documented_addresses(run):
    instrument = Instrument("modbus_rtu", HEALTHY)
    run(instrument)
    assert instrument.requests[0] == bytes.fromhex("0103238D0001") + append_crc(
        bytes.fromhex("0103238D0001"))[-2:]
    instrument = Instrument("cpl", HEALTHY)
    run(instrument)
    assert b"01" + b"00XRD238D0001" in instrument.requests[0]


def test_cpl_honours_every_serial_format_the_instrument_offers(run):
    instrument = Instrument("cpl", HEALTHY)
    run(instrument, parity="O", stopbits=2)
    assert (instrument.opened["parity"], instrument.opened["stopbits"]) == ("O", 2)


@pytest.mark.parametrize("protocol", ["modbus_rtu", "cpl"])
def test_silent_addresses_are_offline_and_the_scan_continues(run, protocol):
    results = run(Instrument(protocol, HEALTHY, station=2), end_address=3)
    assert [item.status for item in results] == ["offline", "online", "offline"]


def test_input_alarm_is_degraded_because_pv_is_pinned_to_its_limit(run):
    [result] = run(Instrument("modbus_rtu", HEALTHY | {PV: 19999, INPUT_ALARM: 0b1}))
    assert result.status == "degraded"
    assert "AL01" in result.detail


def test_undefined_alarm_bit_stays_visible(run):
    [result] = run(Instrument("modbus_rtu", HEALTHY | {INPUT_ALARM: 0b1000}))
    assert (result.status, "0x0008" in result.detail) == ("degraded", True)


def test_control_status_is_decoded_from_its_bits(run):
    [result] = run(Instrument("cpl", HEALTHY | {CONTROL: 0b11}))
    assert (result.values["run_ready"], result.values["auto_manual"]) == ("READY", "MANUAL")


@pytest.mark.parametrize("protocol", ["modbus_rtu", "cpl"])
def test_communication_lock_only_loses_the_control_status(run, protocol):
    [result] = run(Instrument(protocol, HEALTHY, denied=[CONTROL]))
    assert result.status == "online"
    assert "run_ready" not in result.values
    assert "通讯锁定" in result.detail


@pytest.mark.parametrize("protocol", ["modbus_rtu", "cpl"])
def test_pv_rejection_is_an_error_not_offline(run, protocol):
    [result] = run(Instrument(protocol, HEALTHY, denied=[PV]))
    assert result.status == "error"


@pytest.mark.parametrize("registers, denied", [
    (HEALTHY | {DECIMALS: 4}, []),  # engineering values would be off by a factor of ten
    (HEALTHY, [SP]),
])
def test_unusable_scale_or_sp_keeps_raw_pv_without_engineering_units(run, registers, denied):
    [result] = run(Instrument("modbus_rtu", registers, denied=denied))
    assert result.status == "degraded"
    assert result.values == {"pv_raw": HEALTHY[PV]}


def test_cancel_stops_between_addresses_and_closes_the_port(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    instrument = Instrument("cpl", HEALTHY)
    monkeypatch.setattr("serial.Serial", instrument)
    stop = Event()
    config = ScanConfig(device="azbil_temp", port="COM7", azbil_temp_protocol="cpl", parity="E",
                        start_address=1, end_address=5)
    results = list(scan(config, stop, on_result=lambda item: stop.set()))
    assert len(results) == 1
    assert not instrument.is_open


def test_example_profile_is_valid():
    import json
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "examples" / "azbil_temp.json"
    assert ScanConfig(**json.loads(example.read_text(encoding="utf-8"))).device == "azbil_temp"
