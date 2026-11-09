"""Supervisor lifecycle with a controlled blocking process-I/O boundary."""

import asyncio
import io
import threading
import time

import pytest

from aegis.collector import SourceConfig
from aegis.processes import ProcessCollector


def test_configuration_write_keeps_loop_responsive_and_stop_kills_before_close(tmp_path, monkeypatch):
    release = threading.Event()
    write_done = threading.Event()
    dead = threading.Event()
    events = []

    class Input:
        closed = False
        def write(self, data):
            events.append("write_entered")
            release.wait(1)
            write_done.set()
            dead.set()
            events.append("write_finished")
            raise BrokenPipeError("synthetic blocked startup pipe")
        def flush(self):
            pass
        def close(self):
            events.append("close_after_write" if write_done.is_set() else "close_before_write")
            self.closed = True

    class Process:
        pid = 123
        stdin = Input()
        stdout = io.BytesIO()
        def poll(self):
            return 1 if dead.is_set() else None
        def wait(self, timeout=None):
            if not dead.wait(timeout):
                import subprocess
                raise subprocess.TimeoutExpired("synthetic startup process", timeout)
            return 1
        def kill(self):
            events.append("kill")
            dead.set()
            release.set()

    process = Process()
    monkeypatch.setattr("aegis.processes.subprocess.Popen", lambda *a, **kw: process)
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", "synthetic-config-fixture-" + "x" * 32)
    configured = SourceConfig(deviceId="CONFIG-01", baseUrl="http://127.0.0.1:19101")
    # A separate thread makes the old blocking implementation fail, rather than hanging pytest.
    failsafe = threading.Timer(.5, release.set)
    failsafe.daemon = True
    failsafe.start()

    async def check():
        collector = ProcessCollector([configured], tmp_path, worker_timeout=.3,
                                     restart_limit=0, restart_delay=.01)
        await collector.start()
        started = time.monotonic()
        try:
            await asyncio.sleep(.04)
            assert time.monotonic() - started < .2, "Configuration write blocked the supervisor event loop"
            assert "write_entered" in events and not write_done.is_set()
            await collector.stop()
            assert "kill" in events
            assert "close_before_write" not in events
            assert events.index("kill") < events.index("write_finished") < events.index("close_after_write")
            assert write_done.is_set() and process.poll() is not None
            assert all(future.done() for future in collector._writers)
            assert collector.ingestion()[0]["restartCount"] == 0
        finally:
            release.set()
            await collector.stop()

    try:
        asyncio.run(check())
    finally:
        release.set()
        failsafe.cancel()


def test_failed_completed_writer_still_requires_kill_before_buffered_close(tmp_path, monkeypatch):
    events = []
    dead = threading.Event()
    class Input:
        closed = False
        def write(self, data):
            events.append("write_failed")
            raise OSError("synthetic failed write with residual buffered data")
        def flush(self):
            raise AssertionError("Failed write cannot reach flush")
        def close(self):
            events.append("close")
            self.closed = True
    class Process:
        pid = 123
        stdin = Input()
        stdout = io.BytesIO()
        def poll(self):
            return 1 if dead.is_set() else None
        def kill(self):
            events.append("kill")
            dead.set()
        def wait(self, timeout=None):
            if not dead.is_set():
                import subprocess
                raise subprocess.TimeoutExpired("synthetic failed config", timeout)
            return 1
    process = Process()
    monkeypatch.setattr("aegis.processes.subprocess.Popen", lambda *a, **kw: process)
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", "synthetic-config-fixture-" + "x" * 32)
    async def check():
        collector = ProcessCollector([SourceConfig(deviceId="FAILED-WRITE", baseUrl="http://127.0.0.1:19101")],
                                     tmp_path, restart_limit=0)
        await collector.start()
        try:
            deadline = time.monotonic() + 2
            while collector.ingestion()[0]["workerState"] != "FAILED":
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            assert events.index("write_failed") < events.index("kill") < events.index("close")
            assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
            assert process.poll() is not None
        finally:
            await collector.stop()
    asyncio.run(check())


def test_unfinished_writer_blocks_replacement_and_explicit_restart_after_process_reap(tmp_path, monkeypatch):
    release = threading.Event()
    dead = threading.Event()
    launches = []
    class Process:
        pid = 123
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        def poll(self):
            return 1 if dead.is_set() else None
        def kill(self):
            dead.set()
        def wait(self, timeout=None):
            assert dead.is_set()
            return 1
    process = Process()
    def launch(*args, **kwargs):
        launches.append(1)
        return process
    monkeypatch.setattr("aegis.processes.subprocess.Popen", launch)
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", "synthetic-config-fixture-" + "x" * 32)
    async def check():
        collector = ProcessCollector([SourceConfig(deviceId="STUCK-IO", baseUrl="http://127.0.0.1:19101")],
                                     tmp_path, worker_timeout=.05, restart_limit=3, restart_delay=.01)
        # Models a configuration I/O call that does not return even after its child exits.
        collector._write_config = lambda worker, child: release.wait(5)
        await collector.start()
        writers = []
        try:
            deadline = time.monotonic() + 4
            while collector.ingestion()[0]["workerState"] != "FAILED":
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            writers = list(collector._writers)
            assert len(writers) == 1 and not writers[0].done() and not writers[0].cancelled()
            assert process.poll() is not None and len(launches) == 1
            assert collector.connections()[0]["reasonCode"] == "WORKER_CLEANUP_FAILED"
            with pytest.raises(RuntimeError, match="could not be reaped"):
                await asyncio.wait_for(collector.stop(), 1)
            with pytest.raises(RuntimeError, match="has not been reaped"):
                await collector.start()
            assert len(launches) == 1
        finally:
            release.set()
            if writers:
                await asyncio.wait(writers, timeout=2)
                assert all(writer.done() and not writer.cancelled() for writer in writers)
            await collector.stop()
            process.stdin.close()
            process.stdout.close()
    asyncio.run(check())
