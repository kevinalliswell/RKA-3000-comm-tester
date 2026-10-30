import json

import pytest

from rka_comm_tester.auth import AdminSession, AuthStore


def test_first_run_account_and_password_change(tmp_path):
    auth = AuthStore(tmp_path / "auth.json")
    assert not auth.initialized
    auth.initialize("admin_1", "a long test password")
    saved = json.loads(auth.path.read_text())
    assert "a long test password" not in str(saved)
    assert not auth.verify("admin_1", "bad password")
    assert auth.verify("admin_1", "a long test password")
    auth.change_password("admin_1", "a long test password", "the new long password")
    assert not auth.verify("admin_1", "a long test password")
    assert auth.verify("admin_1", "the new long password")
    with pytest.raises(ValueError):
        auth.initialize("another", "the new long password")


def test_failed_logins_lock_account(tmp_path):
    now = [1000]
    auth = AuthStore(tmp_path / "auth.json", clock=lambda: now[0])
    auth.initialize("admin", "a long test password")
    for _ in range(5):
        assert not auth.verify("admin", "bad password")
    with pytest.raises(PermissionError, match="锁定"):
        auth.verify("admin", "a long test password")
    now[0] += 901
    assert auth.verify("admin", "a long test password")


def test_session_idle_lock():
    now = [0]
    session = AdminSession("admin", clock=lambda: now[0])
    now[0] = 1799
    session.touch()
    now[0] = 3600
    with pytest.raises(PermissionError):
        session.touch()
