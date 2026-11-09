"""Acquisition task liveness is independent of accepted data or worker heartbeat."""

import asyncio
import io
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from aegis.collector import Collector, SourceConfig
from aegis import module_worker
from aegis.processes import ProcessCollector


TOKEN = "liveness-fixture-" + "x" * 32


def source():
    return SourceConfig(deviceId="MODULE-LIVE", baseUrl="http://127.0.0.1:19101",
                        pollIntervalMs=60000)


@pytest.mark.parametrize("ending", ["return", "error", "cancel"])
def test_worker_stops_publishing_when_its_real_collector_task_ends(tmp_path, monkeypatch, ending):
    async def ended(self, index):
        if ending == "error":
            raise RuntimeError("synthetic acquisition failure")
        if ending == "cancel":
            raise asyncio.CancelledError

    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    monkeypatch.setattr(Collector, "_loop", ended)
    lines = []
    monkeypatch.setattr(module_worker, "_write", lines.append)
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader:
        monkeypatch.setattr(module_worker.sys, "stdin", SimpleNamespace(buffer=reader))
        config = module_worker.WorkerConfig(source=source(), archivePath=str(tmp_path / "unused.sqlite3"),
                                            evaluate=False, logPath=None)
        try:
            async def check():
                # Keep the parent pipe open: exit must be caused by acquisition failure, not EOF.
                with pytest.raises(RuntimeError, match="COLLECTOR_TASK_STOPPED"):
                    await asyncio.wait_for(module_worker.run(config), .7)
                assert len(lines) <= 1, "A dead collector kept emitting healthy worker summaries"
            asyncio.run(check())
        finally:
            os.close(write_fd)


def test_inflight_poll_deadline_does_not_depend_on_new_samples():
    async def check():
        clock = [0.0]
        entered = asyncio.Event()
        release = asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                                  monotonic=lambda: clock[0])
            async def stalled(*args, **kwargs):
                entered.set()
                await release.wait()
            collector._read = stalled
            await collector.start()
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert not collector._tasks[0].done()
                clock[0] = 11.0
                with pytest.raises(RuntimeError, match="COLLECTOR_POLL_STALLED"):
                    collector.ensure_live()
            finally:
                await collector.stop()
    asyncio.run(check())


@pytest.mark.parametrize("response", ["unavailable", "timeout"])
def test_completed_failed_poll_and_long_idle_remain_live(response):
    async def check():
        clock = [0.0]
        def transport(request):
            if response == "timeout":
                raise httpx.ReadTimeout("synthetic source timeout", request=request)
            return httpx.Response(503)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                                  monotonic=lambda: clock[0])
            await collector.start()
            try:
                for _ in range(10):
                    await asyncio.sleep(0)
                    if collector.connections()[0]["reasonCode"] is not None:
                        break
                assert collector.connections()[0]["reasonCode"] in {"POLL_TIMEOUT", "SOURCE_UNAVAILABLE"}
                assert collector.connections()[0]["acceptedBatches"] == 0
                # This task is sleeping on a configured 60s interval, not stuck inside a poll.
                clock[0] = 600.0
                collector.ensure_live()
                assert not collector._tasks[0].done()
            finally:
                await collector.stop()
            assert collector._tasks == []
    asyncio.run(check())


def test_late_frame_from_a_worker_being_reaped_cannot_publish_running_state(tmp_path):
    collector = ProcessCollector([source()], tmp_path)
    worker = collector._workers[0]
    payload = module_worker.build_frame(Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}))
    process = SimpleNamespace(stdout=io.BytesIO(json.dumps(payload).encode() + b"\n"))
    worker.process = process
    worker.state = "RESTARTING"
    collector._read(worker, process)
    assert worker.summary is None, "A late frame was accepted after teardown began"
    assert worker.state == "RESTARTING"


def test_log_close_failure_does_not_silently_end_the_module_monitor(tmp_path, monkeypatch):
    class BrokenLog:
        def write(self, value):
            pass
        def flush(self):
            pass
        def close(self):
            raise OSError("synthetic log close failure")

    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    async def check():
        collector = ProcessCollector([source()], tmp_path, restart_limit=1, restart_delay=.01)
        worker = collector._workers[0]
        worker.archive = SimpleNamespace(with_suffix=lambda _: SimpleNamespace(open=lambda _: BrokenLog()))
        launches = []
        def failed_launch(*args):
            launches.append(1)
            raise OSError("synthetic launch failure")
        collector._launch = failed_launch
        await collector.start()
        try:
            await asyncio.sleep(.1)
            assert collector.ingestion()[0]["workerState"] == "FAILED"
            assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
            assert len(launches) == 2
        finally:
            await collector.stop()
    asyncio.run(check())
