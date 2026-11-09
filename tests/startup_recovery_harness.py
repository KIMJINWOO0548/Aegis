"""Linux-only, externally timed startup-I/O scenarios with owned-child reaping.

The supervisor is deliberately outside the asyncio process under test. Its
subreaper owns orphaned descendants if the application's event loop gets stuck.
Only synthetic control metadata is written; worker configuration is never logged.
"""

import argparse
import asyncio
import ctypes
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "startup-recovery-synthetic-" + "x" * 32


def _record(directory, **entry):
    # One short append per event, also usable while the application loop is blocked.
    descriptor = os.open(directory / "events.jsonl", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(entry, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(descriptor)


def _events(directory):
    path = directory / "events.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # The other process may still be appending its final short line.
    return entries


def _identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
        return fields[19], int(fields[2])  # starttime and process group, not the account fields.
    except FileNotFoundError:
        return None


def _register(directory, event, process):
    identity = _identity(process.pid)
    _record(directory, event=event, pid=process.pid,
            starttime=identity[0] if identity else None)


def _signal(entry, value, group):
    identity = _identity(entry["pid"])
    if identity is None or identity != (entry["starttime"], group):
        return
    try:
        os.kill(entry["pid"], value)
    except ProcessLookupError:
        pass


def _cleanup(application, directory):
    # First unblock a genuine blocked write and let the real collector reap it.
    for entry in _events(directory):
        if entry.get("event") == "worker_spawned":
            _signal(entry, signal.SIGKILL, application.pid)
    try:
        application.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(application.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        application.wait(timeout=5)
    # PR_SET_CHILD_SUBREAPER makes any surviving grandchildren ours to wait for.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-application.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid == 0:
            try:
                os.killpg(application.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            time.sleep(.02)
    return False


def supervise(scenario, directory):
    directory.mkdir(parents=True, exist_ok=True)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER.
        raise OSError("The isolated harness could not own orphaned test descendants.")
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT),
                       PYTHONDONTWRITEBYTECODE="1", AEGIS_EMULATOR_TOKEN=TOKEN)
    with (directory / "application.log").open("wb") as output:
        application = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            "--inner", scenario, str(directory)], cwd=ROOT, env=environment,
            stdout=output, stderr=output, start_new_session=True)
        _register(directory, "application_spawned", application)
        expired = False
        try:
            application.wait(timeout=25 if scenario == "peer" else 12)
        except subprocess.TimeoutExpired:
            expired = True
        finally:
            reaped = _cleanup(application, directory)
    entries = _events(directory)
    writes = [entry for entry in entries if entry.get("event") == "configuration_write"]
    result_path = directory / "inner-result.json"
    result = {"scenario": scenario, "deadline_expired": expired,
        "descendants_reaped": reaped, "application_exit": application.returncode,
        "configuration_writes": writes,
        "registered_workers": sum(entry.get("event") == "worker_spawned" for entry in entries),
        "inner": json.loads(result_path.read_text()) if result_path.exists() else None}
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, separators=(",", ":")))
    return 0


class ObservedInput:
    def __init__(self, stream, process, capacity, directory, pending, *, prefix_bytes=0):
        self.stream, self.process = stream, process
        self.capacity, self.directory, self.pending = capacity, directory, pending
        self.prefix_bytes = prefix_bytes

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data):
        from aegis.module_worker import WorkerConfig
        config = WorkerConfig.model_validate_json(b" " * self.prefix_bytes + data)
        for value in (config.archivePath, config.logPath):
            if value is None:
                continue
            path = Path(value)
            assert len(value) < 4096 and len(os.fsencode(path)) < 4096
            assert all(len(os.fsencode(part)) < 255 for part in path.parts)
        assert len(data) + self.prefix_bytes > self.capacity
        self.pending.set()
        _record(self.directory, event="configuration_write", payload_bytes=len(data),
                prefill_bytes=self.prefix_bytes, total_wire_bytes=len(data) + self.prefix_bytes,
                capacity=self.capacity, config_valid=True, paths_valid=True)
        try:
            return self.stream.write(data)
        except BaseException:
            self.pending.clear()
            _record(self.directory, event="configuration_io_finished")
            raise

    def flush(self):
        try:
            return self.stream.flush()
        finally:
            self.pending.clear()
            _record(self.directory, event="configuration_io_finished")


async def _until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "The application did not finish its bounded scenario."
        await asyncio.sleep(.02)


async def application(scenario, directory):
    import fcntl
    from aegis.collector import SourceConfig
    from aegis.processes import ProcessCollector
    import aegis.processes as processes

    if scenario == "peer":
        return await peer_application(directory)
    os.environ["AEGIS_EMULATOR_TOKEN"] = TOKEN
    # Each component is 180 UTF-8 bytes; both archive and sidecars fit Linux paths.
    archive = directory / "archive"
    for _ in range(15):
        archive /= "가" * 60
    configured = SourceConfig(deviceId="STARTUP-01", baseUrl="http://127.0.0.1:19101")
    collector = ProcessCollector([configured], archive, log_path=directory / "enabled.jsonl",
                                 worker_timeout=.2, restart_limit=1 if scenario == "budget" else 0,
                                 restart_delay=.01)
    original_popen = subprocess.Popen
    owned, pending = [], threading.Event()
    frame = None
    if scenario == "budget":
        from aegis.collector import Collector
        from aegis.module_worker import build_frame
        frame = (json.dumps(build_frame(Collector([configured],
            {"AEGIS_EMULATOR_TOKEN": TOKEN})), separators=(",", ":")) + "\n").encode()

    def launch(arguments, **kwargs):
        assert arguments[-2:] == ["-m", "aegis.module_worker"]
        assert not pending.is_set(), "A new writer started before the old configuration I/O finished."
        if owned:
            assert all(writer.done() for writer in collector._writers)
        actual_arguments = arguments
        if frame is not None:
            # A real child sends valid summaries while deliberately never reading config.
            marker = json.dumps({"event": "premature_frame_written"}) + "\n"
            program = ("import os,sys,time\n"
                f"frame = {frame!r}\n"
                "sys.stdout.buffer.write(frame); sys.stdout.buffer.flush()\n"
                f"fd=os.open({str(directory / 'events.jsonl')!r},os.O_WRONLY|os.O_APPEND)\n"
                f"os.write(fd,{marker.encode()!r}); os.close(fd)\n"
                "while True:\n"
                "    time.sleep(.02)\n"
                "    sys.stdout.buffer.write(frame); sys.stdout.buffer.flush()\n")
            actual_arguments = [arguments[0], "-u", "-c", program]
        process = original_popen(actual_arguments, **kwargs)
        # Register before fcntl, signals, or assertions can fail.
        owned.append(process)
        _register(directory, "worker_spawned", process)
        try:
            fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            capacity = fcntl.fcntl(process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
            assert capacity == 4096, "The test could not establish its small-pipe precondition."
            if frame is None:
                os.kill(process.pid, signal.SIGSTOP)
                waited, status = os.waitpid(process.pid, os.WUNTRACED)
                assert waited == process.pid and os.WIFSTOPPED(status)
            process.stdin = ObservedInput(process.stdin, process, capacity, directory, pending)
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise

    processes.subprocess.Popen = launch
    ticks = [0]
    ticking = True

    async def ticker():
        while ticking:
            ticks[0] += 1
            if ticks[0] == 1 or ticks[0] % 10 == 0:
                _record(directory, event="loop_tick", ticks=ticks[0])
            await asyncio.sleep(.02)

    tick_task = asyncio.create_task(ticker())
    try:
        await collector.start()
        try:
            if scenario == "stop":
                await _until(pending.is_set)
                _record(directory, event="stop_requested")
                started = time.monotonic()
                await collector.stop()
                assert time.monotonic() - started < 6
                assert collector.ingestion()[0]["restartCount"] == 0
            else:
                await _until(lambda: collector.ingestion()[0]["workerState"] == "FAILED")
                assert ticks[0] >= 3, "The collector lifecycle blocked the shared event loop."
                assert collector.connections()[0]["reasonCode"] == "WORKER_RECOVERY_EXHAUSTED"
                assert collector.ingestion()[0]["restartCount"] == (1 if scenario == "budget" else 0)
            assert len(owned) == (2 if scenario == "budget" else 1)
            assert all(process.poll() is not None for process in owned)
            assert not pending.is_set()
            assert collector._workers[0].summary is None
        finally:
            await collector.stop()
        return {"ok": True, "ticks": ticks[0], "attempts": len(owned),
                "all_workers_exited": all(process.poll() is not None for process in owned)}
    finally:
        ticking = False
        await tick_task
        processes.subprocess.Popen = original_popen
        for process in owned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@contextmanager
def replay_source(device_id):
    """A local synthetic producer with a stable run and contiguous original batches."""
    from aegis.collector import SIGNAL_UNITS, SourceConfig

    run_id = str(uuid4())
    began, epoch = time.monotonic(), datetime.now(timezone.utc)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            first = max(0, int(query.get("after", ["-1"])[0]) + 1)
            latest = int((time.monotonic() - began) / .05)
            snapshots = [{"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device_id,
                "runId": run_id, "sequence": sequence,
                "observedAtUtc": (epoch + timedelta(milliseconds=sequence * 50)).isoformat(),
                "deviceStateVersion": 1, "modeId": "PRODUCTION",
                "signals": [{"signalKey": key, "value": float(sequence + 1), "unit": unit,
                             "quality": "GOOD"} for key, unit in SIGNAL_UNITS.items()]}
                for sequence in range(first, min(latest + 1, first + 64))]
            body = json.dumps({"streamVersion": "1.0", "deviceId": device_id, "runId": run_id,
                "oldestSequence": 0, "latestSequence": latest, "snapshots": snapshots}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield SourceConfig(deviceId=device_id, baseUrl=f"http://127.0.0.1:{server.server_port}",
                           transport="replay", pollIntervalMs=50)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def archive_prefix(path, device_id, through=None):
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


async def peer_application(directory):
    import fcntl
    from aegis.processes import ProcessCollector
    import aegis.processes as processes

    os.environ["AEGIS_EMULATOR_TOKEN"] = TOKEN
    # Real SQLite storage uses a short path; its pathname limit can be stricter
    # than the filesystem/WorkerConfig limit tested in the three I/O-only cases.
    archive = directory / "archive"
    original_popen = subprocess.Popen
    owned, pending = [], threading.Event()
    fault = []

    def launch(arguments, **kwargs):
        assert arguments[-2:] == ["-m", "aegis.module_worker"]
        if fault:
            assert not pending.is_set(), "The next writer started before old configuration I/O finished."
            assert fault[0].poll() is not None, "The next writer overlapped the old process."
            assert all(writer.done() for writer in collector._writers)
        process = original_popen(arguments, **kwargs)
        owned.append(process)
        _register(directory, "worker_spawned", process)
        if len(owned) != 3:
            return process
        # The initial two workers are healthy. Only the killed target's next launch is stopped.
        fault.append(process)
        try:
            fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            capacity = fcntl.fcntl(process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
            assert capacity == 4096
            os.kill(process.pid, signal.SIGSTOP)
            waited, status = os.waitpid(process.pid, os.WUNTRACED)
            assert waited == process.pid and os.WIFSTOPPED(status)
            # Legal leading JSON whitespace creates real pipe backpressure without
            # changing the short archive path or requiring SQLite to open a long one.
            assert os.write(process.stdin.fileno(), b" " * capacity) == capacity
            process.stdin = ObservedInput(process.stdin, process, capacity, directory, pending,
                                          prefix_bytes=capacity)
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise

    processes.subprocess.Popen = launch
    ticks, ticking = [0], True

    async def ticker():
        while ticking:
            ticks[0] += 1
            await asyncio.sleep(.02)

    tick_task = asyncio.create_task(ticker())
    try:
        with replay_source("STARTUP-01") as target, replay_source("STARTUP-02") as peer:
            collector = ProcessCollector([target, peer], archive, log_path=directory / "enabled.jsonl",
                                         worker_timeout=2.5, restart_limit=2, restart_delay=.02)
            await collector.start()
            try:
                await _until(lambda: all(row["lastCommittedSequence"] is not None
                    and row["lastCommittedSequence"] >= 5 for row in collector.ingestion()))
                before = collector.ingestion()
                paths = dict(collector.archive_paths)
                old_cursor, original = archive_prefix(paths[target.deviceId], target.deviceId)
                peer_cursor, peer_original = archive_prefix(paths[peer.deviceId], peer.deviceId)
                old_target = collector._workers[0].process
                peer_process = collector._workers[1].process
                assert old_target.pid != peer_process.pid
                old_target.kill()
                await _until(pending.is_set)
                tick_before = ticks[0]
                pending_peer_cursor = archive_prefix(paths[peer.deviceId], peer.deviceId)[0]
                await _until(lambda: archive_prefix(paths[peer.deviceId], peer.deviceId)[0][1]
                             > pending_peer_cursor[1] + 5)
                assert ticks[0] >= tick_before + 3
                assert pending.is_set(), "The test did not observe storage during pending configuration."
                assert collector._workers[1].process is peer_process and peer_process.poll() is None
                assert collector.ingestion()[1]["restartCount"] == 0
                _record(directory, event="peer_progress_during_pending", loop_responsive=True)

                observed = []

                def recovered():
                    rows = collector.ingestion()
                    current = rows[0]
                    if current["lastCommittedRunId"] == old_cursor[0] and current["lastCommittedSequence"] is not None:
                        observed.append(current["lastCommittedSequence"])
                    assert rows[1]["workerPid"] == peer_process.pid
                    assert rows[1]["restartCount"] == 0
                    return (current["workerState"] == "RUNNING"
                        and current["workerPid"] not in {old_target.pid, fault[0].pid, None}
                        and current["lastCommittedRunId"] == old_cursor[0]
                        and (current["lastCommittedSequence"] or -1) > old_cursor[1])

                await _until(recovered, seconds=12)
                assert observed == sorted(observed)
                assert all(sequence >= before[0]["lastCommittedSequence"] for sequence in observed)
                assert collector.ingestion()[0]["restartCount"] == 2
                assert not pending.is_set() and fault[0].poll() is not None
                assert len(owned) == 4
                assert set(archive.glob("*.sqlite3")) == set(paths.values())
                assert archive_prefix(paths[target.deviceId], target.deviceId, old_cursor[1])[1] == original
                assert archive_prefix(paths[peer.deviceId], peer.deviceId, peer_cursor[1])[1] == peer_original
            finally:
                await collector.stop()
            assert all(process.poll() is not None for process in owned)
            for path in paths.values():
                with closing(sqlite3.connect(path)) as db:
                    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    assert db.execute("SELECT COUNT(*) FROM ingest_gaps").fetchone()[0] == 0
                    rows = db.execute("SELECT run_id,sequence FROM raw_batches ORDER BY sequence").fetchall()
                    assert len({row[0] for row in rows}) == 1
                    assert [row[1] for row in rows] == list(range(rows[-1][1] + 1))
                    assert db.execute("SELECT run_id,sequence FROM ingest_cursors").fetchall() == [rows[-1]]
            return {"ok": True, "ticks": ticks[0], "attempts": len(owned),
                "peer_pid_preserved": True, "peer_committed_while_config_pending": True,
                "original_prefix_and_receipts_preserved": True, "contiguous_archives": True,
                "sqlite_integrity": True, "all_workers_exited": True,
                "scope": "Two real Linux workers, eight-signal synthetic replay, valid-JSON whitespace pipe prefill."}
    finally:
        ticking = False
        await tick_task
        processes.subprocess.Popen = original_popen
        for process in owned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def inner(scenario, directory):
    try:
        result = asyncio.run(application(scenario, directory))
    except Exception as error:
        # Fixed type only: never serialize source configuration, tokens, or exception text.
        frames = traceback.extract_tb(error.__traceback__)
        location = frames[-1]
        result = {"ok": False, "error_type": type(error).__name__,
                  "error_file": Path(location.filename).name, "error_line": location.lineno,
                  "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno}
                                   for frame in frames]}
    (directory / "inner-result.json").write_text(json.dumps(result) + "\n")
    return 0 if result["ok"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("scenario")
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    return inner(arguments.scenario, arguments.directory) if arguments.inner else supervise(
        arguments.scenario, arguments.directory)


if __name__ == "__main__":
    raise SystemExit(main())
