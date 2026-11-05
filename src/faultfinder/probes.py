"""Measurement primitives. Never retain inspected application objects."""

import gc
import tracemalloc
import weakref
from collections import Counter
from pathlib import Path
from typing import Any


class LifetimeTracker:
    def __init__(self) -> None:
        self.enabled = False
        self.refs: list[weakref.ReferenceType[Any]] = []

    def track(self, obj: Any) -> None:
        if self.enabled:
            if len(self.refs) >= 100_000:
                raise ValueError("Object tracking budget exceeded")
            self.refs.append(weakref.ref(obj))

    def survivors(self) -> int:
        return sum(ref() is not None for ref in self.refs)

    def owners(self) -> list[str]:
        types: Counter[str] = Counter()
        # Bounded diagnostic; no object values or reprs enter evidence.
        for ref in self.refs[:20]:
            obj = ref()
            if obj is not None:
                for owner in gc.get_referrers(obj):
                    types[type(owner).__name__] += 1
        return sorted(types)


def snapshot(root: Path) -> tracemalloc.Snapshot:
    filters = [tracemalloc.Filter(True, str(root / "*"))]
    for pattern in ("*/test_*.py", "*\\test_*.py", "*/conftest.py", "*\\conftest.py"):
        filters.append(tracemalloc.Filter(False, pattern))
    return tracemalloc.take_snapshot().filter_traces(filters)


def allocation_delta(before: tracemalloc.Snapshot, root: Path) -> tuple[int, list[dict]]:
    differences = snapshot(root).compare_to(before, "lineno")
    top = []
    for stat in differences[:10]:
        frame = stat.traceback[0]
        try:
            file = str(Path(frame.filename).relative_to(root))
        except ValueError:
            file = Path(frame.filename).name
        top.append({"file": file, "line": frame.lineno, "bytes": stat.size_diff})
    return sum(stat.size_diff for stat in differences), top
