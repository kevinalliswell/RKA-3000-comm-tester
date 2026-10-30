"""One persistent worker thread per serial port, with no same-port overlap."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable


class PortWorkers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._busy: set[str] = set()
        self._closed = False

    def submit(self, port: str, operation: Callable[[], object]) -> Future:
        with self._lock:
            if self._closed:
                raise RuntimeError("工作线程已关闭")
            if port in self._busy:
                raise RuntimeError(f"串口 {port} 正在执行其他任务")
            executor = self._executors.setdefault(
                port, ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"rka-{len(self._executors)+1}"),
            )
            self._busy.add(port)
            future = executor.submit(operation)
        future.add_done_callback(lambda _future: self._release(port))
        return future

    def _release(self, port: str) -> None:
        with self._lock:
            self._busy.discard(port)

    def busy(self, port: str | None = None) -> bool:
        with self._lock:
            return bool(self._busy) if port is None else port in self._busy

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executors = list(self._executors.values())
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)
