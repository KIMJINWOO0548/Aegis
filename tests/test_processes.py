"""Bounded module summaries and real worker process lifetime checks."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis.collector import Collector, RawSnapshot, SIGNAL_UNITS, SourceConfig
from aegis.archive import RawArchive
from aegis.contracts import Observation, Warning
from aegis.evaluation import Evaluator
from aegis.module_worker import build_frame
from aegis.processes import MAX_FRAME_BYTES, ProcessCollector, decode_frame
from scripts.benchmark_emulators import running_stack, wait_until


TOKEN = "module-worker-test-" + "x" * 32


def source(device="CMP-01.PMC-01", port=19101):
    return SourceConfig(deviceId=device, baseUrl=f"http://127.0.0.1:{port}")


def frame():
    configured = source()
    collector = Collector([configured], {"AEGIS_EMULATOR_TOKEN": TOKEN})
    now = datetime.now(timezone.utc)
    signals = [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"}
               for key, unit in SIGNAL_UNITS.items()]
    signals += [{"signalKey": f"raw.{index}", "value": float(index), "unit": "count", "quality": "GOOD"}
                for index in range(248)]
    snapshot = RawSnapshot(protocolVersion="1.1", source="SIMULATOR", deviceId=configured.deviceId,
        runId=str(uuid4()), sequence=7, observedAtUtc=now, deviceStateVersion=1,
        modeId="PRODUCTION", signals=signals)
    collector._accept(0, snapshot)
    return build_frame(collector)


def test_summary_contains_eight_flow_rows_but_no_raw_signal_payload():
    payload = frame()
    decoded = decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId)
    assert len(decoded.observations) == 8
    assert decoded.ingestion["signalCount"] == 256
    assert b"raw.247" not in json.dumps(payload).encode()


@pytest.mark.parametrize("change", [
    lambda payload: payload["connection"].update(deviceId="OTHER"),
    lambda payload: payload["health"].update(deviceId="OTHER"),
    lambda payload: payload["observations"].append(payload["observations"][0]),
    lambda payload: payload.update(rawSnapshots=[]),
])
def test_summary_rejects_mixed_identity_and_unexpected_payload(change):
    payload = frame()
    change(payload)
    with pytest.raises(ValueError):
        decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId)


@pytest.mark.parametrize("section,fields", [
    ("connection", {"state": "UNKNOWN"}),
    ("connection", {"state": []}),
    ("connection", {"acceptedBatches": True}),
    ("connection", {"acceptedBatches": "1"}),
    ("connection", {"lastSequence": 2**63}),
    ("connection", {"lastReceivedAtUtc": "2026-10-19T00:00:00"}),
    ("connection", {"pollLatencyMs": float("nan")}),
    ("connection", {"pollLatencyMs": 10**400}),
    ("connection", {"reasonCode": "BAD\nREASON"}),
    ("ingestion", {"transport": "unknown"}),
    ("ingestion", {"signalCount": 513}),
    ("ingestion", {"archiveEnabled": 1}),
    ("ingestion", {"equipmentId": "../OTHER"}),
    ("ingestion", {"lastCommittedRunId": "not-a-uuid", "lastCommittedSequence": 1}),
    ("ingestion", {"pendingBatches": -1}),
    ("ingestion", {"observedLagMs": float("inf")}),
    ("ingestion", {"observedLagMs": 10**400}),
    ("ingestion", {"lastObservedAtUtc": "2026-10-19T00:00:00"}),
    ("diagnostics", {"enabled": "false"}),
    ("diagnostics", {"enabled": True, "modelVersion": None}),
    ("diagnostics", {"evaluation": "UNKNOWN"}),
    ("diagnostics", {"sampleCount": True}),
    ("diagnostics", {"sampleCount": 61}),
    ("diagnostics", {"flowErrorEwmaLpm": float("nan")}),
    ("diagnostics", {"issues": [{"signalKey": "raw.247", "reasonCode": "BAD", "message": "Bad signal."}]}),
    ("diagnostics", {"issues": [{"signalKey": "actualFlowLpm", "reasonCode": "BAD", "message": "Bad\nsignal."}]}),
    ("diagnostics", {"issues": [None]}),
])
def test_summary_rejects_invalid_metadata_values(section, fields):
    payload = frame()
    payload[section].update(fields)
    with pytest.raises(ValueError):
        decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId)


def test_summary_keeps_valid_enabled_diagnostics_unchanged():
    payload = frame()
    payload["diagnostics"].update(enabled=True, modelVersion="ewma-flow-dev-1", sampleCount=60,
        evaluation="ANOMALOUS", flowErrorEwmaLpm=2.5,
        issues=[{"signalKey": "actualFlowLpm", "reasonCode": "BAD", "message": "유효하지 않은 신호입니다."}])
    decoded = decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId)
    assert decoded.diagnostics == payload["diagnostics"]


def test_summary_accepts_evaluator_issues_for_each_contracted_signal():
    payload = frame()
    evaluator = Evaluator(source().deviceId)
    evaluator.accept([Observation.model_validate(dict(row, quality="MISSING", value=None))
                      for row in payload["observations"]])
    payload["diagnostics"] = evaluator.diagnostics()
    assert len(payload["diagnostics"]["issues"]) == 8
    decoded = decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId)
    assert decoded.diagnostics == payload["diagnostics"]


def test_summary_rejects_oversized_and_incomplete_lines():
    for value in (b"x" * (MAX_FRAME_BYTES + 1), b"{}", b"{}\n"):
        with pytest.raises(ValueError):
            decode_frame(value, source().deviceId)


def test_archive_paths_are_stable_and_distinct_on_windows(tmp_path):
    sources = [source("CON"), source("a", 19102), source("A", 19103)]
    first = ProcessCollector(sources, tmp_path)
    reordered = ProcessCollector(list(reversed(sources)), tmp_path)
    assert first.archive_paths == reordered.archive_paths
    assert len({str(path).casefold() for path in first.archive_paths.values()}) == 3
    assert all(path.parent == tmp_path for path in first.archive_paths.values())


def test_cached_worker_failure_marks_data_unavailable_and_keeps_open_warning(tmp_path):
    collector = ProcessCollector([source()], tmp_path)
    payload = frame()
    warning = Warning(id="open-warning", deviceId=source().deviceId, state="OPEN",
        createdAtUtc=datetime.now(timezone.utc), recommendationId="inspect-flow-tracking")
    payload["warnings"] = [warning.model_dump(mode="json")]
    collector._workers[0].summary = (decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId), time.monotonic())
    assert collector.connections()[0]["reasonCode"] == "WORKER_UNAVAILABLE"
    assert all(row.quality == "STALE" for row in collector.observations(source().deviceId))
    assert collector.device_health(source().deviceId).observationEvaluation == "UNAVAILABLE"
    assert collector.device_health(source().deviceId).normalReferencePercentile is None
    assert collector.diagnostics(source().deviceId)["evaluation"] == "UNAVAILABLE"
    assert collector.warnings()[0].state == "OPEN"
    collector.observations(source().deviceId)[0].model_dump(mode="json")
    collector.device_health(source().deviceId).model_dump(mode="json")


def test_startup_diagnostics_are_consistent_before_a_worker_summary(tmp_path):
    collector = ProcessCollector([source()], tmp_path, evaluate=True)
    assert collector.diagnostics(source().deviceId)["enabled"] is False
    assert collector.device_health(source().deviceId).modelLifecycle == "NO_MODEL"


def test_recent_worker_summary_cannot_refresh_old_observations(tmp_path):
    collector = ProcessCollector([source()], tmp_path)
    payload = frame()
    old = (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat()
    for row in payload["observations"]:
        row["observedAtUtc"] = old
    worker = collector._workers[0]
    worker.summary = (decode_frame(json.dumps(payload).encode() + b"\n", source().deviceId), time.monotonic())
    worker.process = SimpleNamespace(poll=lambda: None)
    worker.state = "RUNNING"
    assert collector.connections()[0]["state"] == "STALE"
    assert collector.connections()[0]["reasonCode"] == "NO_NEW_DATA"
    assert all(row.quality == "STALE" for row in collector.observations(source().deviceId))
    assert collector.device_health(source().deviceId).observationEvaluation == "UNAVAILABLE"


def test_recent_observations_from_an_expired_worker_summary_are_stale(tmp_path):
    collector = ProcessCollector([source()], tmp_path)
    worker = collector._workers[0]
    worker.summary = (decode_frame(json.dumps(frame()).encode() + b"\n", source().deviceId), time.monotonic() - 4)
    worker.process = SimpleNamespace(poll=lambda: None)
    worker.state = "RUNNING"
    assert collector.connections()[0]["reasonCode"] == "WORKER_UNAVAILABLE"
    assert collector.observations(source().deviceId)[0].quality == "STALE"


def test_raw_only_acquisition_freshness_does_not_use_evaluator_startup_time(tmp_path):
    configured = source().model_copy(update={"transport": "replay"})
    producer = Collector([configured], {"AEGIS_EMULATOR_TOKEN": TOKEN}, evaluate=True,
                         archive=RawArchive(tmp_path / "raw.sqlite3"))
    now = datetime.now(timezone.utc)
    producer._evaluators[configured.deviceId]._at = now - timedelta(seconds=10)
    producer._accept(0, RawSnapshot(protocolVersion="1.1", source="SIMULATOR", deviceId=configured.deviceId,
        runId=str(uuid4()), sequence=1, observedAtUtc=now, deviceStateVersion=1,
        modeId="PRODUCTION", signals=[{"signalKey": "scheduler.queueDepth", "value": 1,
                                      "unit": "count", "quality": "GOOD"}]))
    payload = build_frame(producer)
    assert payload["connection"]["state"] == "CONNECTED"
    assert payload["observations"] == []
    collector = ProcessCollector([configured], tmp_path)
    worker = collector._workers[0]
    worker.summary = (decode_frame(json.dumps(payload).encode() + b"\n", configured.deviceId), time.monotonic())
    worker.process = SimpleNamespace(pid=123, poll=lambda: None)
    worker.state = "RUNNING"
    assert collector.connections()[0]["state"] == "CONNECTED"
    assert collector.ingestion()[0]["observedLagMs"] < 1000
    assert len(collector.diagnostics(configured.deviceId)["issues"]) == 8
    # A delayed pipe delivery must not refresh an older source observation.
    payload["ingestion"]["lastObservedAtUtc"] = (now - timedelta(seconds=4)).isoformat()
    payload["ingestion"]["observedLagMs"] = 0
    worker.summary = (decode_frame(json.dumps(payload).encode() + b"\n", configured.deviceId), time.monotonic())
    assert collector.connections()[0]["state"] == "STALE"
    assert collector.ingestion()[0]["observedLagMs"] >= 4000


def test_locked_module_archive_does_not_block_peer_commits(tmp_path):
    with running_stack(2, replay=True, isolate=True, signal_count=256, interval_ms=50,
                       archive_path=tmp_path / "modules") as stack:
        def ready():
            rows = stack.client.get("/v1/ingestion").json()
            return rows if all(row["lastCommittedSequence"] is not None for row in rows) else None

        before = wait_until(ready, stack.active_processes)
        paths = list((tmp_path / "modules").glob("*.sqlite3"))
        locked = next(path for path in paths if stack.device_ids[0] in path.name)
        with sqlite3.connect(locked, isolation_level=None) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            try:
                def peer_progressed():
                    rows = stack.client.get("/v1/ingestion").json()
                    return rows if rows[1]["lastCommittedSequence"] > before[1]["lastCommittedSequence"] + 10 else None

                after = wait_until(peer_progressed, stack.active_processes)
                assert after[1]["workerPid"] == before[1]["workerPid"]
                assert stack.client.get("/v1/health").status_code == 200
            finally:
                blocker.execute("ROLLBACK")


def test_real_workers_restart_independently_and_stop_on_parent_pipe_eof(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)

    async def check():
        collector = ProcessCollector([source(), source("CMP-01.CMC-01", 19102)], tmp_path)
        await collector.start()
        try:
            deadline = time.monotonic() + 15
            while not all(worker.summary is not None for worker in collector._workers):
                assert time.monotonic() < deadline, "Worker summaries did not arrive."
                await asyncio.sleep(.05)
            old = [worker.process for worker in collector._workers]
            before_restarts = collector.ingestion()[0]["restartCount"]
            old[0].kill()
            deadline = time.monotonic() + 15
            while (collector.ingestion()[0]["workerPid"] in {None, old[0].pid}
                   or collector._workers[0].state != "RUNNING"):
                assert time.monotonic() < deadline, "Crashed worker did not restart and publish a summary."
                await asyncio.sleep(.05)
            assert collector._workers[1].process.pid == old[1].pid
            assert old[1].poll() is None
            assert collector.ingestion()[0]["restartCount"] == before_restarts + 1
            owned = [*old, collector._workers[0].process]
        finally:
            await collector.stop()
        assert all(process.poll() is not None for process in owned)
        assert all(process.returncode == 0 for process in owned[1:])

    # API uses this loop on Windows; asyncio subprocess APIs cannot work on it.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())


def test_stop_reaps_an_owned_worker_that_never_finishes_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    original_popen = subprocess.Popen

    def delayed(args, **kwargs):
        return original_popen([args[0], "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr("aegis.processes.subprocess.Popen", delayed)

    async def check():
        collector = ProcessCollector([source()], tmp_path)
        await collector.start()
        try:
            while collector._workers[0].process is None:
                await asyncio.sleep(.01)
            process = collector._workers[0].process
            assert process.poll() is None
            assert collector._workers[0].state == "STARTING"
        finally:
            await collector.stop()
        assert process.poll() is not None
        assert process.returncode != 0

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())


def test_worker_retries_after_stderr_path_open_failure_and_recovers_when_repaired(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)

    async def check():
        collector = ProcessCollector([source()], tmp_path)
        blocked_path = collector._workers[0].archive.with_suffix(".stderr.log")
        blocked_path.mkdir()
        await collector.start()
        try:
            await asyncio.sleep(.05)
            assert not collector._tasks[0].done()
            assert collector.ingestion()[0]["workerState"] == "UNAVAILABLE"
            blocked_path.rmdir()
            deadline = time.monotonic() + 10
            while collector._workers[0].summary is None:
                assert time.monotonic() < deadline, "Worker did not recover after its stderr path was repaired."
                await asyncio.sleep(.05)
            assert collector._workers[0].process.poll() is None
            assert collector.ingestion()[0]["restartCount"] == 1
            assert TOKEN not in blocked_path.read_text(encoding="utf-8")
        finally:
            await collector.stop()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())


def test_stopping_reader_drains_large_pending_output_until_child_exits(tmp_path):
    collector = ProcessCollector([source()], tmp_path)
    collector._stopping = True
    process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 2097152 + b'\\n'); sys.stdout.buffer.flush()"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    worker = collector._workers[0]
    worker.process = process
    try:
        collector._read(worker, process)
        assert process.wait(timeout=2) == 0
        assert worker.summary is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
