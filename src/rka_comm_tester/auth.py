"""Single local administrator account for the GUI."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from .paths import data_dir

_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_MAX_ATTEMPTS = 5
_LOCK_SECONDS = 15 * 60
_IDLE_SECONDS = 30 * 60


def _digest(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


class AuthStore:
    def __init__(self, path: Path | None = None, *, clock=time.time) -> None:
        self.path = path or data_dir() / "auth.json"
        self.clock = clock

    @property
    def initialized(self) -> bool:
        return self.path.is_file()

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    def initialize(self, username: str, password: str) -> None:
        if self.initialized:
            raise ValueError("管理员账号已建立")
        if not _USERNAME.fullmatch(username):
            raise ValueError("账号须为 3–32 位英文字母、数字或下划线")
        if len(password) < 12:
            raise ValueError("密码至少 12 位")
        salt = secrets.token_bytes(16)
        self._write({"version": 1, "username": username, "salt": salt.hex(),
                     "hash": _digest(password, salt), "attempts": 0, "locked_until": 0})

    def verify(self, username: str, password: str) -> bool:
        record = self._read()
        now = self.clock()
        if now < record["locked_until"]:
            raise PermissionError(f"登录已锁定，请在 {int(record['locked_until'] - now) + 1} 秒后重试")
        actual = _digest(password, bytes.fromhex(record["salt"]))
        valid = hmac.compare_digest(username, record["username"]) and hmac.compare_digest(actual, record["hash"])
        if valid:
            record["attempts"] = 0
            record["locked_until"] = 0
        else:
            record["attempts"] += 1
            if record["attempts"] >= _MAX_ATTEMPTS:
                record["attempts"] = 0
                record["locked_until"] = now + _LOCK_SECONDS
        self._write(record)
        return valid

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        if len(new_password) < 12:
            raise ValueError("新密码至少 12 位")
        if not self.verify(username, old_password):
            raise PermissionError("原密码错误")
        record = self._read()
        salt = secrets.token_bytes(16)
        record["salt"] = salt.hex()
        record["hash"] = _digest(new_password, salt)
        self._write(record)


class AdminSession:
    def __init__(self, username: str, *, clock=time.monotonic) -> None:
        self.username = username
        self.clock = clock
        self.last_activity = clock()
        self.locked = False

    @property
    def active(self) -> bool:
        return not self.locked and self.clock() - self.last_activity < _IDLE_SECONDS

    def touch(self) -> None:
        if not self.active:
            self.locked = True
            raise PermissionError("登录已过期，请重新登录")
        self.last_activity = self.clock()

    def lock(self) -> None:
        self.locked = True
