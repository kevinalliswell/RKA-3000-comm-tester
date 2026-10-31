import pytest

from rka_comm_tester.config import ScanConfig
from rka_comm_tester.profiles import FileProfileStore


def test_scan_config_allows_independent_serial_parameters():
    cfg = ScanConfig(
        device="taie", port="COM7", baudrate=9600, parity="E", stopbits=2,
        timeout_s=0.4, start_address=2, end_address=5,
    )
    assert cfg.addresses == range(2, 6)
    assert cfg.to_dict()["port"] == "COM7"


def test_scan_config_round_trip(tmp_path):
    cfg = ScanConfig(device="azbil", port="COM8", parity="E", start_address=2, end_address=4)
    path = tmp_path / "profile.json"
    store = FileProfileStore(path)
    assert store.load() is None
    store.save(cfg)
    assert store.load() == cfg


@pytest.mark.parametrize("changes", [
    {"port": ""}, {"timeout_s": 0}, {"start_address": 0},
    {"start_address": 8, "end_address": 7}, {"device": "azbil", "end_address": 128},
    {"device": "balance", "balance_protocol": "unknown"},
    {"balance_start_output": "false"},
])
def test_scan_config_rejects_invalid_values(changes):
    params = dict(device="taie", port="COM7") | changes
    with pytest.raises(ValueError):
        ScanConfig(**params)
