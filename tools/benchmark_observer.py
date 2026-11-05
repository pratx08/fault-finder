"""Repeatable ASGI middleware overhead experiment; not a production benchmark."""

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from faultfinder.observer import FaultFinderASGI


async def send(message):
    pass


async def target(scope, receive, send):
    await asyncio.sleep(0.002)
    await send({"type": "http.response.start", "status": 200})
    await send({"type": "http.response.body", "body": b"ok"})


async def trial(app, count):
    latencies = []
    started = time.perf_counter()
    for _ in range(count):
        begin = time.perf_counter()
        await app({"type": "http"}, send, send)
        latencies.append(time.perf_counter() - begin)
    return {
        "requests_per_second": count / (time.perf_counter() - started),
        "p95_ms": sorted(latencies)[int(count * 0.95)] * 1000,
    }


async def main(output):
    with tempfile.TemporaryDirectory() as temporary:
        wrapper = FaultFinderASGI(
            target, metrics_file=str(Path(temporary) / "metrics.json"), interval=0.1
        )
        await trial(target, 20)
        await trial(wrapper, 20)
        results = {"baseline": [], "observed": []}
        try:
            for repetition in range(5):
                order = [("baseline", target), ("observed", wrapper)]
                for name, app in order if repetition % 2 == 0 else reversed(order):
                    results[name].append(await trial(app, 300))
        finally:
            wrapper.close()
    baseline = {
        key: statistics.median(r[key] for r in results["baseline"])
        for key in results["baseline"][0]
    }
    observed = {
        key: statistics.median(r[key] for r in results["observed"])
        for key in results["observed"][0]
    }
    evidence = {
        "scope": "Sequential in-process ASGI workload with 2ms simulated I/O, 5 paired trials",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "samples": results,
        "throughput_degradation_percent": 100
        * (1 - observed["requests_per_second"] / baseline["requests_per_second"]),
        "p95_increase_percent": 100 * (observed["p95_ms"] / baseline["p95_ms"] - 1),
        "limitations": "Synthetic workload only; not a production or multiworker qualification",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".faultfinder/observer-benchmark.json"))
    asyncio.run(main(parser.parse_args().output))
