import json

import pytest

from rka_comm_tester.config import ScanConfig
from rka_comm_tester.runs import RunStore, export_bundle
from rka_comm_tester.sources import RealReadSource
from rka_comm_tester.stress import StressConfig, run_stress


class FakeSource:
    def __init__(self):
        self.opened = 0
        self.closed = 0

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def sample(self):
        return {"pv_c": 100}

    def write_cycle(self, number):
        return {"written": number}


def test_stress_run_saves_summary_and_bundle(tmp_path):
    profile = ScanConfig(device="taie", port="SIM", start_address=1, end_address=1)
    cfg = StressConfig(profile, "sim_write", target_hz=5, duration_s=0.45)
    source = FakeSource()
    store = RunStore(cfg.mode, profile, root=tmp_path)
    summary, evidence = run_stress(cfg, source_factory=lambda _: source, store=store)
    assert summary["attempted"] >= 2
    assert summary["errors"] == 0
    assert source.opened == source.closed == 1
    assert json.loads((evidence.path / "summary.json").read_text())["mode"] == "sim_write"
    bundle = export_bundle(evidence.path, tmp_path / "bundle.zip", tmp_path / "missing.log")
    assert bundle.is_file()


def test_balance_tare_cannot_be_stressed():
    profile = ScanConfig(device="balance", port="SIM")
    with pytest.raises(ValueError, match="不参与"):
        StressConfig(profile, "sim_write", target_hz=5, duration_s=1)


@pytest.mark.parametrize("device", ["taie", "azbil"])
def test_protocol_simulated_write_cycle(device, tmp_path):
    profile = ScanConfig(device=device, port="SIM", start_address=1, end_address=1,
                         parity="E" if device == "azbil" else "N")
    cfg = StressConfig(profile, "sim_write", target_hz=5, duration_s=0.25)
    summary, _ = run_stress(cfg, store=RunStore(cfg.mode, profile, root=tmp_path))
    assert summary["succeeded"] >= 1
    assert summary["errors"] == 0


def test_real_source_has_no_automatic_write_cycle():
    source = RealReadSource(ScanConfig(device="azbil", port="COM7", parity="E"))
    with pytest.raises(PermissionError, match="禁用"):
        source.write_cycle(1)
