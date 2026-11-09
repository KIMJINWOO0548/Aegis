"""Existing simulator provenance and original-time preservation controls.

These cases characterize existing rejection/storage behavior. They are not a RED
for optional health metadata, real-device support, or a new source-time fallback.
"""

import asyncio
import copy
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from aegis.archive import RawArchive
from aegis.collector import Collector, RawSnapshot, SIGNAL_UNITS, SourceConfig


TOKEN = "synthetic-source-integrity-" + "x" * 32
DEVICE = "SOURCE-CONTROL-01"


def _sample(transport, run, sequence, observed, count):
    signals = [{"signalKey": key, "value": float(sequence + 1), "unit": unit, "quality": "GOOD"}
               for key, unit in SIGNAL_UNITS.items()]
    signals.extend({"signalKey": f"synthetic.tag{index:04d}", "value": float(index),
                    "unit": "count", "quality": "GOOD"} for index in range(count - 8))
    return {"protocolVersion": "1.0" if transport == "snapshot" else "1.1",
            "source": "SIMULATOR", "deviceId": DEVICE, "runId": run,
            "sequence": sequence, "observedAtUtc": observed.isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION", "signals": signals}


def _state(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        return {
            "raw": db.execute("SELECT * FROM raw_batches ORDER BY sequence").fetchall(),
            "cursors": db.execute("SELECT * FROM ingest_cursors ORDER BY device_id").fetchall(),
            "gaps": db.execute("SELECT * FROM ingest_gaps ORDER BY first_sequence").fetchall(),
        }


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _published_originals(collector):
    # A rejected source intentionally changes quality/status, not these original fields.
    return [row.model_dump(exclude={"quality"}) for row in collector.observations(DEVICE)]


@asynccontextmanager
async def _session(directory, transport, count, *, observed=None):
    clock = [datetime.now(timezone.utc)]
    observed = observed or clock[0] - timedelta(milliseconds=500)
    run = str(uuid4())
    sample = _sample(transport, run, 0, observed, count)
    body = sample if transport == "snapshot" else {
        "streamVersion": "1.0", "deviceId": DEVICE, "runId": run,
        "oldestSequence": 0, "latestSequence": 0, "snapshots": [sample]}
    current = {"body": body, "fixed": False}
    path = directory / "raw.sqlite3"
    archive = RawArchive(path) if transport == "replay" else None
    if archive is not None:
        await archive.start()

    def respond(request):
        assert request.url.path == ("/v1/snapshot" if transport == "snapshot" else "/v1/batches")
        payload = copy.deepcopy(current["body"])
        if transport == "replay" and not current["fixed"]:
            after = int(request.url.params["after"])
            payload["snapshots"] = [row for row in payload["snapshots"] if row["sequence"] > after]
        return httpx.Response(200, json=payload)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            source = SourceConfig(deviceId=DEVICE, baseUrl="http://127.0.0.1:19101", transport=transport)
            collector = Collector([source], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                                  archive=archive, utc_now=lambda: clock[0], evaluate=True)
            yield SimpleNamespace(collector=collector, archive=archive, path=path, sample=sample,
                clock=clock, observed=observed, run=run, current=current, source=source)
    finally:
        if archive is not None:
            await archive.close()


@pytest.mark.parametrize("transport", ["snapshot", "replay"])
@pytest.mark.parametrize("fault", ["real_source", "read_only_source", "missing_source",
                                    "missing_sequence", "missing_observed_at", "missing_run_id"])
def test_unsupported_or_missing_original_identity_is_rejected_without_replacing_accepted_data(
        tmp_path, transport, fault):
    async def check():
        # Snapshot v1.0 has eight flow signals; 512-tag originals use replay v1.1.
        count = 8 if transport == "snapshot" else 512
        async with _session(tmp_path, transport, count) as session:
            await session.collector.poll_once(0)
            before = session.collector.connections()[0]
            original = _published_originals(session.collector)
            ingestion = session.collector.ingestion()[0]
            assert before["acceptedBatches"] == 1 and before["lastSequence"] == 0
            assert ingestion["signalCount"] == count
            saved = _state(session.path) if session.archive is not None else None

            bad_sequence = 1 if transport == "snapshot" else 2
            bad = _sample(transport, session.run, bad_sequence,
                          session.observed + timedelta(milliseconds=bad_sequence * 10), count)
            if fault == "real_source":
                bad["source"] = "REAL"
            elif fault == "read_only_source":
                bad["source"] = "READ_ONLY"
            else:
                field = {"missing_source": "source", "missing_sequence": "sequence",
                         "missing_observed_at": "observedAtUtc", "missing_run_id": "runId"}[fault]
                bad.pop(field)
            if transport == "snapshot":
                session.current["body"] = bad
            else:
                good = _sample(transport, session.run, 1,
                               session.observed + timedelta(milliseconds=10), count)
                # A valid first row must not be committed before the later invalid row is rejected.
                session.current["body"].update(latestSequence=2, snapshots=[good, bad])
                session.current["fixed"] = True
            session.clock[0] += timedelta(milliseconds=250)
            await session.collector.poll_once(0)

            after = session.collector.connections()[0]
            assert after["state"] == "REJECTED" and after["reasonCode"] == "INVALID_SNAPSHOT"
            assert after["rejectedBatches"] == before["rejectedBatches"] + 1
            assert after["acceptedBatches"] == before["acceptedBatches"]
            assert after["lastSequence"] == before["lastSequence"]
            assert after["lastReceivedAtUtc"] == before["lastReceivedAtUtc"]
            assert _published_originals(session.collector) == original
            current = session.collector.ingestion()[0]
            for key in ("acceptedSignalValues", "signalCount", "lastObservedAtUtc",
                        "lastCommittedRunId", "lastCommittedSequence"):
                assert current[key] == ingestion[key]
            if session.archive is not None:
                assert _state(session.path) == saved
                assert await session.archive.cursor(DEVICE) == (session.run, 0)
                assert len(saved["raw"]) == 1 and saved["gaps"] == []
            else:
                # This transport has no raw-storage contract; do not imply otherwise.
                assert current["archiveEnabled"] is False and not session.path.exists()

    asyncio.run(check())


@pytest.mark.parametrize("transport,count", [("snapshot", 8), ("replay", 8), ("replay", 512)])
def test_existing_simulator_widths_duplicates_and_archive_reopen_preserve_originals(tmp_path, transport, count):
    async def check():
        async with _session(tmp_path, transport, count) as session:
            await session.collector.poll_once(0)
            before = session.collector.connections()[0]
            original = _published_originals(session.collector)
            assert before["acceptedBatches"] == 1 and before["lastSequence"] == 0
            assert session.collector.ingestion()[0]["acceptedSignalValues"] == count
            assert len(original) == 8
            assert all(row["observedAtUtc"] == session.observed for row in original)
            assert all(row["receivedAtUtc"] == session.clock[0] for row in original)
            saved = _state(session.path) if session.archive is not None else None
            session.clock[0] += timedelta(milliseconds=250)
            await session.collector.poll_once(0)
            assert session.collector.connections()[0]["acceptedBatches"] == 1
            assert session.collector.connections()[0]["lastReceivedAtUtc"] == before["lastReceivedAtUtc"]
            assert _published_originals(session.collector) == original
            if session.archive is None:
                assert not session.path.exists()
                return

            assert _state(session.path) == saved
            await session.archive.append_validated([RawSnapshot.model_validate(session.sample)],
                session.clock[0] + timedelta(seconds=10), equipment_id="DIFFERENT-SYNTHETIC")
            assert _state(session.path) == saved
            path, run, observed, signals = session.path, session.run, session.observed, session.sample["signals"]

        reopened = RawArchive(path)
        await reopened.start()
        try:
            assert await reopened.cursor(DEVICE) == (run, 0)
            assert _state(path) == saved
            latest = await reopened.latest(DEVICE)
            assert latest["source"] == "SIMULATOR" and latest["sequence"] == 0 and latest["runId"] == run
            assert _utc(latest["observedAtUtc"]) == observed
            assert latest["signals"] == signals and len(latest["signals"]) == count
        finally:
            await reopened.close()

    asyncio.run(check())


def test_buffered_simulator_history_keeps_source_time_separate_from_first_local_receipt(tmp_path):
    async def check():
        observed = datetime.now(timezone.utc) - timedelta(days=2)
        async with _session(tmp_path, "replay", 512, observed=observed) as session:
            receipt = session.clock[0]
            await session.collector.poll_once(0)
            assert session.collector.connections()[0]["state"] == "STALE"
            assert session.collector.connections()[0]["reasonCode"] == "NO_NEW_DATA"
            assert session.collector.device_health(DEVICE).observationEvaluation == "UNAVAILABLE"
            rows = session.collector.observations(DEVICE)
            assert len(rows) == 8
            assert all(row.observedAtUtc == observed and row.receivedAtUtc == receipt for row in rows)
            assert all(row.quality != "GOOD" for row in rows)
            saved = _state(session.path)
            raw = saved["raw"][0]
            assert _utc(raw[3]) == observed and _utc(raw[4]) == receipt
            payload = json.loads(raw[-1])
            assert payload["source"] == "SIMULATOR" and _utc(payload["observedAtUtc"]) == observed

            session.clock[0] += timedelta(hours=1)
            await session.collector.poll_once(0)  # Empty replay after the committed cursor.
            assert session.collector.connections()[0]["state"] == "STALE"
            assert session.collector.connections()[0]["acceptedBatches"] == 1
            assert all(row.observedAtUtc == observed and row.receivedAtUtc == receipt
                       for row in session.collector.observations(DEVICE))
            await session.archive.append_validated([RawSnapshot.model_validate(session.sample)], session.clock[0])
            assert _state(session.path) == saved
            assert await session.archive.cursor(DEVICE) == (session.run, 0)
            path, run = session.path, session.run

        reopened = RawArchive(path)
        await reopened.start()
        try:
            assert _state(path) == saved
            assert await reopened.cursor(DEVICE) == (run, 0)
            assert _utc((await reopened.latest(DEVICE))["observedAtUtc"]) == observed
        finally:
            await reopened.close()
        (tmp_path / "source-time-control-evidence.json").write_text(json.dumps({
            "scope": "Synthetic simulator replay only; no physical-device or Windows verification.",
            "sourceLiteral": "SIMULATOR", "rawSignals": 512,
            "sourceObservedTimePreserved": True, "firstLocalReceiptPreserved": True,
            "historicalDataRemainedStale": True, "rawRowsAndCursorSurvivedReopen": True,
        }, indent=2) + "\n")

    asyncio.run(check())
