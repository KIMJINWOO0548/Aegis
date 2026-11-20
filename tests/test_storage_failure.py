"""Real SQLite capacity failures retain replay originals and committed cursors."""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx

from aegis.api import create_app
from aegis.archive import RawArchive
from aegis.collector import Collector, RawSnapshot, SIGNAL_UNITS, SourceConfig


TOKEN = "synthetic-storage-failure-" + "x" * 32


def _snapshot(device, run, sequence, at, *, signal_count=8):
    signals = [{"signalKey": key, "value": float(sequence + 1), "unit": unit, "quality": "GOOD"}
               for key, unit in SIGNAL_UNITS.items()]
    signals.extend({"signalKey": f"synthetic.tag{index:04d}", "value": float(index),
                    "unit": "count", "quality": "GOOD"} for index in range(signal_count - 8))
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
            "runId": run, "sequence": sequence, "observedAtUtc": at.isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION", "signals": signals}


def _database_state(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        return {
            "raw": db.execute("SELECT * FROM raw_batches ORDER BY device_id,run_id,sequence").fetchall(),
            "cursors": db.execute("SELECT * FROM ingest_cursors ORDER BY device_id").fetchall(),
            "gaps": db.execute("SELECT * FROM ingest_gaps ORDER BY device_id,run_id,first_sequence").fetchall(),
        }


async def _page_limit(archive, limit=None):
    def configure():
        current = archive._connection.execute("PRAGMA page_count").fetchone()[0]
        maximum = current + 1 if limit is None else limit
        actual = archive._connection.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0]
        assert actual == maximum
        return current, actual
    return await archive._run(configure)


@asynccontextmanager
async def _scenario(directory, *, device="STORAGE-01", port=19101):
    path, log = directory / "raw.sqlite3", directory / "events.jsonl"
    run = str(uuid4())
    clock = [datetime.now(timezone.utc)]
    epoch = clock[0] - timedelta(milliseconds=100)
    small = [_snapshot(device, run, sequence, epoch + timedelta(milliseconds=sequence * 10))
             for sequence in (0, 1)]
    retry = [_snapshot(device, run, 4, epoch + timedelta(milliseconds=40)),
             _snapshot(device, run, 5, epoch + timedelta(milliseconds=50), signal_count=512)]
    current = {"oldest": 0, "latest": 1, "snapshots": small}
    requested, failures = [], []

    def respond(request):
        after = int(request.url.params["after"])
        requested.append(after)
        return httpx.Response(200, json={"streamVersion": "1.0", "deviceId": device, "runId": run,
            "oldestSequence": current["oldest"], "latestSequence": current["latest"],
            "snapshots": [sample for sample in current["snapshots"] if sample["sequence"] > after]})

    archive = RawArchive(path)
    await archive.start()
    original_append = archive._append

    def observe_append(*arguments):
        try:
            return original_append(*arguments)
        except sqlite3.Error as error:
            # Observe the real engine result, without substituting a fake exception.
            failures.append(error.sqlite_errorcode)
            raise

    archive._append = observe_append
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            source = SourceConfig(deviceId=device, baseUrl=f"http://127.0.0.1:{port}", transport="replay",
                                  equipmentId="SYNTHETIC-EQUIPMENT", moduleId=device)
            collector = Collector([source], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                archive=archive, log_path=log, utc_now=lambda: clock[0], evaluate=True)
            yield SimpleNamespace(path=path, log=log, archive=archive, source=source,
                collector=collector, run=run, clock=clock, current=current, retry=retry,
                requested=requested, failures=failures)
    finally:
        await archive.close()


async def _fill(scenario):
    await scenario.collector.poll_once(0)
    assert await scenario.archive.cursor(scenario.source.deviceId) == (scenario.run, 1)
    assert scenario.collector.connections()[0]["acceptedBatches"] == 2
    before = _database_state(scenario.path)
    inode = scenario.path.stat().st_ino
    page_count, limit = await _page_limit(scenario.archive)
    assert limit == page_count + 1
    scenario.current.update(oldest=4, latest=5, snapshots=scenario.retry)
    await scenario.collector.poll_once(0)
    assert scenario.failures and scenario.failures[-1] & 0xff == sqlite3.SQLITE_FULL
    # These are pre-existing preservation guarantees, independent of the new reason code.
    assert _database_state(scenario.path) == before
    assert scenario.path.exists() and scenario.path.stat().st_ino == inode
    assert await scenario.archive.cursor(scenario.source.deviceId) == (scenario.run, 1)
    assert scenario.collector.ingestion()[0]["lastCommittedSequence"] == 1
    assert scenario.collector.connections()[0]["acceptedBatches"] == 2
    assert scenario.collector.connections()[0]["droppedBatches"] == 0
    assert scenario.collector.connections()[0]["state"] == "DISCONNECTED"
    assert scenario.collector.device_health(scenario.source.deviceId).observationEvaluation == "UNAVAILABLE"
    assert all(row.quality != "GOOD" for row in scenario.collector.observations(scenario.source.deviceId))
    with closing(sqlite3.connect(scenario.path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return before


async def _api_state(collector):
    app = create_app(TOKEN, collector=collector)
    # ASGITransport does not start background acquisition; poll_once owns this fixture's schedule.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:19000",
                                 headers={"X-Aegis-Token": TOKEN}) as client:
        connection = await client.get("/v1/connections")
        ingestion = await client.get("/v1/ingestion")
        assert connection.status_code == ingestion.status_code == 200
        return connection.json()[0], ingestion.json()[0]


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_real_sqlite_full_has_a_distinct_collector_api_and_storage_event_reason(tmp_path):
    async def check():
        async with _scenario(tmp_path) as scenario:
            before = await _fill(scenario)
            connection, ingestion = await _api_state(scenario.collector)
            assert connection["state"] == "DISCONNECTED"
            assert ingestion["lastCommittedSequence"] == 1
            assert scenario.collector.connections()[0]["reasonCode"] == "ARCHIVE_FULL"
            assert connection["reasonCode"] == "ARCHIVE_FULL"
            failures = [event for event in _events(scenario.log) if event["outcome"] == "FAILED"]
            assert [(event["eventName"], event["reasonCode"]) for event in failures] == [
                ("STORAGE_WRITE_FAILED", "ARCHIVE_FULL")]
            assert all(event["deviceId"] == scenario.source.deviceId for event in failures)
            await scenario.collector.poll_once(0)
            assert _database_state(scenario.path) == before
            assert len(scenario.failures) == 2
            assert len([event for event in _events(scenario.log) if event["outcome"] == "FAILED"]) == 1

    asyncio.run(check())


def test_real_full_rollback_recovers_on_the_same_connection_and_retries_are_idempotent(tmp_path):
    async def check():
        async with _scenario(tmp_path) as scenario:
            original_connection = scenario.archive._connection
            before = await _fill(scenario)
            scenario.clock[0] += timedelta(seconds=1)
            await scenario.collector.poll_once(0)
            assert all(code & 0xff == sqlite3.SQLITE_FULL for code in scenario.failures)
            assert len(scenario.failures) == 2
            assert _database_state(scenario.path) == before
            assert scenario.requested == [-1, 1, 1]

            await _page_limit(scenario.archive, 1000)
            scenario.clock[0] += timedelta(milliseconds=500)
            receipt = scenario.clock[0]
            await scenario.collector.poll_once(0)
            assert scenario.archive._connection is original_connection
            assert await scenario.archive.cursor(scenario.source.deviceId) == (scenario.run, 5)
            connection = scenario.collector.connections()[0]
            assert connection["state"] == "CONNECTED" and connection["reasonCode"] is None
            assert connection["acceptedBatches"] == 4
            assert connection["droppedBatches"] == 2
            assert scenario.collector.ingestion()[0]["lastCommittedSequence"] == 5
            committed = _database_state(scenario.path)
            assert [row[2] for row in committed["raw"]] == [0, 1, 4, 5]
            assert committed["raw"][:2] == before["raw"]
            assert all(row[4] == receipt.isoformat() for row in committed["raw"][2:])
            assert committed["gaps"] == [(scenario.source.deviceId, scenario.run, 2, 3, "REPLAY_OVERFLOW")]
            assert len(json.loads(committed["raw"][-1][-1])["signals"]) == 512

            # Normal source retry resumes after the committed cursor and returns an empty page.
            scenario.clock[0] += timedelta(milliseconds=100)
            await scenario.collector.poll_once(0)
            assert scenario.requested == [-1, 1, 1, 1, 5]
            assert _database_state(scenario.path) == committed
            assert scenario.collector.connections()[0]["acceptedBatches"] == 4
            assert scenario.collector.connections()[0]["droppedBatches"] == 2

            # Also exercise an actual duplicate append, including a repeated overflow gap.
            await scenario.archive.append_validated(
                [RawSnapshot.model_validate(sample) for sample in scenario.retry],
                receipt + timedelta(seconds=10), equipment_id="DIFFERENT-SYNTHETIC",
                module_id="DIFFERENT-SYNTHETIC", gap=(2, 3))
            assert _database_state(scenario.path) == committed
            assert await scenario.archive.cursor(scenario.source.deviceId) == (scenario.run, 5)
            path, device, run = scenario.path, scenario.source.deviceId, scenario.run

        reopened = RawArchive(path)
        await reopened.start()
        try:
            assert await reopened.cursor(device) == (run, 5)
            assert _database_state(path) == committed
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            await reopened.close()
        (tmp_path / "capacity-recovery-evidence.json").write_text(json.dumps({
            "scope": "Synthetic replay and SQLite max_page_count; filesystem capacity was not exhausted.",
            "realSqliteFullFailures": 2, "originalRowsPreserved": 2,
            "committedRowsAfterRecovery": 4, "overflowGapCommittedOnce": True,
            "firstReceiptsPreservedOnDuplicateAppend": True, "cursorSurvivedReopen": True,
        }, indent=2) + "\n")

    asyncio.run(check())


def test_full_module_does_not_block_a_peer_using_a_separate_archive(tmp_path):
    async def check():
        async with _scenario(tmp_path / "target") as target, _scenario(
                tmp_path / "peer", device="STORAGE-02", port=19102) as peer:
            target_before = await _fill(target)
            await peer.collector.poll_once(0)
            peer_before = _database_state(peer.path)
            latest = _snapshot(peer.source.deviceId, peer.run, 2,
                peer.clock[0] - timedelta(milliseconds=80))
            peer.current.update(latest=2, snapshots=[*peer.current["snapshots"], latest])

            await asyncio.gather(target.collector.poll_once(0), peer.collector.poll_once(0))
            assert target.archive is not peer.archive
            assert _database_state(target.path) == target_before
            assert await target.archive.cursor(target.source.deviceId) == (target.run, 1)
            assert target.collector.connections()[0]["state"] == "DISCONNECTED"
            assert len(target.failures) == 2
            assert all(code & 0xff == sqlite3.SQLITE_FULL for code in target.failures)
            assert await peer.archive.cursor(peer.source.deviceId) == (peer.run, 2)
            assert peer.collector.connections()[0]["state"] == "CONNECTED"
            assert peer.collector.connections()[0]["reasonCode"] is None
            assert peer.collector.connections()[0]["acceptedBatches"] == 3
            assert peer.failures == []
            peer_after = _database_state(peer.path)
            assert peer_after["raw"][:2] == peer_before["raw"]
            assert [row[2] for row in peer_after["raw"]] == [0, 1, 2]
            assert peer_after["gaps"] == []
            for path in (target.path, peer.path):
                with closing(sqlite3.connect(path)) as db:
                    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    asyncio.run(check())
