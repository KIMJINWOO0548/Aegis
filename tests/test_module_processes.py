"""Responsibility isolation, independent restart and supervisor loss in real processes."""

import ctypes
import json
import os
import signal
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from scripts.benchmark_emulators import running_stack, wait_until


def alive(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_module_workers_keep_peer_running_restart_cursor_and_exit_with_supervisor(tmp_path):
    archive_root = tmp_path / "modules"
    with running_stack(2, replay=True, isolate=True, signal_count=256, interval_ms=20, poll_ms=100,
                       scenarios=["TRANSIENT", "NORMAL"], archive_path=archive_root, evaluate=True) as stack:
        def ready():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if len(rows) == 2 and all(row["workerPid"] and row["signalCount"] == 256 and
                (row["lastCommittedSequence"] or 0) >= 75 for row in rows) else None
        before = wait_until(ready, stack.active_processes, seconds=30)
        pids = [row["workerPid"] for row in before]
        assert len(set(pids)) == 2 and stack.api.pid not in pids
        os.kill(pids[0], signal.SIGTERM)
        def independently_restarted():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if rows[0]["workerPid"] not in {None, pids[0]} and rows[0]["signalCount"] == 256 and (rows[0]["lastCommittedSequence"] or -1) > before[0]["lastCommittedSequence"] and rows[1]["workerPid"] == pids[1] and (rows[1]["lastCommittedSequence"] or -1) > before[1]["lastCommittedSequence"] else None
        after = wait_until(independently_restarted, stack.active_processes, seconds=30)
        assert after[0]["restartCount"] >= 1 and after[1]["restartCount"] == 0
        assert stack.client.get("/v1/health").status_code == 200
        assert len(stack.client.get(f"/v1/devices/{stack.device_ids[0]}/observations").json()) == 8
        current_pids = [row["workerPid"] for row in after]
        stack.stop_api()
        wait_until(lambda: all(not alive(pid) for pid in [*pids, *current_pids]), stack.active_processes, seconds=10)
        db_files = list(archive_root.glob("*.sqlite3"))
        assert len(db_files) == 2
        saved = []
        for path in db_files:
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert db.execute("SELECT COUNT(*) FROM ingest_gaps").fetchone()[0] == 0
                rows = db.execute("SELECT device_id,sequence,payload_json FROM raw_batches ORDER BY sequence").fetchall()
                assert [row[1] for row in rows] == list(range(rows[-1][1] + 1))
                assert all(len(json.loads(row[2])["signals"]) == 256 for row in rows)
                saved.append({"deviceId": rows[0][0], "batches": len(rows), "signalValues": len(rows) * 256})
        evidence = {"scope": "2 independent module acquisition/archive/advisory processes and one integration API.",
                    "sampleIntervalMs": 20, "signalsPerModule": 256, "before": before, "after": after,
                    "peerWorkerPidUnchanged": True, "workerRestarted": True,
                    "workersExitedAfterSupervisorLoss": True, "contiguousArchives": saved}
    evidence["ownedChildrenCleanedUp"] = all(process.poll() is not None for process in stack.processes)
    (Path(__file__).resolve().parents[1] / "docs/evidence/module-processes.json").write_text(json.dumps(evidence, indent=2) + "\n")
