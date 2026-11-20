"""Live HTTP to real module IPC and raw archives, using synthetic local producers."""

import json
import os
import signal
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from scripts.benchmark_emulators import running_stack, wait_pid_exit, wait_until


def _read(path, statement, parameters=()):
    # Inspect committed evidence without joining the module's writer connection.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
        return db.execute(statement, parameters).fetchall()


def _module_paths(directory):
    result = {}
    for path in directory.glob("*.sqlite3"):
        devices = _read(path, "SELECT DISTINCT device_id FROM raw_batches")
        assert len(devices) == 1
        result[devices[0][0]] = path
    return result


def _original_rows(path, device, run):
    # At 200ms, sequences75/76 are real DATA-QUALITY MISSING samples after15seconds.
    return _read(path, """SELECT * FROM raw_batches
        WHERE device_id=? AND run_id=? AND sequence IN (75,76) ORDER BY sequence""", (device, run))


def _cursor(path, device):
    return _read(path, "SELECT run_id,sequence FROM ingest_cursors WHERE device_id=?", (device,))[0]


def _selection(rows):
    originals = [json.loads(row[7]) for row in rows]
    return {"runId": rows[0][1], "fromUtc": originals[0]["observedAtUtc"],
            "toUtc": (datetime.fromisoformat(originals[-1]["observedAtUtc"].replace("Z", "+00:00"))
                      + timedelta(microseconds=1)).isoformat()}, originals


def _assert_history(body, rows, originals):
    assert body["schemaVersion"] == "raw-query-1" and body["kind"] == "RAW_ARCHIVE_HISTORY"
    assert body["selectionComplete"] is True and body["sourceCoverage"] == "NOT_ASSERTED"
    assert body["matchedBatches"] == 2 and len(body["batches"]) == 2
    assert body["limits"]["matchingBatches"] == 64 and body["limits"]["signals"] == 16
    for actual, row, original in zip(body["batches"], rows, originals):
        expected = {key: value for key, value in original.items() if key != "signals"}
        expected.update(receivedAtUtc=row[4], equipmentId=row[5], moduleId=row[6])
        readings = {item["signalKey"]: item for item in original["signals"]}
        expected["signals"] = [
            {"signalKey": key, "present": key in readings,
             "reading": {field: readings[key][field] for field in ("value", "unit", "quality")}
             if key in readings else None}
            for key in ("actualFlowLpm", "aux0248", "not.generated")]
        assert actual == expected
        assert len(original["signals"]) == 256 and actual["source"] == "SIMULATOR"
        assert actual["signals"][2] == {"signalKey": "not.generated", "present": False, "reading": None}


def test_raw_history_live_http_reads_real_module_ipc_archives_and_survives_worker_restart(tmp_path):
    archive_root = tmp_path / "modules"
    known_worker_pids = set()
    with running_stack(2, replay=True, isolate=True, signal_count=256, interval_ms=200,
                       poll_ms=100, scenarios=["DATA-QUALITY", "NORMAL"], archive_path=archive_root) as stack:
        def committed_missing_samples():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if len(rows) == 2 and all(row["workerPid"] and row["signalCount"] == 256
                and row["archiveEnabled"] and row["lastCommittedSequence"] is not None
                and row["lastCommittedSequence"] >= 77 for row in rows) else None

        before = wait_until(committed_missing_samples, stack.active_processes, seconds=25)
        pids = [row["workerPid"] for row in before]
        assert len(set(pids)) == 2 and stack.api.pid not in pids
        known_worker_pids.update(pids)
        stack.worker_pids.update(pids)
        paths = _module_paths(archive_root)
        assert set(paths) == set(stack.device_ids)
        target, peer = stack.device_ids
        run = before[0]["lastCommittedRunId"]
        stored = _original_rows(paths[target], target, run)
        assert len(stored) == 2
        query, originals = _selection(stored)
        for original in originals:
            reading = next(item for item in original["signals"] if item["signalKey"] == "actualFlowLpm")
            assert reading == {"signalKey": "actualFlowLpm", "value": None, "unit": "L/min", "quality": "MISSING"}
        assert all(row[5:7] == ("CMP-DEMO-01", "MODULE-01") for row in stored)
        history_url = f"/v1/devices/{target}/raw-history"
        history_query = dict(query, signals="actualFlowLpm,aux0248,not.generated")
        response = stack.client.get(history_url, params=history_query)
        assert response.status_code == 200
        first = response.json()
        _assert_history(first, stored, originals)
        assert all(batch["signals"][0]["present"] and batch["signals"][0]["reading"]["quality"] == "MISSING"
                   and batch["signals"][0]["reading"]["value"] is None for batch in first["batches"])

        catalog_response = stack.client.get(f"/v1/devices/{target}/raw-tags", params=query)
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()
        assert catalog["kind"] == "RAW_ARCHIVE_OBSERVED_TAGS" and "batches" not in catalog
        tags = {item["signalKey"]: item for item in catalog["tags"]}
        assert len(tags) == 256 and "not.generated" not in tags
        assert tags["actualFlowLpm"] == {"signalKey": "actualFlowLpm", "units": ["L/min"],
            "presentBatches": 2, "absentBatches": 0, "qualities": {"MISSING": 2}}
        assert tags["aux0248"]["qualities"] == {"GOOD": 2}

        # A different module/run cannot expose the target's archive records.
        peer_run = before[1]["lastCommittedRunId"]
        peer_stored = _original_rows(paths[peer], peer, peer_run)
        peer_query, peer_originals = _selection(peer_stored)
        peer_response = stack.client.get(f"/v1/devices/{peer}/raw-history", params=dict(peer_query,
            signals="actualFlowLpm,aux0248,not.generated"))
        assert peer_response.status_code == 200
        _assert_history(peer_response.json(), peer_stored, peer_originals)
        assert all(batch["moduleId"] == "MODULE-02" for batch in peer_response.json()["batches"])
        assert all(batch["signals"][0]["reading"]["quality"] == "GOOD"
                   for batch in peer_response.json()["batches"])
        cross_run = stack.client.get(f"/v1/devices/{peer}/raw-history", params=history_query)
        assert cross_run.status_code == 200 and cross_run.json()["batches"] == []

        limited = stack.client.get(history_url, params=dict(history_query, maxBatches=1))
        assert limited.status_code == 413 and limited.json()["code"] == "RAW_QUERY_LIMIT"
        assert "batches" not in limited.json() and "tags" not in limited.json()
        assert _original_rows(paths[target], target, run) == stored
        cursor_after_reads = _cursor(paths[target], target)
        assert cursor_after_reads[0] == run and cursor_after_reads[1] >= before[0]["lastCommittedSequence"]

        def progressed_after_reads():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if all(row["workerPid"] == pid and row["lastCommittedSequence"] > old["lastCommittedSequence"]
                               for row, pid, old in zip(rows, pids, before)) else None

        progressed = wait_until(progressed_after_reads, stack.active_processes, seconds=5)
        assert progressed[1]["restartCount"] == 0
        # Terminate only the still-current worker PID reported by this owned supervisor.
        assert stack.client.get("/v1/ingestion").json()[0]["workerPid"] == pids[0]
        os.kill(pids[0], signal.SIGTERM)

        def recovered_without_peer_restart():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if (rows[0]["workerPid"] not in {None, pids[0]} and rows[0]["lastCommittedRunId"] == run
                and rows[0]["lastCommittedSequence"] > progressed[0]["lastCommittedSequence"]
                and rows[1]["workerPid"] == pids[1]
                and rows[1]["lastCommittedSequence"] > progressed[1]["lastCommittedSequence"]) else None

        after = wait_until(recovered_without_peer_restart, stack.active_processes, seconds=12)
        assert after[0]["restartCount"] == 1 and after[1]["restartCount"] == 0
        known_worker_pids.add(after[0]["workerPid"])
        stack.worker_pids.add(after[0]["workerPid"])
        wait_pid_exit(pids[0], seconds=5)
        resumed_response = stack.client.get(history_url, params=history_query)
        assert resumed_response.status_code == 200
        resumed = resumed_response.json()
        _assert_history(resumed, stored, originals)
        assert resumed["batches"] == first["batches"] and resumed["tags"] == first["tags"]
        assert _original_rows(paths[target], target, run) == stored
        assert _cursor(paths[target], target)[1] >= cursor_after_reads[1]
        live_rows = stack.client.get(f"/v1/devices/{target}/observations").json()
        assert len(live_rows) == 8 and not any(item["signalKey"].startswith("aux") for item in live_rows)
        context = stack.client.get("/v1/health?includeSourceContext=true").json()["sourceContext"]
        assert context["realEquipmentSupported"] is False
        assert all(item["declaredOrigin"] == "SYNTHETIC" and item["acquisitionMethod"] == "SOURCE_BUFFER_REPLAY"
                   for item in context["sources"])
        stack.stop_api()
        for pid in known_worker_pids:
            wait_pid_exit(pid, seconds=5)

        # Once owned writers stop, the cursor must name an actual contiguous committed prefix.
        committed_counts = []
        for device, path in paths.items():
            rows = _read(path, "SELECT run_id,sequence FROM raw_batches WHERE device_id=? ORDER BY sequence", (device,))
            assert [row[1] for row in rows] == list(range(rows[-1][1] + 1))
            assert len({row[0] for row in rows}) == 1
            assert _cursor(path, device) == rows[-1]
            assert _read(path, "SELECT COUNT(*) FROM ingest_gaps")[0][0] == 0
            assert _read(path, "PRAGMA integrity_check")[0][0] == "ok"
            committed_counts.append(len(rows))
        assert _original_rows(paths[target], target, run) == stored

    assert all(process.poll() is not None for process in stack.processes)
    for pid in known_worker_pids:
        wait_pid_exit(pid, seconds=5)
    (tmp_path / "raw-ipc-evidence.json").write_text(json.dumps({
        "scope": "Synthetic Linux/local HTTP with real emulator/API/module processes, stdin/stdout IPC and SQLite WAL.",
        "moduleCount": 2, "signalsPerModule": 256, "sampleIntervalMs": 200,
        "initialDistinctModuleWorkers": 2, "ownedWorkerGenerationsExited": len(known_worker_pids),
        "targetWorkerRestartCount": after[0]["restartCount"], "peerWorkerRestartCount": after[1]["restartCount"],
        "capturedHistoricalBatches": len(stored), "actualMissingNullSamples": 2, "explicitAbsentTagSamples": 2,
        "rawNumericTagMatchedStoredValue": True, "storedSourceMetadataAndFirstReceiptPreserved": True,
        "historicalResponseIdenticalAfterWorkerReplay": True, "queryLimit413WithoutPartialSuccess": True,
        "peerContinuedDuringQueriesAndTargetRecovery": True, "committedBatchesAfterStop": committed_counts,
        "cursorMatchesCommittedContiguousRows": True, "archiveIntegrity": "ok", "knownGaps": 0,
        "ownedDirectChildrenStopped": all(process.poll() is not None for process in stack.processes),
        "allKnownOwnedModuleWorkersExited": True,
        "notRun": ["Windows", "Actual equipment", "Customer data", "Long-duration/high-load performance"]
    }, indent=2) + "\n")
