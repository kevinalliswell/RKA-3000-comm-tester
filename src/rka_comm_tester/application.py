"""Application use cases, independent of transport, window and serial libraries."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import ScanConfig
from .models import PortInfo, ScanResult

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"completed", "cancelled", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileStore(Protocol):
    def load(self) -> ScanConfig | None: ...

    def save(self, config: ScanConfig) -> None: ...


class Probe(Protocol):
    def __call__(
        self, config: ScanConfig, cancelled: threading.Event,
    ) -> Iterator[ScanResult]: ...


class ScanConflict(ValueError):
    """A scan or tare test is already using the communication adapter, or it is closing."""


@dataclass
class ScanJob:
    job_id: str
    config: ScanConfig
    operation: str = "scan"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.job_id, "operation": self.operation, "status": self.status,
                "config": self.config.to_dict(), "results": list(self.results),
                "error": self.error, "created_at": self.created_at,
                "started_at": self.started_at, "finished_at": self.finished_at,
            }


class ScanService:
    """One active scan or tare test; cancellation completes only after driver cleanup."""

    def __init__(
        self, log_path: Path, probe: Probe,
        list_ports: Callable[[], list[PortInfo]], profiles: ProfileStore,
        *, tare: Probe | None = None,
    ) -> None:
        self.log_path = log_path
        self._probe = probe
        self._tare = tare
        self._list_ports = list_ports
        self._profiles = profiles
        self._jobs: dict[str, ScanJob] = {}
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._closing = False

    def health(self) -> dict[str, Any]:
        with self._lock:
            running = int(self._worker is not None and self._worker.is_alive())
        return {
            "ok": not self._closing, "service": "rka-comm-tester",
            "running_scans": running, "log_path": str(self.log_path),
        }

    def ports(self) -> list[dict[str, str]]:
        return [asdict(port) for port in self._list_ports()]

    def load_profile(self) -> dict | None:
        config = self._profiles.load()
        return config.to_dict() if config else None

    def save_profile(self, payload: dict) -> dict:
        config = ScanConfig(**payload)
        self._profiles.save(config)
        return config.to_dict()

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._start(ScanConfig(**payload), "scan", self._probe)

    def start_tare(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Tare changes the balance zero; callers start it only on explicit operator request."""
        config = ScanConfig(**payload)
        if config.device != "balance":
            raise ValueError("去皮测试仅适用于电子天平")
        if self._tare is None:
            raise ValueError("当前服务未启用去皮测试")
        return self._start(config, "tare", self._tare)

    def _start(self, config: ScanConfig, operation: str, probe: Probe) -> dict[str, Any]:
        with self._lock:
            if self._closing or (self._worker is not None and self._worker.is_alive()):
                raise ScanConflict("已有扫描或去皮测试正在运行或清理，请稍候")
            # Keep a bounded in-memory history instead of leaking every auto-review job.
            while len(self._jobs) >= 50:
                self._jobs.pop(next(iter(self._jobs)))
            job = ScanJob(uuid.uuid4().hex, config, operation)
            self._jobs[job.job_id] = job
            self._worker = threading.Thread(
                target=self._run, args=(job, probe), name=f"{operation}-{job.job_id[:8]}",
            )
            self._worker.start()
        return job.snapshot()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.snapshot() if job else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        with job.lock:
            if job.status not in TERMINAL_STATES:
                job.cancel_event.set()
        return job.snapshot()

    def close(self) -> None:
        with self._lock:
            self._closing = True
            jobs = list(self._jobs.values())
            worker = self._worker
        for job in jobs:
            job.cancel_event.set()
        if worker:
            worker.join()  # Drivers own bounded I/O and finally-based cleanup.

    def _run(self, job: ScanJob, probe: Probe) -> None:
        with job.lock:
            job.status = "running"
            job.started_at = _now()
        terminal_status = "completed"
        error = None
        try:
            if not job.cancel_event.is_set():
                for result in probe(job.config, job.cancel_event):
                    with job.lock:
                        job.results.append(result.to_dict())
        except Exception as exc:
            logger.exception("%s job failed: %s", job.operation, job.job_id)
            error = f"{type(exc).__name__}: {exc}"
            terminal_status = "error"
        finally:
            with job.lock:
                job.error = error
                job.status = ("cancelled" if job.cancel_event.is_set()
                              and terminal_status == "completed" else terminal_status)
                job.finished_at = _now()
