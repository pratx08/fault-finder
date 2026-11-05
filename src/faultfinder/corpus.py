"""Small, explicitly seeded corpus with known object-lifetime contracts."""

import asyncio
from collections import deque
from collections.abc import Callable
from typing import Any

CASES = ("listeners", "registry", "task_results", "bounded_cache", "cycles", "temporary", "lazy")


class Payload:
    def __init__(self) -> None:
        self.data = bytearray(1024)
        self.cycle: Payload | None = None


class Scenario:
    def __init__(self, case: str, variant: str, track: Callable[[Any], None]) -> None:
        if case not in CASES:
            raise ValueError("Unknown corpus case")
        self.case = case
        self.variant = variant
        self.track = track
        self.owners: list[Any] = []
        self.cache: deque[Payload] = deque(maxlen=8)
        self.initialized = False

    async def run(self) -> None:
        obj = Payload()
        self.track(obj)
        if self.case == "listeners":
            callback = lambda: obj  # noqa: E731
            self.owners.append(callback)
            if self.variant == "control":
                self.owners.remove(callback)
        elif self.case == "registry":
            self.owners.append(obj)
            if self.variant == "control":
                self.owners.pop()
        elif self.case == "task_results":
            future = asyncio.get_running_loop().create_future()
            future.set_result(obj)
            self.owners.append(future)
            if self.variant == "control":
                self.owners.pop()
        elif self.case == "bounded_cache":
            self.cache.append(obj)
        elif self.case == "cycles":
            obj.cycle = obj
        elif self.case == "lazy" and not self.initialized:
            self.owners.append(bytearray(100_000))
            self.initialized = True

    def close(self) -> None:
        self.owners.clear()
        self.cache.clear()
