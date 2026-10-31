from threading import Event

from rka_comm_tester.config import ScanConfig
from rka_comm_tester.scanner import ScanResult, scan


def test_scan_reports_each_probe_and_obeys_cancel(monkeypatch):
    seen = []
    cancelled = Event()

    def fake_scan(cfg, stop):
        for address in cfg.addresses:
            seen.append(address)
            yield ScanResult("taie", cfg.port, address, "online", {"pv_c": 100.0}, "valid PV")

    monkeypatch.setattr("rka_comm_tester.scanner._scan_taie", fake_scan)
    cfg = ScanConfig(device="taie", port="COM7", start_address=1, end_address=3)
    results = list(scan(cfg, cancelled, on_result=lambda item: cancelled.set()))
    assert seen[:1] == [1]
    assert len(results) == 1
    assert results[0].status == "online"


def test_scan_port_error_is_reported_not_raised(monkeypatch):
    def fake_scan(cfg, stop):
        raise OSError("port unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr("rka_comm_tester.scanner._scan_taie", fake_scan)
    cfg = ScanConfig(device="taie", port="COM7", start_address=1, end_address=1)
    result = list(scan(cfg))[0]
    assert result.status == "error"
    assert "port unavailable" in result.detail


def test_taie_scan_uses_pv_for_liveness_and_keeps_sv_diagnostic(monkeypatch):
    from rka_comm_tester._vendor.taie_temp import modbus_rtu
    from rka_comm_tester.scanner import _scan_taie

    class FakeClient:
        def __init__(self, slave_id, settings):
            self.slave_id = slave_id

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read_holding_registers(self, register, quantity):
            if self.slave_id == 2:
                raise modbus_rtu.ModbusTimeoutError("no response")
            if register == 0x0000:
                raise OSError("SV register unavailable")
            return (1000,) if register == 0x008A else (900,)

    monkeypatch.setattr(modbus_rtu, "TaieModbusRtuClient", FakeClient)
    cfg = ScanConfig(device="taie", port="COM7", start_address=1, end_address=2)
    results = list(_scan_taie(cfg, Event()))
    assert results[0].status == "online" and results[0].values["pv_c"] == 100.0
    assert "SV 读取失败" in results[0].detail
    assert results[1].status == "offline"


def test_azbil_scan_needs_live_scale_before_engineering_units(monkeypatch):
    from rka_comm_tester._vendor.azbil_mfc import bus as azbil_bus
    from rka_comm_tester._vendor.azbil_mfc import device_catalog
    from rka_comm_tester.scanner import _scan_azbil

    class FakeBus:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read_registers(self, address, register, quantity):
            return [100]

    monkeypatch.setattr(azbil_bus, "RS485Bus", FakeBus)
    monkeypatch.setattr(device_catalog, "read_flow_scale", lambda bus, address: None)
    cfg = ScanConfig(device="azbil", port="COM8", parity="E", start_address=1, end_address=1)
    result = next(_scan_azbil(cfg, Event()))
    assert result.status == "degraded"
    assert "pv_lpm" not in result.values


def test_azbil_scan_rejects_raw_values_above_live_scale(monkeypatch):
    from rka_comm_tester._vendor.azbil_mfc import bus as azbil_bus
    from rka_comm_tester._vendor.azbil_mfc import device_catalog
    from rka_comm_tester._vendor.azbil_mfc.device_catalog import FlowScale
    from rka_comm_tester._vendor.azbil_mfc.registers import RAMAddress
    from rka_comm_tester.scanner import _scan_azbil

    class FakeBus:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read_registers(self, address, register, quantity):
            if register == RAMAddress.PV_CURRENT:
                return [1000]
            return [50]

    monkeypatch.setattr(azbil_bus, "RS485Bus", FakeBus)
    monkeypatch.setattr(device_catalog, "read_flow_scale", lambda bus, address: FlowScale(100, 1, 1))
    cfg = ScanConfig(device="azbil", port="COM8", parity="E", start_address=1, end_address=1)
    result = next(_scan_azbil(cfg, Event()))
    assert result.status == "degraded"
    assert "超出量程" in result.detail
    assert "pv_lpm" not in result.values


def test_balance_scan_treats_unstable_as_degraded_and_o1_as_opt_in(monkeypatch):
    from rka_comm_tester._vendor import balance_serial
    from rka_comm_tester._vendor.balance_serial.protocols.gs_cpa import GSCpaProtocol
    from rka_comm_tester.scanner import _scan_balance

    initialized = []
    shutdowns = []

    class FakeClient:
        def __init__(self, settings, protocol):
            assert settings.to_serial_kwargs()["write_timeout"] == settings.timeout
            self.protocol = protocol
            self.messages = iter([protocol.parse_message("+0277.19 G U")])

        def open(self):
            pass

        def close(self):
            pass

        def initialize_device(self):
            initialized.append(True)

        def shutdown_device(self):
            shutdowns.append(True)

        def read_message(self):
            return next(self.messages, None)

    monkeypatch.setattr(balance_serial, "BalanceClient", FakeClient)
    cfg = ScanConfig(device="balance", port="COM9", balance_lines=2,
                     balance_start_output=False)
    result = list(_scan_balance(cfg, Event()))[0]
    assert result.status == "degraded"
    assert not initialized
    cfg_with_o1 = ScanConfig(device="balance", port="COM9", balance_lines=2,
                             balance_start_output=True)
    result = list(_scan_balance(cfg_with_o1, Event()))[0]
    assert result.status == "degraded"
    assert initialized == [True]
    assert shutdowns == [True]
    assert GSCpaProtocol(auto_continuous_output=False).startup_commands() == ()


def test_cancel_closes_balance_probe_before_scan_returns(monkeypatch):
    from rka_comm_tester._vendor import balance_serial

    stop = Event()
    cleanup = []

    class FakeClient:
        def __init__(self, settings, protocol):
            self.protocol = protocol

        def open(self): ...

        def initialize_device(self): ...

        def read_message(self):
            stop.set()  # Cancellation arrives during the serial read.
            return self.protocol.parse_message("+0277.19 G S")

        def shutdown_device(self):
            cleanup.append("O0")

        def close(self):
            cleanup.append("close")

    monkeypatch.setattr(balance_serial, "BalanceClient", FakeClient)
    config = ScanConfig(device="balance", port="unused", balance_start_output=True)
    assert list(scan(config, stop)) == []
    assert cleanup == ["O0", "close"]
