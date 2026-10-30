"""Rate-controlled, non-backlogging diagnostic runs."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Callable

from .config import ScanConfig
from .runs import RunStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StressConfig:
    profile: ScanConfig
    mode: str  # real_read | sim_read | sim_write
    target_hz: float
    duration_s: float

    def __post_init__(self) -> None:
        if self.mode not in {"real_read", "sim_read", "sim_write"}:
            raise ValueError("未知压测模式")
        if self.mode == "sim_write" and self.profile.device == "balance":
            raise ValueError("天平去皮不参与循环写入")
        if not math.isfinite(self.target_hz) or not 0.1 <= self.target_hz <= 20:
            raise ValueError("目标频率须在 0.1–20 Hz")
        if not math.isfinite(self.duration_s) or not 0.1 <= self.duration_s <= 86400:
            raise ValueError("持续时间须在 0.1–86400 秒")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(percentile / 100 * len(ordered)) - 1
    return round(ordered[max(0, index)], 3)


def run_stress(
    cfg: StressConfig, *, stop: Event | None = None,
    progress: Callable[[dict], None] | None = None,
    source_factory: Callable[[StressConfig], object] | None = None,
    store: RunStore | None = None,
) -> tuple[dict, RunStore]:
    """Run one source in the calling background worker; source is opened once per run."""
    from .sources import open_source

    cancel = stop or Event()
    evidence = store or RunStore(cfg.mode, cfg.profile)
    source = source_factory(cfg) if source_factory else open_source(cfg)
    memory_start = _memory_mb()
    started = time.monotonic()
    deadline = started + cfg.duration_s
    next_due = started
    period = 1 / cfg.target_hz
    attempted = succeeded = missed = reconnects = 0
    reconnect_started: float | None = None
    recovery_s: list[float] = []
    latencies_ms: list[float] = []
    last_error = ""
    try:
        try:
            source.open()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            reconnect_started = time.monotonic()
            logger.warning("initial source open failed: %s", last_error)
        while not cancel.is_set() and time.monotonic() < deadline:
            delay = next_due - time.monotonic()
            if delay > 0 and cancel.wait(delay):
                break
            if time.monotonic() >= deadline:
                break
            attempted += 1
            tick = time.monotonic()
            try:
                value = source.sample() if cfg.mode != "sim_write" else source.write_cycle(attempted)
                succeeded += 1
                if reconnect_started is not None:
                    recovery_s.append(time.monotonic() - reconnect_started)
                    reconnect_started = None
                status, detail = "ok", value
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                status, detail = "error", last_error
                logger.warning("stress I/O failed: %s", last_error)
                if cfg.mode == "real_read":
                    if reconnect_started is None:
                        reconnect_started = time.monotonic()
                    try:
                        source.close()
                        source.open()
                        reconnects += 1
                    except Exception as reconnect_exc:
                        logger.warning("reconnect failed: %s", reconnect_exc)
            latency_ms = (time.monotonic() - tick) * 1000
            latencies_ms.append(latency_ms)
            event = {"at": datetime.now(timezone.utc).isoformat(), "number": attempted,
                     "status": status, "latency_ms": round(latency_ms, 3), "result": detail}
            evidence.event(event)
            if progress and (status != "ok" or attempted % max(1, int(cfg.target_hz)) == 0):
                progress(event)
            next_due += period
            if time.monotonic() > next_due:
                skipped = math.floor((time.monotonic() - next_due) / period) + 1
                missed += skipped
                next_due += skipped * period
    finally:
        try:
            source.close()
        except Exception as exc:
            logger.warning("source close failed: %s", exc)
        elapsed = max(time.monotonic() - started, 0.001)
        error_rate = (attempted - succeeded) / attempted if attempted else 1.0
        summary = {
            "mode": cfg.mode, "device": cfg.profile.device, "port": cfg.profile.port,
            "target_hz": cfg.target_hz, "duration_target_s": cfg.duration_s,
            "elapsed_s": round(elapsed, 3), "attempted": attempted, "succeeded": succeeded,
            "errors": attempted - succeeded, "error_rate": round(error_rate, 6),
            "missed_ticks": missed, "achieved_hz": round(succeeded / elapsed, 3),
            "p50_ms": _percentile(latencies_ms, 50),
            "p95_ms": _percentile(latencies_ms, 95),
            "p99_ms": _percentile(latencies_ms, 99),
            "reconnects": reconnects, "max_recovery_s": round(max(recovery_s), 3) if recovery_s else None,
            "memory_delta_mb": round(_memory_mb() - memory_start, 3),
            "last_error": last_error, "cancelled": cancel.is_set(),
            "pass_provisional": (not cancel.is_set() and error_rate <= 0.001
                                 and (not recovery_s or max(recovery_s) <= 60)
                                 and succeeded / elapsed >= cfg.target_hz * 0.99),
        }
        evidence.finish(summary)
    return summary, evidence


def _memory_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0
