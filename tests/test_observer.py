import asyncio
import gc
import weakref

import pytest

from faultfinder.observer import Counters, FaultFinderASGI, GrowthDetector, Sample


async def nothing(*args):
    pass


@pytest.mark.asyncio
async def test_streaming_count_finishes_after_response_and_background_work():
    started, finish = asyncio.Event(), asyncio.Event()

    async def target(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        started.set()
        await finish.wait()
        await send({"type": "http.response.body", "body": b"done"})

    wrapper = FaultFinderASGI(target)
    task = asyncio.create_task(wrapper({"type": "http"}, nothing, nothing))
    await started.wait()
    assert wrapper.counters.read() == {"active": 1, "completed": 0, "failed": 0}
    finish.set()
    await task
    assert wrapper.counters.read() == {"active": 0, "completed": 1, "failed": 0}


@pytest.mark.asyncio
async def test_cancellation_preserves_application_semantics_and_cleans_counter():
    started = asyncio.Event()

    async def target(*args):
        started.set()
        await asyncio.Future()

    wrapper = FaultFinderASGI(target)
    task = asyncio.create_task(wrapper({"type": "http"}, nothing, nothing))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wrapper.counters.read() == {"active": 0, "completed": 1, "failed": 1}


@pytest.mark.asyncio
async def test_observer_does_not_retain_request_objects():
    class Request:
        pass

    wrapper = FaultFinderASGI(nothing)
    obj = Request()
    ref = weakref.ref(obj)
    await wrapper({"type": "http", "request": obj}, nothing, nothing)
    del obj
    gc.collect()
    assert ref() is None


@pytest.mark.asyncio
async def test_collector_failure_does_not_break_requests(monkeypatch):
    calls = []

    async def target(*args):
        calls.append(True)

    wrapper = FaultFinderASGI(target)

    def broken():
        raise OSError("disk unavailable")

    monkeypatch.setattr(wrapper.counters, "begin", broken)
    await wrapper({"type": "http"}, nothing, nothing)
    assert calls == [True]
    assert wrapper.disabled


def test_counters_are_bounded_after_100000_operations():
    counters = Counters()
    for _ in range(100000):
        counters.begin()
        counters.end(False)
    assert counters.read() == {"active": 0, "completed": 100000, "failed": 0}
    assert set(vars(counters)) == {"lock", "active", "completed", "failed"}


def test_detector_requires_warmup_growth_and_completed_work():
    detector = GrowthDetector(warmup=10, minimum_bytes=10)
    for i in range(10):
        assert detector.add(Sample(i, i * 10, i, 0)) is None
    for i in range(10, 15):
        assert detector.add(Sample(i, i * 10, i, 0)) is None
    assert detector.add(Sample(15, 150, 15, 0)) is not None
    assert len(detector.samples) == 6


@pytest.mark.parametrize("active,completed", [(1, 10), (0, 0)])
def test_detector_does_not_escalate_active_or_idle_work(active, completed):
    detector = GrowthDetector(warmup=0, minimum_bytes=10)
    for i in range(20):
        assert detector.add(Sample(i, i * 10, completed, active)) is None
