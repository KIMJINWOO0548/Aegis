"""Synthetic hundreds-of-tags, transient and crash/replay checks in real processes."""

import json
import sqlite3
import time
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from scripts.benchmark_emulators import running_stack, wait_until


def test_hundreds_of_signals_survive_api_restart_and_keep_subsecond_fault(tmp_path):
    archive = tmp_path / "raw.sqlite3"
    with running_stack(2, replay=True, signal_count=256, interval_ms=10, poll_ms=100,
                       scenarios=["TRANSIENT", "NORMAL"], archive_path=archive, evaluate=True) as stack:
        def caught_up():
            rows = stack.client.get("/v1/connections").json()
            return rows if len(rows) == 2 and all(row["state"] in {"CONNECTED", "STALE"} and row["lastSequence"] >= 150 for row in rows) else None
        before = wait_until(caught_up, stack.active_processes, seconds=20)
        metadata = stack.client.get("/v1/ingestion").json()
        assert all(row["signalCount"] == 256 and row["archiveEnabled"] and row["equipmentId"] == "CMP-DEMO-01" for row in metadata)
        stack.stop_api()
        time.sleep(.5)
        started = time.perf_counter()
        stack.start_api()
        def resumed():
            rows = stack.client.get("/v1/connections").json()
            return rows if len(rows) == 2 and all(row["state"] in {"CONNECTED", "STALE"} and row["lastSequence"] > old["lastSequence"] + 50
                                                for row, old in zip(rows, before)) else None
        try:
            after = wait_until(resumed, stack.active_processes, seconds=20)
        except TimeoutError:
            print("Restart connection states:", stack.client.get("/v1/connections").json())
            print("Restart ingestion states:", stack.client.get("/v1/ingestion").json())
            raise
        restart_ms = (time.perf_counter() - started) * 1000
        stack.stop_api()
        with closing(sqlite3.connect(archive)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM ingest_gaps").fetchone()[0] == 0
            saved = []
            for device in stack.device_ids:
                rows = db.execute("SELECT sequence,payload_json FROM raw_batches WHERE device_id=? ORDER BY sequence", (device,)).fetchall()
                sequences = [row[0] for row in rows]
                assert sequences == list(range(sequences[-1] + 1))
                assert all(len(json.loads(row[1])["signals"]) == 256 for row in rows)
                saved.append({"deviceId": device, "batches": len(rows), "signalValues": len(rows) * 256})
            rows = db.execute("SELECT payload_json FROM raw_batches WHERE device_id=? ORDER BY sequence", (stack.device_ids[0],)).fetchall()
            bad = [json.loads(row[0]) for row in rows if json.loads(row[0])["signals"][1]["value"] == -5]
            assert bad and all(snapshot["signals"][1]["quality"] == "GOOD" for snapshot in bad)
            assert any(a["sequence"] + 1 == b["sequence"] for a, b in zip(bad, bad[1:]))
        evidence = {"scope": "Real separate processes; 2 logical modules of one synthetic equipment, 256 signals each, 10ms sampling.",
                    "sourceSampleIntervalMs": 10, "collectorPollIntervalMs": 100, "transientDurationMs": 100,
                    "apiRestartCatchupMs": round(restart_ms, 3), "before": before, "after": after,
                    "rawArchive": saved, "subsecondFaultSamples": len(bad), "archiveGaps": 0, "integrity": "ok"}
    evidence["ownedChildrenCleanedUp"] = all(process.poll() is not None for process in stack.processes)
    path = Path(__file__).resolve().parents[1] / "docs/evidence/replay-integration.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def test_demo_cli_keeps_raw_archive_after_owned_processes_exit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    archive = tmp_path / "retained.sqlite3"
    result = subprocess.run([sys.executable, "scripts/run_emulator_demo.py", "--headless", "--count", "1",
        "--seconds", "1", "--signal-count", "256", "--interval-ms", "100",
        "--archive", str(archive)], cwd=root, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert "All owned demo processes stopped." in result.stdout
    assert archive.is_file()
    with closing(sqlite3.connect(archive)) as db:
        assert db.execute("SELECT COUNT(*) FROM raw_batches").fetchone()[0] > 0
        assert len(json.loads(db.execute("SELECT payload_json FROM raw_batches LIMIT 1").fetchone()[0])["signals"]) == 256
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
