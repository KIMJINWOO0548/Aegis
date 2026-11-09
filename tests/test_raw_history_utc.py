"""Raw UTC normalization failures stay inside the existing validation contracts."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app
from aegis.archive import RawArchive
from aegis.collector import Collector, SourceConfig
from aegis.raw_history import RawQueryError, read_raw_archive


TOKEN = "synthetic-raw-utc-" + "x" * 32
DEVICE = "UTC-CONTROL-01"
RUN = "11111111-1111-4111-8111-111111111111"
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
OVERFLOW = ["0001-01-01T00:00:00+01:00", "9999-12-31T23:00:00-02:00"]


def sample(observed=None):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": DEVICE,
        "runId": RUN, "sequence": 0, "observedAtUtc": observed or AT.isoformat(),
        "deviceStateVersion": 1, "modeId": "PRODUCTION", "signals": [
            {"signalKey": "numeric.alpha", "value": None, "unit": "count", "quality": "MISSING"}]}


def prepare(path, original=None):
    async def write():
        store = RawArchive(path)
        await store.start()
        try:
            await store.append([original or sample()], AT, equipment_id="UTC-SYNTHETIC", module_id="MODULE-01")
        finally:
            await store.close()
    asyncio.run(write())
    acquisition = Collector([SourceConfig(deviceId=DEVICE, baseUrl="http://127.0.0.1:19351", transport="replay")],
                            {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=RawArchive(path))
    return TestClient(create_app(TOKEN, collector=acquisition, raw_archive_paths={DEVICE: path}),
                      base_url="http://127.0.0.1", headers={"X-Aegis-Token": TOKEN})


def params(start=AT):
    return {"runId": RUN, "fromUtc": start.isoformat(), "toUtc": (start + timedelta(seconds=1)).isoformat(),
            "signals": "numeric.alpha,numeric.absent"}


def state(path):
    connection = sqlite3.connect(path)
    try:
        return {name: connection.execute("SELECT * FROM " + name + " ORDER BY 1,2,3").fetchall()
                for name in ("raw_batches", "ingest_cursors", "ingest_gaps")}
    finally:
        connection.close()


@pytest.mark.parametrize("kind", ["raw-history", "raw-tags"])
@pytest.mark.parametrize("field", ["fromUtc", "toUtc"])
@pytest.mark.parametrize("bad", OVERFLOW)
def test_query_utc_overflow_is_422_without_echo_or_archive_changes(tmp_path, kind, field, bad):
    path = tmp_path / "PRIVATE-raw.sqlite3"
    api = prepare(path)
    before = state(path)
    response = api.get(f"/v1/devices/{DEVICE}/{kind}", params=params() | {field: bad})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_QUERY" and body["requestId"]
    assert "batches" not in body and "tags" not in body
    assert bad not in response.text and str(path) not in response.text
    assert state(path) == before
    assert api.get(f"/v1/devices/{DEVICE}/{kind}", params=params()).status_code == 200


@pytest.mark.parametrize("bad", OVERFLOW)
def test_direct_query_utc_overflow_is_the_existing_typed_error_before_file_open(tmp_path, bad):
    path = tmp_path / "PRIVATE-absent" / "raw.sqlite3"
    with pytest.raises(RawQueryError) as error:
        read_raw_archive(path, DEVICE, RUN, bad, AT.isoformat(), signals="numeric.alpha")
    assert error.value.status == 422 and error.value.code == "INVALID_QUERY"
    assert not path.parent.exists()


@pytest.mark.parametrize("kind", ["raw-history", "raw-tags"])
@pytest.mark.parametrize("field", ["payloadObserved", "indexedObserved", "firstReceipt"])
@pytest.mark.parametrize("bad", OVERFLOW)
def test_stored_utc_overflow_is_409_without_success_prefix_or_rewriting_originals(tmp_path, kind, field, bad):
    path = tmp_path / "PRIVATE-raw.sqlite3"
    api = prepare(path)
    with sqlite3.connect(path) as connection:
        if field == "payloadObserved":
            original = sample(bad)
            connection.execute("UPDATE raw_batches SET payload_json=?", (json.dumps(original),))
        elif field == "indexedObserved":
            connection.execute("UPDATE raw_batches SET observed_at_utc=?", (bad,))
        else:
            connection.execute("UPDATE raw_batches SET received_at_utc=?", (bad,))
    before = state(path)
    response = api.get(f"/v1/devices/{DEVICE}/{kind}", params=params())
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "RAW_ARCHIVE_INVALID" and body["requestId"]
    assert "batches" not in body and "tags" not in body
    assert bad not in response.text and str(path) not in response.text
    assert state(path) == before


@pytest.mark.parametrize("kind", ["raw-history", "raw-tags"])
@pytest.mark.parametrize("observed,start", [
    ("0001-01-01T00:00:00Z", datetime(1, 1, 1, tzinfo=timezone.utc)),
    ("0001-01-01T01:00:00+01:00", datetime(1, 1, 1, tzinfo=timezone.utc)),
    ("9999-12-31T23:59:58Z", datetime(9999, 12, 31, 23, 59, 58, tzinfo=timezone.utc)),
    ("9999-12-31T22:59:58-01:00", datetime(9999, 12, 31, 23, 59, 58, tzinfo=timezone.utc)),
])
def test_valid_utc_boundary_and_offsets_keep_original_time_receipt_and_missing_semantics(tmp_path, kind, observed, start):
    path = tmp_path / "raw.sqlite3"
    api = prepare(path, sample(observed))
    before = state(path)
    response = api.get(f"/v1/devices/{DEVICE}/{kind}", params=params(start))
    assert response.status_code == 200
    body = response.json()
    assert body["matchedBatches"] == 1 and body["selectionComplete"] is True
    assert body["sourceCoverage"] == "NOT_ASSERTED"
    if kind == "raw-history":
        batch = body["batches"][0]
        assert batch["observedAtUtc"] == observed and batch["receivedAtUtc"] == AT.isoformat()
        assert batch["signals"] == [
            {"signalKey": "numeric.alpha", "present": True,
             "reading": {"value": None, "unit": "count", "quality": "MISSING"}},
            {"signalKey": "numeric.absent", "present": False, "reading": None}]
    assert state(path) == before
