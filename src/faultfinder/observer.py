"""Bounded lifecycle counters and coarse production growth detection."""

import json
import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class Counters:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.completed = 0
        self.failed = 0

    def begin(self) -> None:
        with self.lock:
            self.active += 1

    def end(self, failed: bool) -> None:
        with self.lock:
            self.active -= 1
            self.completed += 1
            self.failed += int(failed)

    def read(self) -> dict[str, int]:
        with self.lock:
            return {"active": self.active, "completed": self.completed, "failed": self.failed}


class FaultFinderASGI:
    """Wrap once at deployment. Never stores request/response objects or payloads."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        metrics_file: str | None = None,
        interval: float = 5,
    ) -> None:
        self.app = app
        self.counters = Counters()
        self.metrics_file = metrics_file or os.environ.get("FAULTFINDER_METRICS_FILE")
        self.interval = interval
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.disabled = False

    def publish(self) -> None:
        if not self.metrics_file:
            return
        target = Path(self.metrics_file)
        temporary = target.with_suffix(".tmp")
        data = {**self.counters.read(), "pid": os.getpid(), "timestamp": time.time()}
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, target)

    def loop(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.publish()
            except Exception:
                self.disabled = True
                return

    def start(self) -> None:
        if self.metrics_file and self.thread is None:
            self.thread = threading.Thread(
                target=self.loop, daemon=True, name="faultfinder-metrics"
            )
            self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=max(1, self.interval + 0.1))

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            self.start()
            try:
                await self.app(scope, receive, send)
            finally:
                self.close()
            return
        if scope["type"] != "http" or self.disabled:
            await self.app(scope, receive, send)
            return
        started = False
        failed = True
        try:
            try:
                self.start()
                self.counters.begin()
                started = True
            except Exception:
                self.disabled = True
            await self.app(scope, receive, send)
            failed = False
        finally:
            if started:
                try:
                    self.counters.end(failed)
                except Exception:
                    self.disabled = True


@dataclass(frozen=True)
class Sample:
    elapsed: float
    rss: int
    completed: int | None = None
    active: int | None = None


class GrowthDetector:
    def __init__(
        self, warmup: float = 60, windows: int = 6, minimum_bytes: int = 8 * 1024 * 1024
    ) -> None:
        self.samples: deque[Sample] = deque(maxlen=windows)
        self.warmup = warmup
        self.windows = windows
        self.minimum_bytes = minimum_bytes

    def add(self, sample: Sample) -> dict[str, Any] | None:
        if sample.elapsed < self.warmup:
            return None
        self.samples.append(sample)
        if len(self.samples) < self.windows or any(s.active for s in self.samples):
            return None
        first, last = self.samples[0], self.samples[-1]
        growth = last.rss - first.rss
        rises = sum(
            b.rss > a.rss for a, b in zip(self.samples, list(self.samples)[1:], strict=False)
        )
        if growth < self.minimum_bytes or rises < self.windows - 2:
            return None
        operations = None
        if last.completed is not None and first.completed is not None:
            operations = last.completed - first.completed
            if operations <= 0:
                return None
        return {
            "rss_growth_bytes": growth,
            "completed_operations": operations,
            "verdict": "suspected",
            "reason": "Sustained process-memory growth; "
            "allocation and lifecycle evidence still required",
        }
