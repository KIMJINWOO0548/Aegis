"""Raw replay -> module-owned evidence -> bounded pipe -> authenticated API."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app
from aegis.archive import RawArchive
from aegis.collector import Collector, RawSnapshot, SIGNAL_UNITS, SourceConfig
from aegis.module_worker import build_frame
from aegis.processes import ProcessCollector, decode_frame
from scripts.benchmark_emulators import ROOT, running_stack, wait_until


TOKEN = "signal-review-test-" + "x" * 32
DEVICE = "CMP-01.PMC-01"


def payload(sequence, at, run):
    values = [10, 6 if 123 <= sequence < 133 else 10, 250, 50, 50, 2, 25, 10]
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": DEVICE,
        "runId": run, "sequence": sequence, "observedAtUtc": at.isoformat().replace("+00:00", "Z"),
        "deviceStateVersion": 1, "modeId": "PRODUCTION", "signals": [
            {"signalKey": key, "value": float(value), "unit": unit, "quality": "GOOD"}
            for (key, unit), value in zip(SIGNAL_UNITS.items(), values)]}


def configured(transport="snapshot"):
    return SourceConfig(deviceId=DEVICE, baseUrl="http://127.0.0.1:9101", transport=transport)


def client(collector, token=TOKEN):
    return TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                      headers={"X-Aegis-Token": token})


def populated():
    now = datetime.now(timezone.utc)
    collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, evaluate=True, utc_now=lambda: now)
    run = str(uuid4())
    for index in range(220):
        collector._accept(0, RawSnapshot.model_validate(payload(index, now - timedelta(seconds=2.19 - index / 100), run)), history=True)
    return collector, now


def test_replay_stores_unmodified_valid_pulse_and_api_retains_review_after_latest_recovers(tmp_path):
    async def check():
        now, run = datetime.now(timezone.utc), str(uuid4())
        frames = [payload(index, now - timedelta(seconds=2.19 - index / 100), run) for index in range(220)]
        def respond(request):
            after = int(request.url.params["after"])
            return httpx.Response(200, json={"streamVersion": "1.0", "deviceId": DEVICE, "runId": run,
                "oldestSequence": 0, "latestSequence": 219, "snapshots": frames[after + 1:after + 51]})
        archive = RawArchive(tmp_path / "raw.sqlite3")
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
                collector = Collector([configured("replay")], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=upstream,
                                      archive=archive, evaluate=True, utc_now=lambda: now)
                for _ in range(5):
                    await collector.poll_once(0)
                response = client(collector).get(f"/v1/devices/{DEVICE}/signal-review")
                assert response.status_code == 200
                result = response.json()
                assert result["state"] == "REVIEW_REQUIRED"
                assert result["rawErrorLpm"] == 0
                assert result["events"][0]["rawErrorMinLpm"] == -4
                assert result["events"][0]["startObservationId"] == f"{run}:123:actualFlowLpm"
                assert result["events"][0]["sampleCount"] == 10
                assert await archive.cursor(DEVICE) == (run, 219)
                assert client(collector, "x" * 32).get(f"/v1/devices/{DEVICE}/signal-review").status_code == 401
                assert client(collector).get("/v1/devices/OTHER/signal-review").status_code == 404
        finally:
            await archive.close()
        with sqlite3.connect(tmp_path / "raw.sqlite3") as db:
            rows = db.execute("SELECT payload_json FROM raw_batches ORDER BY sequence").fetchall()
            assert [json.loads(row[0]) for row in rows] == frames
            assert all("signalReview" not in row[0] and "scenario" not in row[0] for row in rows)
    asyncio.run(check())


def test_pipe_preserves_review_and_stale_supervisor_clears_current_trend_without_erasing_history(tmp_path):
    collector, now = populated()
    summary = decode_frame(json.dumps(build_frame(collector)).encode() + b"\n", DEVICE)
    assert summary.signalReview.events[0].disposition == "UNRESOLVED"
    parent = ProcessCollector([configured()], tmp_path, evaluate=True)
    worker = parent._workers[0]
    worker.summary = (summary, time.monotonic())
    worker.process = SimpleNamespace(poll=lambda: None)
    worker.state = "RUNNING"
    result = parent.signal_review(DEVICE)
    assert result["state"] == "REVIEW_REQUIRED"
    worker.summary = (summary, time.monotonic() - 5)
    result = parent.signal_review(DEVICE)
    assert result["state"] == "UNAVAILABLE"
    assert result["trendErrorLpm"] is None
    assert result["rawErrorLpm"] is None
    assert result["events"][0]["peakDeviationLpm"] == 4
    result["events"][0]["peakDeviationLpm"] = 999
    assert parent.signal_review(DEVICE)["events"][0]["peakDeviationLpm"] == 4


@pytest.mark.parametrize("corruption", [
    lambda result: result.update(deviceId="OTHER"),
    lambda result: result.update(secret="unexpected"),
    lambda result: result.update(rawErrorLpm=float("nan")),
    lambda result: result.update(eventCount=True),
    lambda result: result.update(contextSampleCount=257),
    lambda result: result.update(state="NOISE_REMOVED"),
    lambda result: result["events"][0].update(disposition="NOISE"),
    lambda result: result["events"][0].update(rawErrorMinLpm=90, rawErrorMaxLpm=-90),
    lambda result: result["events"][0].update(relatedSignalKeys=["raw.1"]),
    lambda result: result.update(events=result["events"] * 33),
])
def test_pipe_rejects_invalid_review_evidence(corruption):
    collector, _ = populated()
    frame = build_frame(collector)
    corruption(frame["signalReview"])
    with pytest.raises(ValueError):
        decode_frame(json.dumps(frame).encode() + b"\n", DEVICE)


def test_disabled_and_stale_direct_collector_are_explicit():
    collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN})
    assert client(collector).get(f"/v1/devices/{DEVICE}/signal-review").json()["state"] == "DISABLED"
    collector, now = populated()
    collector._now = lambda: now + timedelta(seconds=5)
    result = client(collector).get(f"/v1/devices/{DEVICE}/signal-review").json()
    assert result["state"] == "UNAVAILABLE"
    assert result["trendErrorLpm"] is None
    assert result["events"][0]["disposition"] == "UNRESOLVED"


def test_small_sequence_gap_interrupts_evidence_even_when_time_gap_is_under_one_second():
    now, run = datetime.now(timezone.utc), str(uuid4())
    collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, evaluate=True, utc_now=lambda: now)
    for index in range(124):
        collector._accept(0, RawSnapshot.model_validate(payload(index, now - timedelta(seconds=1.3 - index / 100), run)))
    collector._accept(0, RawSnapshot.model_validate(payload(126, now - timedelta(seconds=.04), run)))
    result = collector.signal_review(DEVICE)
    assert result["events"][0]["endReason"] == "GAP"
    assert result["state"] == "WARMING"
    assert result["contextSampleCount"] == 0


def test_real_noisy_emulator_module_archive_and_api_preserve_short_review(tmp_path):
    with running_stack(1, replay=True, isolate=True, evaluate=True, signal_count=256,
            interval_ms=50, poll_ms=100, scenarios=["NOISY-FLOW"], archive_path=tmp_path) as stack:
        device = stack.device_ids[0]
        trace = []
        def detected():
            result = stack.client.get(f"/v1/devices/{device}/signal-review").json()
            if not trace or (result["state"], result["eventCount"]) != (trace[-1]["state"], trace[-1]["eventCount"]):
                trace.append({"state": result["state"], "eventCount": result["eventCount"],
                    "contextReason": result["contextReason"], "contextSampleCount": result["contextSampleCount"],
                    "observedAtUtc": result["observedAtUtc"], "events": result["events"]})
            return result if result["state"] == "REVIEW_REQUIRED" and any(
                event["endReason"] == "RETURNED_TO_CONTEXT" for event in result["events"]) else None
        try:
            result = wait_until(detected, stack.active_processes, seconds=30)
        except TimeoutError:
            evidence = {"trace": trace, "ingestion": stack.client.get("/v1/ingestion").json(),
                "connections": stack.client.get("/v1/connections").json()}
            (ROOT / "docs/evidence/signal-review-process-failure.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            raise
        event = next(event for event in result["events"] if event["endReason"] == "RETURNED_TO_CONTEXT")
        assert event["disposition"] == "UNRESOLVED"
        assert "supplyPressureKpa" in event["relatedSignalKeys"]
        ingestion = stack.client.get("/v1/ingestion").json()[0]
        assert ingestion["signalCount"] == 256
        assert ingestion["workerPid"] != stack.api.pid
    assert all(process.poll() is not None for process in stack.processes)
    with sqlite3.connect(next(tmp_path.glob("*.sqlite3"))) as db:
        rows = db.execute("SELECT sequence,payload_json FROM raw_batches ORDER BY sequence").fetchall()
        assert [row[0] for row in rows] == list(range(rows[-1][0] + 1))
        raw = next(json.loads(row[1]) for row in rows if f":{row[0]}:actualFlowLpm" in event["startObservationId"])
        assert all(row["quality"] == "GOOD" for row in raw["signals"])
        assert 0 < raw["signals"][1]["value"] < 8
        assert len(raw["signals"]) == 256
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    evidence = {"scope": "Real independent emulator, module acquisition/archive/review process and API; synthetic input only.",
        "intervalMs": 50, "signalsPerModule": 256, "review": result, "ingestion": ingestion,
        "archivedBatches": len(rows), "contiguousRawPrefix": True, "rawPeakQuality": "GOOD",
        "ownedProcessesStopped": True}
    (ROOT / "docs/evidence/signal-review-process.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
