import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


AT = datetime(2026, 10, 18, tzinfo=timezone.utc)
RUN = str(uuid4())


def snapshot(sequence, *, run=RUN, device="CMP-01.PMC-01", value=10.0, quality="GOOD"):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
            "runId": run, "sequence": sequence,
            "observedAtUtc": (AT + timedelta(milliseconds=sequence * 10)).isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": "actualFlowLpm", "value": value,
                         "unit": "L/min", "quality": quality}]}


def archive(path):
    from aegis.archive import RawArchive
    return RawArchive(path)


def read(path, query):
    with sqlite3.connect(path) as connection:
        return connection.execute(query).fetchall()


def test_committed_raw_values_hierarchy_and_cursor_survive_close_and_reopen(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        assert await store.cursor("CMP-01.PMC-01") is None
        samples = [snapshot(0, value=None, quality="BAD"), snapshot(1, value=-5.0),
                   snapshot(2, value=None, quality="MISSING")]
        await store.append(samples, AT, equipment_id="CMP-01", module_id="PMC-01")
        await store.close()
        rows = read(path, "SELECT payload_json,equipment_id,module_id FROM raw_batches ORDER BY sequence")
        assert [json.loads(row[0]) for row in rows] == samples
        assert all(row[1:] == ("CMP-01", "PMC-01") for row in rows)
        reopened = archive(path)
        await reopened.start()
        assert await reopened.cursor("CMP-01.PMC-01") == (RUN, 2)
        assert read(path, "PRAGMA journal_mode")[0][0] == "wal"
        await reopened.close()
    asyncio.run(check())


def test_exact_retry_preserves_first_receipt_and_changed_content_rolls_back_the_page(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.append([snapshot(1)], AT, equipment_id="original")
        original = read(path, "SELECT received_at_utc,equipment_id FROM raw_batches")
        await store.append([dict(reversed(list(snapshot(1).items())))], AT + timedelta(seconds=9), equipment_id="changed")
        assert read(path, "SELECT received_at_utc,equipment_id FROM raw_batches") == original
        with pytest.raises(ValueError, match="conflict|changed|different"):
            await store.append([snapshot(2), snapshot(1, value=11.0)], AT, gap=(0, 0))
        assert read(path, "SELECT sequence FROM raw_batches") == [(1,)]
        assert read(path, "SELECT * FROM ingest_gaps") == []
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
        await store.append([snapshot(2)], AT)
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 2)
        await store.close()
    asyncio.run(check())


def test_validated_model_append_preserves_legacy_retry_and_atomic_conflict_rollback(tmp_path):
    from aegis.collector import RawSnapshot

    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        try:
            original = RawSnapshot.model_validate(snapshot(1, value=-5.0))
            await store.append([original.model_dump(mode="json")], AT, equipment_id="original")
            before = read(path, "SELECT payload_json,received_at_utc,equipment_id FROM raw_batches")
            await store.append_validated([original], AT + timedelta(seconds=9), equipment_id="changed")
            assert read(path, "SELECT payload_json,received_at_utc,equipment_id FROM raw_batches") == before
            with pytest.raises(ValueError, match="conflict|changed|different"):
                await store.append_validated([RawSnapshot.model_validate(snapshot(2)),
                    RawSnapshot.model_validate(snapshot(1, value=11.0))], AT, gap=(0, 0))
            assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
            assert read(path, "SELECT sequence FROM raw_batches") == [(1,)]
            assert read(path, "SELECT * FROM ingest_gaps") == []
            bad = RawSnapshot.model_validate(snapshot(2, value=None, quality="BAD"))
            await store.append_validated([bad], AT)
            assert json.loads(read(path, "SELECT payload_json FROM raw_batches WHERE sequence=2")[0][0]) == bad.model_dump(mode="json")
            assert await store.cursor("CMP-01.PMC-01") == (RUN, 2)
        finally:
            await store.close()

    asyncio.run(check())


def test_overflow_gap_is_committed_once_with_its_available_batches(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.append([snapshot(1)], AT)
        await store.append([snapshot(5), snapshot(6)], AT, gap=(2, 4))
        await store.append([snapshot(5), snapshot(6)], AT, gap=(2, 4))
        assert read(path, "SELECT first_sequence,last_sequence,reason FROM ingest_gaps") == [(2, 4, "REPLAY_OVERFLOW")]
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 6)
        await store.close()
    asyncio.run(check())


def test_old_run_retry_cannot_replace_the_active_new_run_cursor(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        new_run = str(uuid4())
        await store.append([snapshot(100)], AT)
        await store.append([snapshot(0, run=new_run)], AT)
        await store.append([snapshot(100)], AT)
        assert await store.cursor("CMP-01.PMC-01") == (new_run, 0)
        assert read(path, "SELECT count(*) FROM raw_batches") == [(2,)]
        await store.close()
    asyncio.run(check())


def test_locked_database_does_not_advance_cursor_or_block_the_event_loop(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.append([snapshot(1)], AT)
        blocker = sqlite3.connect(path)
        blocker.execute("BEGIN IMMEDIATE")
        pending = asyncio.create_task(store.append([snapshot(5)], AT, gap=(2, 4)))
        await asyncio.sleep(.02)
        assert not pending.done()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await pending
        blocker.rollback()
        blocker.close()
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
        assert read(path, "SELECT sequence FROM raw_batches") == [(1,)]
        assert read(path, "SELECT * FROM ingest_gaps") == []
        await store.append([snapshot(5)], AT, gap=(2, 4))
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 5)
        await store.close()
    asyncio.run(check())


def test_disk_full_rolls_back_rows_gap_and_cursor_then_the_same_connection_can_retry(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.append([snapshot(1)], AT)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(store._executor, lambda: store._connection.execute("PRAGMA max_page_count=12").fetchone())
        large = snapshot(5)
        large["signals"] = [{"signalKey": f"tag{index:04d}", "value": float(index), "unit": "unit", "quality": "GOOD"}
                            for index in range(512)]
        with pytest.raises(sqlite3.OperationalError, match="full"):
            await store.append([snapshot(4), large], AT, gap=(2, 3))
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
        assert read(path, "SELECT sequence FROM raw_batches") == [(1,)]
        assert read(path, "SELECT * FROM ingest_gaps") == []
        await loop.run_in_executor(store._executor, lambda: store._connection.execute("PRAGMA max_page_count=1000").fetchone())
        await store.append([snapshot(4), large], AT, gap=(2, 3))
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 5)
        assert len(json.loads(read(path, "SELECT payload_json FROM raw_batches WHERE sequence=5")[0][0])["signals"]) == 512
        await store.close()
    asyncio.run(check())


def test_commit_failure_rolls_back_the_entire_transaction_and_can_retry(tmp_path, monkeypatch):
    connect = sqlite3.connect
    fail_commit = [False]

    class FailingCommit(sqlite3.Connection):
        def commit(self):
            if fail_commit[0]:
                raise sqlite3.OperationalError("injected commit failure")
            return super().commit()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=FailingCommit))

    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.append([snapshot(1)], AT)
        fail_commit[0] = True
        with pytest.raises(sqlite3.OperationalError, match="commit failure"):
            await store.append([snapshot(5)], AT, gap=(2, 4))
        fail_commit[0] = False
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
        assert read(path, "SELECT sequence FROM raw_batches") == [(1,)]
        assert read(path, "SELECT * FROM ingest_gaps") == []
        await store.append([snapshot(5)], AT, gap=(2, 4))
        await store.close()
    asyncio.run(check())


@pytest.mark.parametrize("version", [0, 1, 2])
def test_unrelated_or_unknown_schema_is_refused_without_altering_it(tmp_path, version):
    async def check():
        path = tmp_path / "unrelated.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE private_data(value TEXT)")
            connection.execute(f"PRAGMA user_version={version}")
        store = archive(path)
        with pytest.raises(ValueError, match="schema|database|version"):
            await store.start()
        assert read(path, "SELECT name FROM sqlite_master WHERE type='table'") == [("private_data",)]
        await store.close()
    asyncio.run(check())


def test_a_database_containing_only_an_unrelated_view_is_not_initialized(tmp_path):
    async def check():
        path = tmp_path / "unrelated.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE VIEW private_view AS SELECT 1 AS value")
        store = archive(path)
        with pytest.raises(ValueError, match="schema|database|version"):
            await store.start()
        assert read(path, "PRAGMA user_version") == [(0,)]
        assert read(path, "SELECT name FROM sqlite_master") == [("private_view",)]
    asyncio.run(check())


def test_queued_payload_is_copied_before_the_caller_can_mutate_it(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        started, release = threading.Event(), threading.Event()

        def hold_worker():
            started.set()
            release.wait(2)

        held = asyncio.get_running_loop().run_in_executor(store._executor, hold_worker)
        assert await asyncio.to_thread(started.wait, 1)
        sample = snapshot(1)
        pending = asyncio.create_task(store.append([sample], AT))
        await asyncio.sleep(.01)
        sample["signals"][0]["value"] = 999.0
        release.set()
        await held
        await pending
        assert json.loads(read(path, "SELECT payload_json FROM raw_batches")[0][0])["signals"][0]["value"] == 10.0
        await store.close()
    asyncio.run(check())


def test_cancelled_running_append_is_resolved_by_the_durable_cursor_and_idempotent_retry(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        blocker = sqlite3.connect(path)
        blocker.execute("BEGIN IMMEDIATE")
        entered = threading.Event()
        await asyncio.get_running_loop().run_in_executor(store._executor, lambda: store._connection.set_trace_callback(
            lambda statement: entered.set() if statement == "BEGIN IMMEDIATE" else None))
        pending = asyncio.create_task(store.append([snapshot(1)], AT))
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        blocker.rollback()
        blocker.close()
        assert await store.cursor("CMP-01.PMC-01") == (RUN, 1)
        await store.append([snapshot(1)], AT + timedelta(seconds=2))
        assert read(path, "SELECT count(*) FROM raw_batches") == [(1,)]
        await store.close()
    asyncio.run(check())


def test_close_drains_already_submitted_work_and_rejects_new_calls(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        blocker = sqlite3.connect(path)
        blocker.execute("BEGIN IMMEDIATE")
        pending = asyncio.create_task(store.append([snapshot(1)], AT))
        await asyncio.sleep(.02)
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(.01)
        with pytest.raises(RuntimeError, match="started|closing"):
            await store.cursor("CMP-01.PMC-01")
        blocker.rollback()
        blocker.close()
        await pending
        await closing
        await store.close()
        assert read(path, "SELECT count(*) FROM raw_batches") == [(1,)]
    asyncio.run(check())


def test_cancelled_start_closes_the_worker_connection_before_the_instance_can_restart(tmp_path, monkeypatch):
    connect = sqlite3.connect
    entered, release = threading.Event(), threading.Event()

    def delayed_connect(*args, **kwargs):
        entered.set()
        release.wait(2)
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", delayed_connect)

    async def check():
        store = archive(tmp_path / "raw.sqlite")
        starting = asyncio.create_task(store.start())
        assert await asyncio.to_thread(entered.wait, 1)
        starting.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert store._executor is None
        assert store._connection is None
        await store.start()
        await store.append([snapshot(1)], AT)
        await store.close()
    asyncio.run(check())


def test_repeated_close_cancellation_still_drains_and_closes_the_worker(tmp_path):
    async def check():
        store = archive(tmp_path / "raw.sqlite")
        await store.start()
        entered, release = threading.Event(), threading.Event()

        def hold_worker():
            entered.set()
            release.wait(2)

        held = asyncio.get_running_loop().run_in_executor(store._executor, hold_worker)
        assert await asyncio.to_thread(entered.wait, 1)
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(.01)
        closing.cancel()
        await asyncio.sleep(.01)
        closing.cancel()
        await asyncio.sleep(.01)
        release.set()
        await held
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert store._connection is None
        assert store._executor is None
    asyncio.run(check())


def test_version_one_tables_without_the_expected_identity_constraints_are_refused(tmp_path):
    async def check():
        path = tmp_path / "unrelated.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE raw_batches(device_id,run_id,sequence,observed_at_utc,received_at_utc,equipment_id,module_id,payload_json)")
            connection.execute("CREATE TABLE ingest_cursors(device_id,run_id,sequence)")
            connection.execute("CREATE TABLE ingest_gaps(device_id,run_id,first_sequence,last_sequence,reason)")
            connection.execute("PRAGMA user_version=1")
        store = archive(path)
        with pytest.raises(ValueError, match="schema|database"):
            await store.start()
    asyncio.run(check())


def test_a_partial_gap_index_cannot_substitute_for_archive_identity_uniqueness(tmp_path):
    async def check():
        path = tmp_path / "raw.sqlite"
        store = archive(path)
        await store.start()
        await store.close()
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE ingest_gaps")
            connection.execute("CREATE TABLE ingest_gaps(device_id,run_id,first_sequence,last_sequence,reason)")
            connection.execute("CREATE UNIQUE INDEX partial_gap ON ingest_gaps(device_id,run_id,first_sequence,last_sequence) WHERE first_sequence>0")
        reopened = archive(path)
        with pytest.raises(ValueError, match="schema"):
            await reopened.start()
    asyncio.run(check())


def test_latest_returns_only_the_committed_active_run_payload(tmp_path):
    async def check():
        store = archive(tmp_path / "raw.sqlite")
        await store.start()
        assert await store.latest("CMP-01.PMC-01") is None
        first = snapshot(10)
        await store.append([first], AT)
        assert await store.latest("CMP-01.PMC-01") == first
        current = snapshot(0, run=str(uuid4()))
        await store.append([current], AT)
        await store.append([first], AT)
        assert await store.latest("CMP-01.PMC-01") == current
        await store.close()
    asyncio.run(check())
