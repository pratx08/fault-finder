"""Worker-only pytest integration; no autoload in normal user test runs."""

import gc
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from faultfinder.probes import allocation_delta, snapshot


class RetentionPlugin:
    def __init__(self, root: Path, repeats: int) -> None:
        self.root = root
        self.repeats = repeats
        self.width = 0
        self.finished = 0
        self.passed = 0
        self.invalid = False
        self.before: tracemalloc.Snapshot | None = None
        self.delta: int | None = None
        self.allocations: list[dict] = []

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        self.width = len(items)
        # One warm-up suite, then N measured suites. Function fixtures still tear down.
        items[:] = items * (self.repeats + 1)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.failed or report.skipped or hasattr(report, "wasxfail"):
            self.invalid = True
        if report.when == "call" and report.passed:
            self.passed += 1

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: Any, nextitem: Any) -> Any:
        yield
        self.finished += 1
        if self.finished == self.width:
            gc.collect()
            self.before = snapshot(self.root)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        if self.before is not None:
            gc.collect()
            self.delta, self.allocations = allocation_delta(self.before, self.root)
