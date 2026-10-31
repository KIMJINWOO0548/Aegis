import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from aegis.archive import RawArchive
from aegis.api import create_app
from aegis.collector import Collector, SourceConfig, SIGNAL_UNITS


TOKEN = "replay-test-" + "x" * 32


def frame(run, seq, at):
    signals = [{"signalKey": key, "value": 10.0, "quality": "GOOD", "unit": unit}
               for key, unit in SIGNAL_UNITS.items()]
    signals.extend({"signalKey": f"aux{i:04d}", "value": float(i), "quality": "GOOD", "unit": "count"}
                   for i in range(248))
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": "CMP-01.PMC-01", "runId": run,
            "sequence": seq, "observedAtUtc": at.isoformat(), "deviceStateVersion": 1,
            "modeId": "PRODUCTION", "signals": signals}


def test_replay_archives_hundreds_of_signals_and_brief_bad_value_before_publication(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        frames = [frame(run, i, now - timedelta(seconds=1) + timedelta(milliseconds=i * 10)) for i in range(101)]
        frames[45]["signals"][1]["value"] = -5.0
        def respond(request):
            after = int(request.url.params["after"])
            return httpx.Response(200, json={"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                "oldestSequence": 0, "latestSequence": 100, "snapshots": frames[after + 1:after + 26]})
        archive = RawArchive(tmp_path / "raw.sqlite3")
        await archive.start()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            config = SourceConfig(deviceId="CMP-01.PMC-01", equipmentId="CMP-01", moduleId="PMC-01",
                                  baseUrl="http://127.0.0.1:9101", transport="replay", pollIntervalMs=100)
            collector = Collector([config], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                                  archive=archive, evaluate=True, utc_now=lambda: now)
            for _ in range(5):
                await collector.poll_once(0)
            assert collector.connections()[0]["acceptedBatches"] == 101
            assert await archive.cursor(config.deviceId) == (run, 100)
            await collector.poll_once(0)
            assert collector.connections()[0]["acceptedBatches"] == 101
            assert len(collector.observations(config.deviceId)) == 8
            assert collector.ingestion()[0]["signalCount"] == 256
        await archive.close()
        with sqlite3.connect(tmp_path / "raw.sqlite3") as db:
            rows = db.execute("SELECT payload_json FROM raw_batches ORDER BY sequence").fetchall()
            assert len(rows) == 101
            assert sum(len(json.loads(row[0])["signals"]) for row in rows) == 25856
            assert json.loads(rows[45][0])["signals"][1]["value"] == -5
            assert db.execute("SELECT DISTINCT equipment_id,module_id FROM raw_batches").fetchall() == [("CMP-01", "PMC-01")]
    asyncio.run(check())


def test_replay_commits_cursor_before_publish_and_reports_overflow_and_storage_failure(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        archive = RawArchive(tmp_path / "raw.sqlite3")
        await archive.start()
        await archive.append([frame(run, 0, now - timedelta(seconds=20))], now)
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 10, "latestSequence": 10, "snapshots": [frame(run, 10, now)]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now)
            with sqlite3.connect(tmp_path / "raw.sqlite3", isolation_level=None) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                await collector.poll_once(0)
                assert collector.connections()[0]["reasonCode"] == "ARCHIVE_UNAVAILABLE"
                assert collector.observations("CMP-01.PMC-01") == []
                assert await archive.cursor("CMP-01.PMC-01") == (run, 0)
                blocker.execute("ROLLBACK")
            await collector.poll_once(0)
            assert await archive.cursor("CMP-01.PMC-01") == (run, 10)
            assert collector.connections()[0]["droppedBatches"] == 9
        await archive.close()
        with sqlite3.connect(tmp_path / "raw.sqlite3") as db:
            assert db.execute("SELECT first_sequence,last_sequence FROM ingest_gaps").fetchall() == [(1, 9)]
    asyncio.run(check())


def test_old_replay_is_archived_without_being_presented_as_fresh(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 0, "snapshots": [frame(run, 0, now - timedelta(seconds=30))]}
        archive = RawArchive(tmp_path / "raw.sqlite3")
        await archive.start()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now, evaluate=True)
            await collector.poll_once(0)
            assert await archive.cursor("CMP-01.PMC-01") == (run, 0)
            assert collector.connections()[0]["state"] == "STALE"
            assert collector.device_health("CMP-01.PMC-01").observationEvaluation == "UNAVAILABLE"
            assert all(row.quality != "GOOD" for row in collector.observations("CMP-01.PMC-01"))
        await archive.close()
    asyncio.run(check())


def test_same_run_cannot_regress_its_advertised_head_after_restart(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        archive = RawArchive(tmp_path / "regressed.sqlite3")
        await archive.start()
        await archive.append([frame(run, 10, now)], now)
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 9, "snapshots": []}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now)
            await collector.poll_once(0)
            assert collector.connections()[0]["state"] == "REJECTED"
            assert await archive.cursor("CMP-01.PMC-01") == (run, 10)
        await archive.close()
    asyncio.run(check())


def test_empty_replay_recovers_transport_without_refreshing_old_observations(tmp_path):
    async def check(stale):
        now = [datetime.now(timezone.utc)]
        run = str(uuid4())
        initial = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 0, "snapshots": [frame(run, 0, now[0])]}
        empty = dict(initial, snapshots=[])
        replies = iter([httpx.Response(200, json=initial), httpx.Response(503), httpx.Response(200, json=empty)])
        archive = RawArchive(tmp_path / f"reconnect-{stale}.sqlite3")
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(replies))) as client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now[0])
                await collector.poll_once(0)
                received = collector.connections()[0]["lastReceivedAtUtc"]
                await collector.poll_once(0)
                assert collector.connections()[0]["state"] == "DISCONNECTED"
                if stale:
                    now[0] += timedelta(seconds=5)
                await collector.poll_once(0)
                connection = collector.connections()[0]
                assert connection["state"] == ("STALE" if stale else "CONNECTED")
                assert connection["lastReceivedAtUtc"] == received
                assert connection["acceptedBatches"] == 1
                assert all(row.quality == ("STALE" if stale else "GOOD")
                           for row in collector.observations("CMP-01.PMC-01"))
        finally:
            await archive.close()
    asyncio.run(check(False))
    asyncio.run(check(True))


def test_retired_run_after_collector_restart_cannot_publish_an_uncommitted_cursor(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        old_run, current_run = str(uuid4()), str(uuid4())
        old = frame(old_run, 0, now - timedelta(seconds=1))
        current = frame(current_run, 0, now)
        for sample in (old, current):
            sample["observedAtUtc"] = sample["observedAtUtc"].replace("+00:00", "Z")
        archive = RawArchive(tmp_path / "retired.sqlite3")
        await archive.start()
        try:
            await archive.append([old], now)
            await archive.append([current], now)
            payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": old_run,
                       "oldestSequence": 0, "latestSequence": 0, "snapshots": [old]}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now)
                await collector.poll_once(0)
                assert collector.connections()[0]["state"] == "REJECTED"
                assert await archive.cursor("CMP-01.PMC-01") == (current_run, 0)
                assert collector.ingestion()[0]["lastCommittedRunId"] == current_run
                assert collector.observations("CMP-01.PMC-01") == []
        finally:
            await archive.close()
    asyncio.run(check())


@pytest.mark.parametrize("fault", ["future", "duplicate_timestamp", "out_of_order_sequence", "wrong_identity", "byte_limit"])
def test_invalid_replay_envelope_cannot_commit_any_part_of_the_page(tmp_path, fault):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        samples = [frame(run, 0, now - timedelta(milliseconds=10)), frame(run, 1, now)]
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 1, "snapshots": samples}
        if fault == "future":
            samples[1]["observedAtUtc"] = (now + timedelta(seconds=6)).isoformat()
        elif fault == "duplicate_timestamp":
            samples[1]["observedAtUtc"] = samples[0]["observedAtUtc"]
        elif fault == "out_of_order_sequence":
            samples[1]["sequence"] = 0
        elif fault == "wrong_identity":
            samples[1]["deviceId"] = "ANOTHER-DEVICE"
        else:
            payload["snapshots"] = samples * 30
        archive = RawArchive(tmp_path / "invalid.sqlite3")
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now)
                await collector.poll_once(0)
                assert collector.connections()[0]["state"] == "REJECTED"
                assert await archive.cursor("CMP-01.PMC-01") is None
                assert collector.observations("CMP-01.PMC-01") == []
                with sqlite3.connect(tmp_path / "invalid.sqlite3") as db:
                    assert db.execute("SELECT count(*) FROM raw_batches").fetchone()[0] == 0
        finally:
            await archive.close()
    asyncio.run(check())


def test_stale_historical_replay_cannot_raise_a_live_warning(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        samples = [frame(run, seq, now - timedelta(seconds=1000 - seq)) for seq in range(120)]
        for sample in samples:
            sample["signals"] = sample["signals"][:8]
            sample["signals"][1]["value"] = 8.0
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 119, "snapshots": samples}
        archive = RawArchive(tmp_path / "stale-warning.sqlite3")
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now, evaluate=True)
                await collector.poll_once(0)
                assert await archive.cursor("CMP-01.PMC-01") == (run, 119)
                assert collector.connections()[0]["state"] == "STALE"
                assert collector.warnings() == []
        finally:
            await archive.close()
    asyncio.run(check())


def test_stale_historical_replay_cannot_resolve_an_existing_live_warning(tmp_path):
    async def check():
        origin = datetime.now(timezone.utc)
        now = [origin]
        run = str(uuid4())
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 0, "snapshots": []}
        archive = RawArchive(tmp_path / "stale-recovery.sqlite3")
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=client, utc_now=lambda: now[0], evaluate=True)
                for seq in range(120):
                    now[0] = origin + timedelta(seconds=seq)
                    sample = frame(run, seq, now[0])
                    sample["signals"] = sample["signals"][:8]
                    sample["signals"][1]["value"] = 8.0
                    payload.update(latestSequence=seq, snapshots=[sample])
                    await collector.poll_once(0)
                warning = collector.warnings()[0]
                assert warning.state == "OPEN"
                normal_history = [frame(run, seq, origin + timedelta(seconds=seq)) for seq in range(120, 240)]
                for sample in normal_history:
                    sample["signals"] = sample["signals"][:8]
                payload.update(latestSequence=239, snapshots=normal_history)
                now[0] = origin + timedelta(seconds=1000)
                await collector.poll_once(0)
                assert await archive.cursor("CMP-01.PMC-01") == (run, 239)
                assert collector.connections()[0]["state"] == "STALE"
                assert [(item.id, str(item.state)) for item in collector.warnings()] == [(warning.id, "OPEN")]
                assert collector.device_health("CMP-01.PMC-01").observationEvaluation == "UNAVAILABLE"
        finally:
            await archive.close()
    asyncio.run(check())


def test_blocked_archive_keeps_api_reads_responsive_and_published_state_unchanged(tmp_path):
    async def check():
        now = datetime.now(timezone.utc)
        run = str(uuid4())
        payload = {"streamVersion": "1.0", "deviceId": "CMP-01.PMC-01", "runId": run,
                   "oldestSequence": 0, "latestSequence": 0, "snapshots": [frame(run, 0, now)]}
        path = tmp_path / "blocked.sqlite3"
        archive = RawArchive(path)
        await archive.start()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source_client:
                collector = Collector([SourceConfig(deviceId="CMP-01.PMC-01", baseUrl="http://127.0.0.1:9101", transport="replay")],
                    {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=archive, client=source_client, utc_now=lambda: now)
                await collector.poll_once(0)
                previous = collector.observations("CMP-01.PMC-01")
                sample = frame(run, 1, now + timedelta(milliseconds=10))
                sample["signals"][1]["value"] = 15.0
                payload.update(latestSequence=1, snapshots=[sample])
                # Keep the real write lock pending long enough to inspect concurrent API reads.
                await archive._run(lambda: archive._connection.execute("PRAGMA busy_timeout=2000"))
                with sqlite3.connect(path, isolation_level=None) as blocker:
                    blocker.execute("BEGIN IMMEDIATE")
                    pending = asyncio.create_task(collector.poll_once(0))
                    try:
                        await asyncio.sleep(.03)
                        assert not pending.done()
                        app = create_app(TOKEN, collector=collector)
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                            base_url="http://127.0.0.1:8765", headers={"X-Aegis-Token": TOKEN}) as api:
                            for route in ("/v1/health", "/v1/connections", "/v1/ingestion", "/v1/devices/CMP-01.PMC-01/observations"):
                                response = await asyncio.wait_for(api.get(route), .5)
                                assert response.status_code == 200
                        assert not pending.done()
                        assert collector.observations("CMP-01.PMC-01") == previous
                        assert collector.ingestion()[0]["lastCommittedSequence"] == 0
                    finally:
                        blocker.execute("ROLLBACK")
                        await pending
                assert await archive.cursor("CMP-01.PMC-01") == (run, 1)
                assert collector.ingestion()[0]["lastCommittedSequence"] == 1
                assert collector.observations("CMP-01.PMC-01")[1].value == 15.0
        finally:
            await archive.close()
    asyncio.run(check())
