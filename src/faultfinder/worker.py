"""One fresh process per trial. Invoked only by the execution backend."""

import asyncio
import gc
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import platform
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import psutil

from faultfinder.corpus import Scenario
from faultfinder.models import Plan, Trial
from faultfinder.probes import LifetimeTracker, allocation_delta, snapshot


async def resolve(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


async def probe(plan: Plan, trial: Trial, root: Path, harness: Path) -> None:
    tracker = LifetimeTracker()
    if plan.kind == "demo":
        scenario: Any = Scenario(plan.demo_case, trial.variant, tracker.track)
    else:
        spec = importlib.util.spec_from_file_location("faultfinder_experiment", harness)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load harness")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scenario = module.build(trial.variant, tracker.track)
    target = (root / plan.target_file).resolve() if plan.target_file else None
    hits = 0

    def trace(frame: Any, event: str, arg: Any) -> Any:
        nonlocal hits
        if event == "line" and frame.f_lineno == plan.target_line:
            if Path(frame.f_code.co_filename).resolve() == target:
                hits += 1
        return trace

    try:
        for _ in range(plan.policy.warmup):
            await resolve(scenario.run())
        await asyncio.sleep(0)
        gc.collect()
        before = snapshot(root)
        tracker.enabled = True
        if target is not None:
            sys.settrace(trace)
        for _ in range(trial.size):
            await resolve(scenario.run())
            trial.completed += 1
        sys.settrace(None)
        await asyncio.sleep(0)
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        if pending:
            raise ValueError("Background tasks remain active; workload has not settled")
        trial.natural_survivors = tracker.survivors()
        gc.collect()
        trial.survivors = tracker.survivors()
        if trial.size and not tracker.refs:
            raise ValueError("Harness did not track any weak-referenceable objects")
        trial.owner_types = tracker.owners()
        trial.retained_bytes, trial.allocations = allocation_delta(before, root)
        trial.path_hits = hits
    finally:
        sys.settrace(None)
        await resolve(scenario.close())
    gc.collect()
    trial.after_release = tracker.survivors()


def execute(request: dict) -> Trial:
    plan = Plan.model_validate(request["plan"])
    trial = Trial(variant=request["variant"], size=request["size"], repeat=request["repeat"])
    root = Path(request["root"]).resolve()
    for path in (root, root / "src", root / "python"):
        if path.is_dir():
            sys.path.insert(0, str(path))
    trial.runtime = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "instrumentation_sha256": hashlib.sha256(
            b"".join(
                p.name.encode() + p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py"))
            )
        ).hexdigest(),
        "packages_sha256": hashlib.sha256(
            "\n".join(
                sorted(
                    f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
                )
            ).encode()
        ).hexdigest(),
    }
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        trial.error = "Unsupported Python version"
        return trial
    tracemalloc.start(1)
    try:
        if plan.kind == "pytest":
            import pytest

            from faultfinder.pytest_plugin import RetentionPlugin

            plugin = RetentionPlugin(root, trial.size)
            code = pytest.main(
                [
                    *plan.args,
                    "-q",
                    "-o",
                    "addopts=",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "pytest_asyncio.plugin",
                ],
                plugins=[plugin],
            )
            if code != 0 or plugin.invalid or plugin.width == 0:
                raise ValueError("Tests failed, skipped, xfailed, or no tests were collected")
            expected = plugin.width * (trial.size + 1)
            if plugin.passed != expected:
                raise ValueError("Not every repeated test passed")
            trial.completed = trial.size
            trial.retained_bytes = plugin.delta
            trial.allocations = plugin.allocations
        else:
            asyncio.run(probe(plan, trial, root, Path(request["harness"])))
        trial.rss_bytes = psutil.Process().memory_info().rss
        if psutil.Process().children():
            raise ValueError("Child processes are outside the supported measurement scope")
        trial.ok = True
    except Exception as exc:
        # Exception text may contain credentials or application values.
        trial.error = f"{type(exc).__name__}: workload or measurement failed"
        if isinstance(exc, ValueError) and str(exc) in {
            "Child processes are outside the supported measurement scope",
            "Background tasks remain active; workload has not settled",
            "Harness did not track any weak-referenceable objects",
            "Tests failed, skipped, xfailed, or no tests were collected",
            "Not every repeated test passed",
        }:
            trial.error = str(exc)
    finally:
        tracemalloc.stop()
    return trial


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = execute(request)
    output = Path(sys.argv[2])
    output.write_text(result.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
