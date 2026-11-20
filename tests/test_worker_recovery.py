"""Bounded recovery of owned workers, using synthetic local processes and data."""

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from aegis.collector import Collector, SIGNAL_UNITS, SourceConfig
from aegis.module_worker import build_frame
from aegis.processes import ProcessCollector
from scripts.benchmark_emulators import running_stack, wait_until


TOKEN = "synthetic-worker-recovery-" + "x" * 32


def _source(port=19101, *, poll_ms=50):
    return SourceConfig(deviceId="RECOVERY-01", baseUrl=f"http://127.0.0.1:{port}",
                        pollIntervalMs=poll_ms)


async def _until(predicate, *, seconds=8, message="Worker condition did not become ready."):
    deadline = time.monotonic() + seconds
    while True:
        result = predicate()
        if result:
            return result
        assert time.monotonic() < deadline, message
        await asyncio.sleep(.02)


def _probe_workers(monkeypatch, modes):
    """Replace only the worker executable; retain the real supervisor and pipes."""
    original_popen = subprocess.Popen
    payload = build_frame(Collector([_source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}))
    encoded = json.dumps(payload, separators=(",", ":")) + "\n"
    owned = []

    def launch(arguments, **kwargs):
        assert arguments[-2:] == ["-m", "aegis.module_worker"]
        mode = modes[min(len(owned), len(modes) - 1)]
        # Keep the stdin lifeline open until the real supervisor closes it.
        # Partial bytes deliberately never form a complete heartbeat line.
        program = (
            "import sys, threading\n"
            "sys.stdin.buffer.readline()\n"
            f"mode = {mode!r}\n"
            f"frame = {encoded.encode()!r}\n"
            "if mode == 'frame_then_exit':\n"
            "    sys.stdout.buffer.write(frame)\n"
            "    sys.stdout.buffer.flush()\n"
            "    sys.exit(0)\n"
            "done = threading.Event()\n"
            "def emit():\n"
            "    while not done.is_set():\n"
            "        sys.stdout.buffer.write(b' ' if mode == 'partial' else frame)\n"
            "        sys.stdout.buffer.flush()\n"
            "        done.wait(.04)\n"
            "if mode in ('partial', 'heartbeat'):\n"
            "    writer = threading.Thread(target=emit, daemon=True)\n"
            "    writer.start()\n"
            "sys.stdin.buffer.read()\n"
            "done.set()\n"
            "if mode in ('partial', 'heartbeat'):\n"
            "    writer.join(timeout=1)\n"
        )
        process = original_popen([arguments[0], "-u", "-c", program], **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr("aegis.processes.subprocess.Popen", launch)
    return owned


@pytest.mark.parametrize("options", [
    {"worker_timeout": .049}, {"worker_timeout": 301},
    {"worker_timeout": float("nan")}, {"worker_timeout": float("inf")},
    {"worker_timeout": True}, {"worker_timeout": "1"},
    {"restart_limit": -1}, {"restart_limit": 11},
    {"restart_limit": True}, {"restart_limit": 1.0},
    {"restart_delay": .009}, {"restart_delay": 61},
    {"restart_delay": float("nan")}, {"restart_delay": float("inf")},
    {"restart_delay": True}, {"restart_delay": "1"},
])
def test_recovery_options_reject_unbounded_or_ambiguous_values(tmp_path, options):
    with pytest.raises(ValueError):
        ProcessCollector([_source()], tmp_path, **options)


@pytest.mark.parametrize("options", [
    {"worker_timeout": .05, "restart_limit": 0, "restart_delay": .01},
    {"worker_timeout": 300, "restart_limit": 10, "restart_delay": 60},
])
def test_recovery_options_accept_the_documented_finite_boundaries(tmp_path, options):
    ProcessCollector([_source()], tmp_path, **options)


@pytest.mark.parametrize("mode", ["silent", "partial"])
def test_live_worker_without_complete_frames_is_reaped_and_recovery_is_bounded(
        tmp_path, monkeypatch, mode):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    owned = _probe_workers(monkeypatch, [mode])

    async def check():
        collector = ProcessCollector([_source()], tmp_path, worker_timeout=.3,
                                     restart_limit=1, restart_delay=.02)
        await collector.start()
        try:
            await _until(lambda: owned)
            assert owned[0].poll() is None
            await _until(lambda: collector.ingestion()[0]["workerState"] == "FAILED",
                         message="A live worker without valid frames exhausted no recovery budget.")
            assert len(owned) == 2  # Initial attempt and one permitted replacement.
            assert all(process.poll() is not None for process in owned)
            assert collector.ingestion()[0]["restartCount"] == 1
            assert collector.ingestion()[0]["workerPid"] is None
            assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
            await asyncio.sleep(.4)
            assert len(owned) == 2
        finally:
            await collector.stop()
        assert all(process.poll() is not None for process in owned)

    asyncio.run(check())


def test_valid_frame_then_exit_does_not_reset_the_lifetime_recovery_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    owned = _probe_workers(monkeypatch, ["frame_then_exit"])

    async def check():
        collector = ProcessCollector([_source()], tmp_path, worker_timeout=.5,
                                     restart_limit=2, restart_delay=.02)
        await collector.start()
        try:
            await _until(lambda: collector.ingestion()[0]["workerState"] == "FAILED")
            assert collector._workers[0].summary is not None
            assert len(owned) == 3
            assert all(process.poll() is not None for process in owned)
            assert collector.ingestion()[0]["restartCount"] == 2
            assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
            await asyncio.sleep(.2)
            assert len(owned) == 3
        finally:
            await collector.stop()

    asyncio.run(check())


def test_zero_recovery_budget_still_allows_the_initial_worker_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    owned = _probe_workers(monkeypatch, ["frame_then_exit"])

    async def check():
        collector = ProcessCollector([_source()], tmp_path, worker_timeout=.5,
                                     restart_limit=0, restart_delay=.02)
        await collector.start()
        try:
            await _until(lambda: collector.ingestion()[0]["workerState"] == "FAILED")
            assert len(owned) == 1
            assert owned[0].poll() is not None
            assert collector.ingestion()[0]["restartCount"] == 0
            assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
        finally:
            await collector.stop()

    asyncio.run(check())


def test_explicit_stop_start_starts_a_new_recovery_budget_after_terminal_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    owned = _probe_workers(monkeypatch, ["frame_then_exit"])

    async def check():
        collector = ProcessCollector([_source()], tmp_path, worker_timeout=.5,
                                     restart_limit=1, restart_delay=.02)
        for expected_attempts in (2, 4):
            await collector.start()
            try:
                await _until(lambda: len(owned) == expected_attempts
                    and collector.ingestion()[0]["workerState"] == "FAILED")
                assert all(process.poll() is not None for process in owned)
                assert collector.ingestion()[0]["restartCount"] == 1
                assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
            finally:
                await collector.stop()
        assert len(owned) == 4

    asyncio.run(check())


def test_normal_stop_reaps_a_silent_worker_without_starting_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    owned = _probe_workers(monkeypatch, ["silent"])

    async def check():
        collector = ProcessCollector([_source()], tmp_path)
        await collector.start()
        try:
            await _until(lambda: owned)
            assert owned[0].poll() is None
        finally:
            await collector.stop()
        assert len(owned) == 1
        assert owned[0].poll() is not None
        assert collector.ingestion()[0]["restartCount"] == 0
        await collector.stop()

    asyncio.run(check())


def test_failed_owned_process_cleanup_blocks_a_replacement_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    original_popen = subprocess.Popen
    owned = []

    class Unreapable:
        """Inject the OS cleanup failure while retaining a real owned pipe reader."""
        def __init__(self, process):
            self.process = process

        def __getattr__(self, name):
            return getattr(self.process, name)

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("synthetic owned worker", timeout)

        def kill(self):
            raise OSError("Synthetic process cleanup failure.")

    def launch(arguments, **kwargs):
        assert arguments[-2:] == ["-m", "aegis.module_worker"]
        process = original_popen([arguments[0], "-u", "-c",
            "import sys,time; sys.stdin.buffer.readline(); time.sleep(30)"], **kwargs)
        owned.append(process)
        return Unreapable(process)

    monkeypatch.setattr("aegis.processes.subprocess.Popen", launch)

    async def check():
        collector = ProcessCollector([_source()], tmp_path, worker_timeout=.3,
                                     restart_limit=3, restart_delay=.02)
        await collector.start()
        try:
            await _until(lambda: collector.ingestion()[0]["workerState"] == "FAILED")
            assert collector.connections()[0]["reasonCode"] == "WORKER_CLEANUP_FAILED"
            assert len(owned) == 1 and owned[0].poll() is None
            await asyncio.sleep(.2)
            assert len(owned) == 1, "A replacement could write beside an uncollected old worker."
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(collector.stop(), timeout=3)
            with pytest.raises(RuntimeError):
                await collector.start()
        finally:
            # Bypass only the test's injected cleanup failure, then release the real reader.
            for process in owned:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            try:
                await collector.stop()
            except RuntimeError:
                pass  # The injected cleanup failure may remain latched until operator recovery.
        assert all(process.poll() is not None for process in owned)

    asyncio.run(check())


@contextmanager
def _snapshot_server(mode):
    replay = mode == "no_new_data"
    now = datetime.now(timezone.utc)
    snapshot = {"protocolVersion": "1.1" if replay else "1.0",
        "source": "SIMULATOR", "deviceId": "RECOVERY-01",
        "runId": str(uuid4()), "sequence": 0 if replay else 7,
        "observedAtUtc": (now - timedelta(seconds=10) if replay else now).isoformat(),
        "deviceStateVersion": 1, "modeId": "PRODUCTION",
        "signals": [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"}
                    for key, unit in SIGNAL_UNITS.items()]}
    body = json.dumps(snapshot).encode()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(time.monotonic())
            if mode == "timeout":
                time.sleep(.8)  # Real worker HTTP timeout is .5 seconds.
            response_body = body
            if replay:
                after = parse_qs(urlsplit(self.path).query).get("after", ["-1"])[0]
                response_body = json.dumps({"streamVersion": "1.0", "deviceId": "RECOVERY-01",
                    "runId": snapshot["runId"], "oldestSequence": 0, "latestSequence": 0,
                    "snapshots": [snapshot] if after == "-1" else []}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        configured = _source(server.server_port, poll_ms=60000 if mode == "long_idle" else 50)
        if replay:
            configured = configured.model_copy(update={"transport": "replay"})
        yield configured, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("mode", ["no_new_data", "long_idle", "timeout"])
def test_real_worker_heartbeats_keep_healthy_idle_and_handled_timeouts_out_of_recovery(
        tmp_path, monkeypatch, mode):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    with _snapshot_server(mode) as (configured, requests):
        async def check():
            collector = ProcessCollector([configured], tmp_path, worker_timeout=2.5,
                                         restart_limit=1, restart_delay=.02)
            await collector.start()
            process = None
            try:
                expected = "POLL_TIMEOUT" if mode == "timeout" else "NO_NEW_DATA"
                await _until(lambda: collector.connections()[0]["reasonCode"] == expected)
                process = collector._workers[0].process
                first_receipt = collector._workers[0].summary[1]
                await asyncio.sleep(2.8)  # Longer than the configured heartbeat deadline.
                assert process.poll() is None
                assert collector._workers[0].process is process
                assert collector._workers[0].summary[1] > first_receipt
                assert collector.ingestion()[0]["restartCount"] == 0
                assert collector.ingestion()[0]["workerState"] == "RUNNING"
                assert collector.connections()[0]["reasonCode"] == expected
                if mode != "timeout":
                    assert collector.connections()[0]["lastSequence"] == (0 if mode == "no_new_data" else 7)
                    assert collector.device_health(configured.deviceId).observationEvaluation == "UNAVAILABLE"
                if mode == "no_new_data":
                    assert collector.ingestion()[0]["lastCommittedSequence"] == 0
                    assert collector.connections()[0]["acceptedBatches"] == 1
                if mode == "long_idle":
                    assert len(requests) == 1
            finally:
                await collector.stop()
            assert process is not None and process.poll() is not None
            assert collector.ingestion()[0]["restartCount"] == 0

        asyncio.run(check())


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _archive_prefix(path, device_id, *, through=None):
    # Cursor and original rows are captured in one read transaction.
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        cursor = db.execute("SELECT run_id,sequence FROM ingest_cursors WHERE device_id=?",
                            (device_id,)).fetchone()
        assert cursor is not None
        boundary = cursor[1] if through is None else through
        rows = db.execute("""SELECT device_id,run_id,sequence,observed_at_utc,received_at_utc,
            equipment_id,module_id,payload_json FROM raw_batches
            WHERE device_id=? AND run_id=? AND sequence<=? ORDER BY sequence""",
            (device_id, cursor[0], boundary)).fetchall()
        return cursor, rows


@pytest.mark.skipif(sys.platform != "linux", reason="Owned-worker SIGSTOP integration requires Linux.")
def test_sigstop_worker_recovers_its_archive_while_peer_keeps_committing(tmp_path):
    archive_root = tmp_path / "modules"
    evidence = {"scope": "Linux synthetic local workers; no physical device or Windows-service claim."}
    with running_stack(2, replay=True, isolate=True, signal_count=256, interval_ms=50,
                       poll_ms=100, archive_path=archive_root) as stack:
        def ready():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if len(rows) == 2 and all(row["workerPid"] and row["signalCount"] == 256
                and row["lastCommittedSequence"] is not None and row["lastCommittedSequence"] >= 20
                for row in rows) else None

        before = wait_until(ready, stack.active_processes, seconds=30)
        original_pids = [row["workerPid"] for row in before]
        stack.worker_pids.update(original_pids)
        paths = list(archive_root.glob("*.sqlite3"))
        assert len(paths) == 2
        target_path = next(path for path in paths if stack.device_ids[0] in path.name)
        old_cursor, old_rows = _archive_prefix(target_path, stack.device_ids[0])
        assert old_rows and all(len(json.loads(row[-1])["signals"]) == 256 for row in old_rows)
        observed_cursors = []
        stopped = False
        try:
            os.kill(original_pids[0], signal.SIGSTOP)
            stopped = True
            assert _alive(original_pids[0])

            def peer_progressed_during_stall():
                rows = stack.client.get("/v1/ingestion").json()
                return rows if rows[1]["workerPid"] == original_pids[1] and (
                    rows[1]["lastCommittedSequence"] or 0) > before[1]["lastCommittedSequence"] + 5 else None

            stalled = wait_until(peer_progressed_during_stall, stack.active_processes, seconds=5)
            assert stalled[0]["workerPid"] == original_pids[0]
            assert stack.client.get("/v1/health").status_code == 200

            def recovered():
                rows = stack.client.get("/v1/ingestion").json()
                target, peer = rows
                if target["lastCommittedRunId"] == old_cursor[0] and target["lastCommittedSequence"] is not None:
                    observed_cursors.append(target["lastCommittedSequence"])
                assert peer["workerPid"] == original_pids[1]
                assert peer["restartCount"] == 0
                return rows if target["workerPid"] not in {None, original_pids[0]} and (
                    target["lastCommittedRunId"] == old_cursor[0]
                    and (target["lastCommittedSequence"] or -1) > old_cursor[1]
                    and target["workerState"] == "RUNNING"
                    and (peer["lastCommittedSequence"] or -1) > before[1]["lastCommittedSequence"]) else None

            after = wait_until(recovered, stack.active_processes, seconds=45)
            stack.worker_pids.update(row["workerPid"] for row in after if row["workerPid"])
            wait_until(lambda: not _alive(original_pids[0]), stack.active_processes, seconds=5)
            stopped = False
            assert after[0]["restartCount"] == before[0]["restartCount"] + 1
            assert observed_cursors == sorted(observed_cursors)
            assert all(sequence >= before[0]["lastCommittedSequence"] for sequence in observed_cursors)
            assert set(archive_root.glob("*.sqlite3")) == set(paths)
            new_cursor, prefix = _archive_prefix(target_path, stack.device_ids[0], through=old_cursor[1])
            assert new_cursor[0] == old_cursor[0] and new_cursor[1] > old_cursor[1]
            assert prefix == old_rows, "Recovery rewrote an original row or its receipt timestamp."
            assert stack.client.get("/v1/health").status_code == 200
            evidence.update(workerRestarted=True, peerPidUnchanged=True,
                originalPrefixAndReceivedTimestampsPreserved=True,
                originalPrefixRows=len(old_rows), cursorAdvanced=True)
        finally:
            if stopped:
                try:
                    os.kill(original_pids[0], signal.SIGCONT)
                except ProcessLookupError:
                    pass

        stack.stop_api()
        assert all(not _alive(pid) for pid in stack.worker_pids)
        for path in paths:
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert db.execute("SELECT COUNT(*) FROM ingest_gaps").fetchone()[0] == 0
                rows = db.execute("SELECT run_id,sequence FROM raw_batches ORDER BY sequence").fetchall()
                assert len({row[0] for row in rows}) == 1
                assert [row[1] for row in rows] == list(range(rows[-1][1] + 1))
                cursor = db.execute("SELECT run_id,sequence FROM ingest_cursors").fetchall()
                assert cursor == [(rows[-1][0], rows[-1][1])]
        evidence.update(allOwnedWorkersExited=True, contiguousArchives=True, sqliteIntegrity=True)
    assert all(process.poll() is not None for process in stack.processes)
    (tmp_path / "worker-recovery-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
