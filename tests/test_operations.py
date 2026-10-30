import pytest

from rka_comm_tester.auth import AdminSession
from rka_comm_tester.config import ScanConfig
from rka_comm_tester.operations import (
    WritePreview,
    WriteUncertain,
    WriteVerificationFailed,
    execute_write,
)


def test_write_requires_session_and_offline_confirmation(monkeypatch):
    cfg = ScanConfig(device="taie", port="COM7")
    session = AdminSession("admin")
    with pytest.raises(PermissionError):
        execute_write(cfg, 1, 100.0, session=session, offline_confirmed=False, expected_before=50.0)
    session.lock()
    with pytest.raises(PermissionError):
        execute_write(cfg, 1, 100.0, session=session, offline_confirmed=True, expected_before=50.0)


def test_write_rechecks_before_and_never_retries(monkeypatch):
    writes = []

    class FakeDevice:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def preview(self):
            value = 100.0 if writes else 50.0
            return WritePreview("taie", "COM7", 1, value, "°C", 0.0, 1000.0)

        def write_once(self, target):
            writes.append(target)

    monkeypatch.setattr("rka_comm_tester.operations._open_device", lambda cfg, address: FakeDevice())
    cfg = ScanConfig(device="taie", port="COM7")
    result = execute_write(cfg, 1, 100.0, session=AdminSession("admin"),
                           offline_confirmed=True, expected_before=50.0)
    assert result.status == "success"
    assert writes == [100.0]
    with pytest.raises(ValueError, match="变化"):
        execute_write(cfg, 1, 90.0, session=AdminSession("admin"),
                      offline_confirmed=True, expected_before=50.0)
    assert writes == [100.0]


@pytest.mark.parametrize("error,status", [
    (WriteVerificationFailed("echo mismatch"), "failed"),
    (WriteUncertain("timeout"), "unknown"),
])
def test_single_write_distinguishes_bad_echo_from_unknown_result(monkeypatch, error, status):
    class FakeDevice:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def preview(self):
            return WritePreview("taie", "COM7", 1, 50.0, "°C", 0.0, 1000.0)

        def write_once(self, target):
            raise error

    monkeypatch.setattr("rka_comm_tester.operations._open_device", lambda cfg, address: FakeDevice())
    cfg = ScanConfig(device="taie", port="COM7")
    result = execute_write(cfg, 1, 100.0, session=AdminSession("admin"),
                           offline_confirmed=True, expected_before=50.0)
    assert result.status == status
    assert result.after is None
