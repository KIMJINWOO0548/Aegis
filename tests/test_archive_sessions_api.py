import asyncio
from datetime import timedelta
import hashlib
import os
from pathlib import Path

import httpx
import pytest

from aegis.api import create_app
from aegis.archive import RawArchive
from aegis.archive_sessions import ArchiveSessions
from aegis.stored_records import QueryError
from test_archive import AT, RUN, snapshot


TOKEN = "local-history-" + "x" * 32
DEVICE = "EQ-SUPPLY-01"
SESSION = "run-old12345"
ACTIVE = "run-new12345"


def archive_path(folder: Path, device: str = DEVICE) -> Path:
    marker = hashlib.sha256(device.encode("ascii")).hexdigest()[:12]
    return folder / f"module-{device}-{marker}.sqlite3"


def run(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if os.name == "nt" else None) as runner:
        runner.run(coroutine)


def test_past_session_recovers_exact_values_and_preserves_previous_files(tmp_path):
    root = tmp_path / "runs"
    old = root / SESSION
    current = root / ACTIVE
    old.mkdir(parents=True)
    current.mkdir()

    async def check():
        previous = RawArchive(archive_path(old))
        active = RawArchive(archive_path(current))
        await previous.start()
        try:
            await active.start()
            await previous.append([snapshot(1, device=DEVICE, value=0)], AT)
            await previous.append([snapshot(2, device=DEVICE, value=None, quality="MISSING")],
                                  AT + timedelta(milliseconds=250))
            await active.append([snapshot(99, device=DEVICE)], AT)
            before = {path.name: path.read_bytes() for path in old.iterdir()}
            app = create_app(TOKEN, archive_sessions_root=root, active_archive=current, stored_sources=[
                {"storeId": DEVICE, "kind": "RAW_V1", "path": archive_path(current)}])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                         headers={"X-Aegis-Token": TOKEN}) as client:
                response = await client.get("/v1/archive-sessions")
                assert response.status_code == 200
                catalog = response.json()
                assert catalog["schemaVersion"] == "archive-sessions-1"
                assert not catalog["hasMore"]
                assert [(s["sessionId"], s["storeIds"]) for s in catalog["sessions"]] == [(SESSION, [DEVICE])]
                assert str(root) not in response.text
                endpoint = f"/v1/archive-sessions/{SESSION}/records"
                response = await client.get(endpoint, params={"signals": "actualFlowLpm"})
                assert response.status_code == 200
                result = response.json()
                assert result["selectionComplete"] and result["timeBasis"] == "FIRST_RECEIPT"
                assert result["sources"][0]["integrity"] == "VERIFIED_WINDOW_RECORDS"
                first, second = result["records"]
                assert first["identity"]["nativeKey"] == [DEVICE, RUN, 1]
                assert first["signals"][0]["reading"]["valueText"] in ("0", "0.0")
                assert second["signals"][0]["reading"]["valueText"] is None
                assert second["signals"][0]["reading"]["quality"] == "MISSING"
                assert result["window"]["receivedToUtc"] == "2026-10-18T00:00:00.250001Z"
                selection = {"deviceId": DEVICE, "storeId": DEVICE, "runId": RUN, **result["window"]}
                selection.pop("bounds")
                response = await client.get(endpoint, params=selection)
                assert response.status_code == 200
                assert response.json()["runEvidence"]["storedRowsInReceivedWindow"] == 2
                active_result = await client.get("/v1/stored-records", params=selection)
                assert active_result.status_code == 200
                assert [row["identity"]["nativeKey"][-1] for row in active_result.json()["records"]] == [99]
                await active.append([snapshot(100, device=DEVICE)], AT + timedelta(milliseconds=100))
                active_result = await client.get("/v1/stored-records", params=selection)
                assert [row["identity"]["nativeKey"][-1] for row in active_result.json()["records"]] == [99, 100]
                assert str(root) not in response.text
                assert (await client.get(f"/v1/archive-sessions/{ACTIVE}/records")).status_code == 404
                assert (await client.get("/v1/archive-sessions/run-unseen01/records")).status_code == 404
                assert (await client.get(endpoint, params={"path": str(old)})).status_code == 422
                assert (await client.get(endpoint, params={"receivedFromUtc": "2026-10-18T00:00:00Z"})).status_code == 422
                assert (await client.get(endpoint, headers={"X-Aegis-Token": "wrong"})).status_code == 401
                analysis_route = f"/v1/archive-sessions/{SESSION}/flow-analysis"
                analysis = await client.get(analysis_route, params={"deviceId": DEVICE})
                assert analysis.status_code == 200
                assert analysis.json()["recommendation"]["status"] == "INSUFFICIENT_INPUT"
                assert analysis.json()["zeroFlowRows"] == 1
                assert analysis.json()["invalidPairs"] == 2
                assert (await client.get(analysis_route, params={"deviceId": "EQ-SUPPLY-02"})).status_code == 404
                assert (await client.get(analysis_route, params={"deviceId": DEVICE, "path": str(old)})).status_code == 422
            assert before == {path.name: path.read_bytes() for path in old.iterdir()}
        finally:
            await previous.close()
            await active.close()

    run(check())


def test_catalog_excludes_unowned_file_names_and_linked_databases(tmp_path):
    active = tmp_path / ACTIVE
    active.mkdir()
    unowned = tmp_path / "run-unowned1"
    unowned.mkdir()
    (unowned / "module-EQ-SUPPLY-01-000000000000.sqlite3").touch()
    outside = tmp_path / "outside.sqlite3"
    outside.touch()
    linked = tmp_path / "run-linked01"
    linked.mkdir()
    os.link(outside, archive_path(linked))

    sessions = ArchiveSessions(tmp_path, active)
    assert sessions.catalog()["sessions"] == []
    with pytest.raises(QueryError) as failure, sessions.snapshots(linked.name, None):
        pytest.fail("A linked source must be rejected before opening a snapshot.")
    assert failure.value.code == "SOURCE_UNAVAILABLE"


def test_configured_archive_root_must_contain_the_active_run(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    active = other / ACTIVE
    active.mkdir()
    with pytest.raises(ValueError):
        create_app(TOKEN, archive_sessions_root=tmp_path, active_archive=active)


def test_portable_window_handles_a_long_session_without_claiming_full_store_validation(tmp_path):
    from aegis.stored_records import MAX_RAW_ROWS, query_stored_records

    initial = MAX_RAW_ROWS + 1
    path = archive_path(tmp_path)

    async def populate():
        store = RawArchive(path)
        await store.start()
        try:
            await store.append([snapshot(i, device=DEVICE) for i in range(initial)], AT)
            await store.append([snapshot(initial, device=DEVICE, value=-2.0)], AT + timedelta(seconds=2))
        finally:
            await store.close()

    run(populate())
    source = {"storeId": DEVICE, "kind": "RAW_V1", "path": path}
    start = "2026-10-18T00:00:01Z"
    end = "2026-10-18T00:00:03Z"
    result = query_stored_records([source], start, end, device_id=DEVICE, portable_window=True)
    assert [record["identity"]["nativeKey"][-1] for record in result["records"]] == [initial]
    assert result["sources"][0]["integrity"] == "VERIFIED_WINDOW_RECORDS"
    assert result["limits"]["maxRawRowsPerStore"] is None
    with pytest.raises(QueryError) as failure:
        query_stored_records([source], start, end)
    assert failure.value.code == "QUERY_LIMIT"


def test_flow_analysis_keeps_forecast_holdout_separate_from_unavailable_alert_labels(tmp_path):
    from aegis.flow_analysis import analyze_archived_flow

    path = archive_path(tmp_path)

    async def populate():
        store = RawArchive(path)
        await store.start()
        try:
            for i in range(140):
                at = AT + timedelta(seconds=5 * i)
                record = snapshot(i, device=DEVICE, value=11.0 if i else 0)
                record["observedAtUtc"] = at.isoformat()
                record["signals"].append({"signalKey": "setpointFlowLpm", "quality": "GOOD", "unit": "L/min",
                                          "value": 10})
                await store.append([record], at)
        finally:
            await store.close()

    run(populate())
    source = {"storeId": DEVICE, "kind": "RAW_V1", "path": path, "cleanPortable": True}
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = analyze_archived_flow(SESSION, DEVICE, source)
    assert result["labelEvidence"] == "NOT_PROVIDED"
    assert result["zeroFlowRows"] == 1
    assert result["validPairs"] == 140
    assert result["modeSummaries"][0]["medianResidualLpm"] == 1.0
    assert result["recommendation"] == {
        "status": "RECOMMENDATION_ONLY", "modelId": "rule-ewma-flow",
        "reasonCode": "EXISTING_RULE_HAS_COMPATIBLE_INPUT",
    }
    assert result["forecastComparison"] == [{
        "modeId": "PRODUCTION", "trainRows": 45, "testRows": 35,
        "setpointMaeLpm": 1.0, "biasAdjustedMaeLpm": 0.0,
    }]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
