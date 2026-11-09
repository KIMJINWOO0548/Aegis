"""Bounded raw reads preserve original data and fail without success prefixes."""

import asyncio
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aegis.archive import RawArchive


AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
RUN = "11111111-1111-4111-8111-111111111111"
OTHER_RUN = "22222222-2222-4222-8222-222222222222"
DEVICE = "CMP-01.PMC-01"


def sample(sequence=0, *, at=None, run=RUN, device=DEVICE, signals=None):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
            "runId": run, "sequence": sequence, "observedAtUtc": at or AT.isoformat(),
            "deviceStateVersion": 9, "modeId": "PRODUCTION",
            "signals": signals or [{"signalKey": "numeric.alpha", "value": 10.0,
                                     "unit": "count", "quality": "GOOD"}]}


def save(path, rows, *, equipment="ORIGINAL-EQUIPMENT", module="ORIGINAL-MODULE"):
    async def write():
        store = RawArchive(path)
        await store.start()
        try:
            # Separate appends allow different devices/runs and retain original receipt metadata.
            for row in rows:
                await store.append([row], AT + timedelta(days=1), equipment_id=equipment, module_id=module)
        finally:
            await store.close()
    asyncio.run(write())
    return path


def read(path, **overrides):
    from aegis.raw_history import read_raw_archive
    params = {"device_id": DEVICE, "run_id": RUN, "from_utc": AT.isoformat(),
              "to_utc": (AT + timedelta(seconds=1)).isoformat(), "signals": "numeric.alpha"}
    params.update(overrides)
    return read_raw_archive(path, **params)


def failure(path, status, code, **kwargs):
    from aegis.raw_history import RawQueryError
    with pytest.raises(RawQueryError) as error:
        read(path, **kwargs)
    assert error.value.status == status and error.value.code == code
    assert "PRIVATE" not in str(error.value) and str(path) not in str(error.value)


def db_state(path):
    db = sqlite3.connect(path)
    try:
        return {table: db.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()
                for table in ("raw_batches", "ingest_cursors", "ingest_gaps")}
    finally:
        db.close()


def test_history_uses_exact_half_open_source_time_not_lexical_time_or_receipt(tmp_path):
    offset = timezone(timedelta(hours=9))
    rows = [sample(0, at=AT.strftime("%Y-%m-%dT%H:%M:%SZ")),
            sample(1, at=(AT + timedelta(microseconds=500)).astimezone(offset).isoformat()),
            sample(2, at=(AT + timedelta(seconds=1)).isoformat()),
            sample(3, at=(AT - timedelta(microseconds=1)).isoformat())]
    path = save(tmp_path / "raw #?.sqlite3", rows)
    result = read(path)
    assert result["schemaVersion"] == "raw-query-1" and result["kind"] == "RAW_ARCHIVE_HISTORY"
    assert result["selectionComplete"] is True and result["sourceCoverage"] == "NOT_ASSERTED"
    assert result["scannedBatches"] == 4 and result["matchedBatches"] == 2
    assert result["window"]["bounds"] == "[from,to)"
    assert [b["sequence"] for b in result["batches"]] == [0, 1]
    for batch, original in zip(result["batches"], rows):
        for key, value in original.items():
            if key != "signals":
                assert batch[key] == value
        assert batch["receivedAtUtc"] == (AT + timedelta(days=1)).isoformat()
        assert batch["equipmentId"] == "ORIGINAL-EQUIPMENT" and batch["moduleId"] == "ORIGINAL-MODULE"


def test_absent_tag_is_distinct_from_original_missing_bad_and_null_values(tmp_path):
    rows = [sample(0, signals=[{"signalKey": "numeric.alpha", "value": 9007199254740994,
                               "unit": "count", "quality": "GOOD"},
                              {"signalKey": "numeric.beta", "value": None, "unit": "volt", "quality": "MISSING"}]),
            sample(1, signals=[{"signalKey": "numeric.alpha", "value": None, "unit": "amp", "quality": "BAD"}])]
    path = save(tmp_path / "raw.sqlite3", rows)
    result = read(path, signals="numeric.alpha,numeric.beta,numeric.absent")
    a, b = result["batches"]
    assert a["signals"] == [
        {"signalKey": "numeric.alpha", "present": True,
         "reading": {"value": 9007199254740994, "unit": "count", "quality": "GOOD"}},
        {"signalKey": "numeric.beta", "present": True,
         "reading": {"value": None, "unit": "volt", "quality": "MISSING"}},
        {"signalKey": "numeric.absent", "present": False, "reading": None}]
    assert type(a["signals"][0]["reading"]["value"]) is int
    assert b["signals"][0]["reading"] == {"value": None, "unit": "amp", "quality": "BAD"}
    assert b["signals"][1] == {"signalKey": "numeric.beta", "present": False, "reading": None}
    tags = {t["signalKey"]: t for t in result["tags"]}
    assert tags["numeric.alpha"] == {"signalKey": "numeric.alpha", "units": ["amp", "count"],
        "presentBatches": 2, "absentBatches": 0, "qualities": {"BAD": 1, "GOOD": 1}}
    assert tags["numeric.beta"]["presentBatches"] == 1 and tags["numeric.beta"]["absentBatches"] == 1
    assert tags["numeric.absent"]["units"] == [] and tags["numeric.absent"]["qualities"] == {}


def test_observed_catalog_is_window_scoped_and_preserves_unit_variants(tmp_path):
    rows = [sample(0), sample(1, signals=[{"signalKey": "numeric.alpha", "value": -2.0,
                                         "unit": "amp", "quality": "STALE"}]),
            sample(2, at=(AT + timedelta(seconds=5)).isoformat(), signals=[
                {"signalKey": "outside.window", "value": 4.0, "unit": "unit", "quality": "GOOD"}])]
    result = read(save(tmp_path / "raw.sqlite3", rows), signals=None)
    assert result["kind"] == "RAW_ARCHIVE_OBSERVED_TAGS" and "batches" not in result
    assert result["tags"] == [{"signalKey": "numeric.alpha", "units": ["amp", "count"],
        "presentBatches": 2, "absentBatches": 0, "qualities": {"GOOD": 1, "STALE": 1}}]


def test_device_run_and_original_module_are_isolated_without_advancing_cursor(tmp_path):
    path = save(tmp_path / "raw.sqlite3", [sample(), sample(run=OTHER_RUN), sample(device="OTHER-01")])
    before = db_state(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = read(path)
    assert len(result["batches"]) == 1 and result["batches"][0]["runId"] == RUN
    assert all(b["deviceId"] == DEVICE for b in result["batches"])
    assert db_state(path) == before and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    other = read(path, run_id=OTHER_RUN)
    assert len(other["batches"]) == 1 and other["batches"][0]["runId"] == OTHER_RUN


def test_512_tag_catalog_and_projection_keep_all_requested_original_values(tmp_path):
    signals = [{"signalKey": f"generic.tag{i:04d}", "value": float(i), "unit": "count", "quality": "GOOD"}
               for i in range(512)]
    path = save(tmp_path / "raw.sqlite3", [sample(signals=signals)])
    catalog = read(path, signals=None)
    assert len(catalog["tags"]) == 512
    result = read(path, signals="generic.tag0000,generic.tag0511")
    assert [s["reading"]["value"] for s in result["batches"][0]["signals"]] == [0.0, 511.0]


def test_empty_stored_selection_is_not_source_coverage_or_a_fabricated_fixture(tmp_path):
    path = save(tmp_path / "raw.sqlite3", [sample()])
    result = read(path, from_utc=(AT + timedelta(seconds=10)).isoformat(),
                  to_utc=(AT + timedelta(seconds=11)).isoformat())
    assert result["batches"] == [] and result["matchedBatches"] == 0
    assert result["selectionComplete"] is True and result["sourceCoverage"] == "NOT_ASSERTED"


@pytest.mark.parametrize("change", [
    {"from_utc": "2026-11-09T12:00:00"}, {"to_utc": AT.isoformat()},
    {"to_utc": (AT + timedelta(days=1, microseconds=1)).isoformat()},
    {"signals": "numeric.alpha,numeric.alpha"}, {"signals": ""},
    {"signals": "numeric.alpha'; DROP TABLE raw_batches;--"},
    {"signals": ",".join(f"tag{i}" for i in range(17))},
    {"max_batches": 0}, {"max_batches": 65}, {"max_batches": True}, {"run_id": "invalid"},
])
def test_invalid_queries_fail_before_opening_or_creating_a_database(tmp_path, change):
    path = tmp_path / "PRIVATE-missing" / "raw.sqlite3"
    failure(path, 422, "INVALID_QUERY", **change)
    assert not path.parent.exists()


def test_missing_archive_is_unavailable_without_creating_files(tmp_path):
    path = tmp_path / "PRIVATE-missing" / "raw.sqlite3"
    failure(path, 503, "RAW_ARCHIVE_UNAVAILABLE")
    assert not path.parent.exists()


@pytest.mark.parametrize("mode", ["schema", "empty_database", "malformed_json", "duplicate_json_key",
                                 "wrong_device", "wrong_sequence", "wrong_time", "real_source", "nan", "good_null"])
def test_invalid_archives_fail_closed_without_a_partial_success(tmp_path, mode):
    path = save(tmp_path / "PRIVATE-raw.sqlite3", [sample(0), sample(1)])
    db = sqlite3.connect(path)
    try:
        row = copy.deepcopy(sample(1))
        if mode == "schema": db.execute("PRAGMA user_version=99")
        elif mode == "empty_database":
            db.close(); path.unlink(); sqlite3.connect(path).close()
        else:
            if mode == "wrong_device": row["deviceId"] = "OTHER-01"
            elif mode == "wrong_sequence": row["sequence"] = 99
            elif mode == "wrong_time": row["observedAtUtc"] = (AT + timedelta(seconds=8)).isoformat()
            elif mode == "real_source": row["source"] = "REAL"
            elif mode == "nan": row["signals"][0]["value"] = float("nan")
            elif mode == "good_null": row["signals"][0]["value"] = None
            raw = json.dumps(row)
            if mode == "malformed_json": raw = "PRIVATE-invalid-json"
            elif mode == "duplicate_json_key": raw = '{"source":"SIMULATOR",' + raw[1:]
            db.execute("UPDATE raw_batches SET payload_json=? WHERE sequence=1", (raw,))
            db.commit()
        if mode == "schema": db.commit()
    finally:
        db.close()
    failure(path, 409, "RAW_ARCHIVE_INVALID")


@pytest.mark.parametrize("bound,value", [("MAX_CANDIDATE_BATCHES", 1), ("MAX_PAYLOAD_BYTES", 32),
                                        ("MAX_QUERY_BYTES", 32), ("MAX_CATALOG_TAGS", 1),
                                        ("MAX_SQL_STEPS", 0), ("MAX_QUERY_SECONDS", 0)])
def test_resource_bounds_return_an_error_without_success_prefixes(tmp_path, monkeypatch, bound, value):
    import aegis.raw_history as module
    signals = [{"signalKey": key, "value": 1.0, "unit": "unit", "quality": "GOOD"}
               for key in ("numeric.alpha", "numeric.beta")]
    path = save(tmp_path / "raw.sqlite3", [sample(0, signals=signals), sample(1)])
    before = db_state(path)
    monkeypatch.setattr(module, bound, value)
    if bound == "MAX_SQL_STEPS": monkeypatch.setattr(module, "SQL_PROGRESS_INTERVAL", 1)
    failure(path, 413, "RAW_QUERY_LIMIT", signals=None)
    assert db_state(path) == before


def test_matching_batch_limit_cannot_silently_truncate_results(tmp_path):
    path = save(tmp_path / "raw.sqlite3", [sample(0), sample(1)])
    failure(path, 413, "RAW_QUERY_LIMIT", max_batches=1)


def test_sequence_gaps_are_preserved_as_run_metadata_without_fabricated_time_ranges(tmp_path, monkeypatch):
    async def prepare():
        path = tmp_path / "raw.sqlite3"
        store = RawArchive(path); await store.start()
        try:
            await store.append([sample(5)], AT, gap=(0, 2))
            await store.append([sample(9)], AT, gap=(6, 7))
        finally: await store.close()
        return path
    path = asyncio.run(prepare())
    before = db_state(path)
    gaps = read(path)["sequenceGaps"]
    assert gaps == {"scope": "SELECTED_RUN_SEQUENCE_ONLY", "items": [
        {"firstSequence": 0, "lastSequence": 2, "reason": "REPLAY_OVERFLOW"},
        {"firstSequence": 6, "lastSequence": 7, "reason": "REPLAY_OVERFLOW"}]}
    import aegis.raw_history as module
    monkeypatch.setattr(module, "MAX_RUN_GAPS", 1)
    failure(path, 413, "RAW_QUERY_LIMIT")
    assert db_state(path) == before


def test_active_wal_reader_sees_only_committed_data_and_preserves_writer_cursor(tmp_path):
    async def check():
        path = tmp_path / "active.sqlite3"
        store = RawArchive(path); await store.start()
        writer = None
        try:
            await store.append([sample()], AT)
            cursor = await store.cursor(DEVICE)
            writer = sqlite3.connect(path, isolation_level=None)
            writer.execute("BEGIN IMMEDIATE")
            altered = sample(); altered["signals"][0]["value"] = 99.0
            writer.execute("UPDATE raw_batches SET payload_json=?", (json.dumps(altered),))
            assert read(path)["batches"][0]["signals"][0]["reading"]["value"] == 10.0
            writer.commit()
            assert read(path)["batches"][0]["signals"][0]["reading"]["value"] == 99.0
            assert await store.cursor(DEVICE) == cursor
            await store.append([sample(1)], AT + timedelta(seconds=1))
            assert await store.cursor(DEVICE) == (RUN, 1)
        finally:
            if writer is not None: writer.close()
            await store.close()
    asyncio.run(check())


def test_archive_without_the_existing_unique_gap_contract_is_rejected(tmp_path):
    path = save(tmp_path / "raw.sqlite3", [sample()])
    db = sqlite3.connect(path)
    try:
        db.executescript("""ALTER TABLE ingest_gaps RENAME TO old_gaps;
            CREATE TABLE ingest_gaps (device_id TEXT,run_id TEXT,first_sequence INTEGER,
                last_sequence INTEGER,reason TEXT);
            DROP TABLE old_gaps;""")
    finally:
        db.close()
    failure(path, 409, "RAW_ARCHIVE_INVALID")


def test_exclusive_database_lock_is_unavailable_and_does_not_return_empty_success(tmp_path):
    path = save(tmp_path / "raw.sqlite3", [sample()])
    blocker = sqlite3.connect(path, isolation_level=None)
    try:
        blocker.execute("PRAGMA journal_mode=DELETE")
        blocker.execute("BEGIN EXCLUSIVE")
        failure(path, 503, "RAW_ARCHIVE_UNAVAILABLE")
    finally:
        blocker.rollback()
        blocker.close()
    assert len(read(path)["batches"]) == 1


def test_actual_candidate_matching_and_selected_tag_boundaries_and_narrow_window_overflow(tmp_path):
    rows = [sample(i, at=(AT + timedelta(seconds=i)).isoformat()) for i in range(1024)]
    path = save(tmp_path / "raw.sqlite3", rows)
    selected = ["numeric.alpha", *(f"absent.tag{i:02d}" for i in range(15))]
    result = read(path, to_utc=(AT + timedelta(seconds=64)).isoformat(), signals=",".join(selected))
    assert result["scannedBatches"] == result["limits"]["candidateBatches"] == 1024
    assert result["matchedBatches"] == result["limits"]["matchingBatches"] == 64
    assert len(result["tags"]) == result["limits"]["signals"] == 16
    assert len(result["batches"]) == 64 and all(len(b["signals"]) == 16 for b in result["batches"])
    save(path, [sample(1024, at=(AT + timedelta(seconds=1024)).isoformat())])
    # Even this one-second selection cannot bypass the unindexed run scan limit.
    failure(path, 413, "RAW_QUERY_LIMIT")
