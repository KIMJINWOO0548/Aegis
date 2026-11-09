"""Bounded raw archive API extension preserves legacy flow and source contracts."""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app, main
from aegis.archive import RawArchive
from aegis.collector import Collector, SourceConfig
from aegis.processes import ProcessCollector


TOKEN = "synthetic-raw-history-api-" + "x" * 32
DEVICE = "CMP-SYNTHETIC.PMC-01"
PEER = "CMP-SYNTHETIC.PMC-02"
RUN = "11111111-2222-4333-8444-555555555555"
OTHER_RUN = "99999999-8888-4777-8666-555555555555"
AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
RECEIPT = AT + timedelta(hours=3)
QUERY = {"runId": RUN, "fromUtc": AT.isoformat(), "toUtc": (AT + timedelta(seconds=2)).isoformat()}
HISTORY = QUERY | {"signals": "raw.counter,raw.temperature"}


def sample(sequence=0, *, device=DEVICE, run=RUN, signals=None):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
            "runId": run, "sequence": sequence, "observedAtUtc": (AT + timedelta(seconds=sequence)).isoformat(),
            "deviceStateVersion": 17, "modeId": "PRODUCTION", "signals": signals if signals is not None else [
                {"signalKey": "raw.counter", "value": 11 + sequence, "unit": "count", "quality": "GOOD"}]}


def save(path, rows):
    async def write():
        store = RawArchive(path)
        await store.start()
        try:
            # Separate appends permit multiple source runs/devices in a shared archive.
            for row in rows:
                await store.append([row], RECEIPT, equipment_id="STORED-EQUIPMENT", module_id="STORED-MODULE")
        finally:
            await store.close()
    asyncio.run(write())


def source(device=DEVICE, port=19341, transport="replay"):
    return SourceConfig(deviceId=device, baseUrl=f"http://127.0.0.1:{port}", transport=transport,
                        equipmentId="CURRENT-EQUIPMENT", moduleId="CURRENT-MODULE", analysisProfile="raw")


def collector(path, sources=None):
    return Collector(sources or [source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, archive=RawArchive(path))


def client(acquisition=None, paths=None):
    options = {} if paths is None else {"raw_archive_paths": paths}
    # No lifespan is entered: history reads must not start acquisition or its writers.
    return TestClient(create_app(TOKEN, collector=acquisition, **options),
                      base_url="http://127.0.0.1", headers={"X-Aegis-Token": TOKEN})


def route(kind="raw-history", device=DEVICE):
    return f"/v1/devices/{device}/{kind}"


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code and body["requestId"]
    assert "batches" not in body and "tags" not in body
    return body


@pytest.fixture
def shared(tmp_path):
    path = tmp_path / "shared.sqlite3"
    rows = [sample(0, signals=[
        {"signalKey": "raw.counter", "value": 11, "unit": "count", "quality": "GOOD"},
        {"signalKey": "raw.temperature", "value": None, "unit": "degC", "quality": "MISSING"}]),
        sample(1, signals=[{"signalKey": "raw.counter", "value": 12, "unit": "count", "quality": "BAD"}]),
        sample(0, device=PEER, signals=[{"signalKey": "private.peer", "value": 999, "unit": "count", "quality": "GOOD"}]),
        sample(0, run=OTHER_RUN, signals=[{"signalKey": "private.other-run", "value": 888, "unit": "count", "quality": "GOOD"}])]
    save(path, rows)
    acquisition = collector(path, [source(), source(PEER, 19342)])
    return path, acquisition, rows


def test_history_reads_only_the_selected_device_run_and_keeps_original_missing_quality_and_receipt(shared):
    path, acquisition, originals = shared
    api = client(acquisition, {DEVICE: path, PEER: path})
    response = api.get(route(), params=HISTORY)
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "raw-query-1" and body["kind"] == "RAW_ARCHIVE_HISTORY"
    assert body["selectionComplete"] is True and body["sourceCoverage"] == "NOT_ASSERTED"
    assert body["window"]["bounds"] == "[from,to)"
    assert body["scannedBatches"] == body["matchedBatches"] == 2
    assert len(body["batches"]) == 2
    for index, batch in enumerate(body["batches"]):
        expected = {key: value for key, value in originals[index].items() if key != "signals"}
        expected.update(receivedAtUtc=RECEIPT.isoformat(), equipmentId="STORED-EQUIPMENT", moduleId="STORED-MODULE")
        assert {key: batch[key] for key in expected} == expected
    assert body["batches"][0]["signals"] == [
        {"signalKey": "raw.counter", "present": True,
         "reading": {"value": 11, "unit": "count", "quality": "GOOD"}},
        {"signalKey": "raw.temperature", "present": True,
         "reading": {"value": None, "unit": "degC", "quality": "MISSING"}}]
    assert type(body["batches"][0]["signals"][0]["reading"]["value"]) is int
    assert body["batches"][1]["signals"] == [
        {"signalKey": "raw.counter", "present": True,
         "reading": {"value": 12, "unit": "count", "quality": "BAD"}},
        {"signalKey": "raw.temperature", "present": False, "reading": None}]
    assert "private.peer" not in response.text and "private.other-run" not in response.text
    assert str(path) not in response.text and "CURRENT-MODULE" not in response.text
    assert acquisition._tasks == [] and acquisition._archive._executor is None


def test_tags_are_observed_window_metadata_with_quality_counts_and_no_batch_values(shared):
    path, acquisition, _ = shared
    response = client(acquisition, {DEVICE: path}).get(route("raw-tags"), params=QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "raw-query-1" and body["kind"] == "RAW_ARCHIVE_OBSERVED_TAGS"
    assert body["selectionComplete"] is True and body["sourceCoverage"] == "NOT_ASSERTED"
    assert "batches" not in body
    assert {row["signalKey"]: row for row in body["tags"]} == {
        "raw.counter": {"signalKey": "raw.counter", "units": ["count"], "presentBatches": 2,
                        "absentBatches": 0, "qualities": {"GOOD": 1, "BAD": 1}},
        "raw.temperature": {"signalKey": "raw.temperature", "units": ["degC"], "presentBatches": 1,
                            "absentBatches": 1, "qualities": {"MISSING": 1}}}


def test_history_window_is_half_open_with_offset_equivalence_and_unknown_tag_presence(shared):
    path, acquisition, _ = shared
    api = client(acquisition, {DEVICE: path})
    params = HISTORY | {"fromUtc": "2026-11-01T09:00:01+09:00", "toUtc": "2026-11-01T09:00:02+09:00",
                        "signals": "not.recorded"}
    body = api.get(route(), params=params).json()
    assert len(body["batches"]) == 1 and body["batches"][0]["sequence"] == 1
    assert body["batches"][0]["signals"] == [{"signalKey": "not.recorded", "present": False, "reading": None}]


@pytest.mark.parametrize("kind", ["raw-tags", "raw-history"])
def test_unknown_run_is_a_complete_empty_selection_without_source_coverage_claim(shared, kind):
    path, acquisition, _ = shared
    response = client(acquisition, {DEVICE: path}).get(route(kind), params=HISTORY | {
        "runId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"})
    assert response.status_code == 200
    body = response.json()
    assert body["selectionComplete"] is True and body["sourceCoverage"] == "NOT_ASSERTED"
    assert body["tags" if kind == "raw-tags" else "batches"] == []


@pytest.mark.parametrize("kind", ["raw-tags", "raw-history"])
@pytest.mark.parametrize("mode", ["fixture", "snapshot", "unmapped-replay"])
def test_sources_without_supported_server_mapping_do_not_return_mock_or_empty_success(tmp_path, kind, mode):
    if mode == "fixture":
        api, device = client(), "EQ-SUPPLY-01"
    else:
        acquisition = collector(tmp_path / "never-created.sqlite3", [source(transport="snapshot" if mode == "snapshot" else "replay")])
        api, device = client(acquisition), DEVICE
    assert_error(api.get(route(kind, device), params=HISTORY), 501, "RAW_ARCHIVE_NOT_SUPPORTED")
    assert not (tmp_path / "never-created.sqlite3").exists()


@pytest.mark.parametrize("mapping_kind", ["unknown-device", "snapshot-device"])
def test_server_mapping_rejects_unknown_or_snapshot_device_keys(tmp_path, mapping_kind):
    acquisition = collector(tmp_path / "unused.sqlite3", [source(transport="snapshot" if mapping_kind == "snapshot-device" else "replay")])
    with pytest.raises(ValueError):
        create_app(TOKEN, collector=acquisition, raw_archive_paths={
            "UNKNOWN" if mapping_kind == "unknown-device" else DEVICE: tmp_path / "unused.sqlite3"})
    assert not (tmp_path / "unused.sqlite3").exists()


def test_mapping_is_copied_and_client_query_cannot_redirect_to_another_archive(shared, tmp_path):
    path, acquisition, _ = shared
    redirect = tmp_path / "should-not-exist.sqlite3"
    paths = {DEVICE: path}
    api = client(acquisition, paths)
    paths[DEVICE] = redirect
    response = api.get(route(), params=HISTORY | {"archivePath": str(redirect), "path": str(redirect)})
    assert response.status_code == 200 and len(response.json()["batches"]) == 2
    assert not redirect.exists() and str(redirect) not in response.text
    parameters = api.app.openapi()["paths"]["/v1/devices/{id}/raw-history"]["get"]["parameters"]
    assert not {"archivePath", "path", "file", "database"} & {p["name"] for p in parameters}


def test_raw_openapi_exposes_required_selection_and_bounded_optional_batch_count(shared):
    path, acquisition, _ = shared
    schema = client(acquisition, {DEVICE: path}).app.openapi()
    for kind in ("raw-history", "raw-tags"):
        items = schema["paths"][f"/v1/devices/{{id}}/{kind}"]["get"]["parameters"]
        parameters = {p["name"]: p for p in items}
        required = {"runId", "fromUtc", "toUtc"} | ({"signals"} if kind == "raw-history" else set())
        assert all(parameters[name]["in"] == "query" and parameters[name]["required"] for name in required)
        limit = parameters["maxBatches"]
        assert limit["in"] == "query" and limit["required"] is False
        assert limit["schema"]["type"] == "integer"
        assert limit["schema"]["default"] == limit["schema"]["maximum"] == 64
        assert limit["schema"]["minimum"] == 1


def test_process_collector_mapping_selects_one_module_archive_without_starting_ipc(tmp_path):
    acquisition = ProcessCollector([source(), source(PEER, 19342)], tmp_path / "modules")
    for device, path in acquisition.archive_paths.items():
        save(path, [sample(device=device)])
    api = client(acquisition, acquisition.archive_paths)
    for device in (DEVICE, PEER):
        body = api.get(route(device=device), params=HISTORY).json()
        assert len(body["batches"]) == 1 and body["batches"][0]["deviceId"] == device
    assert acquisition._tasks == [] and acquisition._executor is None
    assert all(worker.process is None for worker in acquisition._workers)


@pytest.mark.parametrize("overrides", [
    {"runId": "not-a-uuid"}, {"runId": ""}, {"fromUtc": "2026-11-01T00:00:00"},
    {"fromUtc": "invalid-private-input"}, {"toUtc": AT.isoformat()},
    {"toUtc": (AT + timedelta(days=1, seconds=1)).isoformat()},
    {"signals": ""}, {"signals": "raw.counter,raw.counter"}, {"signals": "bad tag"},
    {"signals": ",".join(f"tag.{i}" for i in range(17))},
    {"maxBatches": 0}, {"maxBatches": 65}, {"maxBatches": "1.5"},
])
def test_history_rejects_invalid_or_unbounded_query_without_echoing_values(shared, overrides):
    path, acquisition, _ = shared
    response = client(acquisition, {DEVICE: path}).get(route(), params=HISTORY | overrides)
    assert_error(response, 422, "INVALID_QUERY")
    assert "invalid-private-input" not in response.text and str(path) not in response.text


@pytest.mark.parametrize("kind,missing", [
    ("raw-tags", "runId"), ("raw-tags", "fromUtc"), ("raw-tags", "toUtc"),
    ("raw-history", "runId"), ("raw-history", "fromUtc"), ("raw-history", "toUtc"),
    ("raw-history", "signals"),
])
def test_raw_queries_require_explicit_selection_fields(shared, kind, missing):
    path, acquisition, _ = shared
    params = QUERY if kind == "raw-tags" else HISTORY
    assert_error(client(acquisition, {DEVICE: path}).get(route(kind),
        params={key: value for key, value in params.items() if key != missing}), 422, "INVALID_QUERY")


@pytest.mark.parametrize("kind", ["raw-tags", "raw-history"])
def test_selected_batch_limit_has_no_partial_success(shared, kind):
    path, acquisition, _ = shared
    assert_error(client(acquisition, {DEVICE: path}).get(route(kind), params=HISTORY | {"maxBatches": 1}),
                 413, "RAW_QUERY_LIMIT")


def test_missing_archive_is_unavailable_and_is_never_created(tmp_path):
    path = tmp_path / "absent-directory" / "archive.sqlite3"
    response = client(collector(path), {DEVICE: path}).get(route(), params=HISTORY)
    assert_error(response, 503, "RAW_ARCHIVE_UNAVAILABLE")
    assert not path.parent.exists() and str(path) not in response.text


def test_invalid_stored_payload_is_an_error_instead_of_normal_empty_data(shared):
    path, acquisition, _ = shared
    with sqlite3.connect(path) as db:
        db.execute("UPDATE raw_batches SET payload_json=? WHERE device_id=? AND run_id=? AND sequence=0",
                   ('{"invalid":"private-stored-text"}', DEVICE, RUN))
    response = client(acquisition, {DEVICE: path}).get(route(), params=HISTORY)
    assert_error(response, 409, "RAW_ARCHIVE_INVALID")
    assert "private-stored-text" not in response.text and str(path) not in response.text


@pytest.mark.parametrize("kind", ["raw-tags", "raw-history"])
@pytest.mark.parametrize("headers,status,code", [
    ({"X-Aegis-Token": "wrong"}, 401, "UNAUTHORIZED"),
    ({"Origin": "https://invalid.example"}, 403, "BROWSER_ORIGIN_FORBIDDEN"),
    ({"Host": "invalid.example"}, 400, "INVALID_HOST"),
])
def test_raw_routes_keep_existing_security_boundary(shared, kind, headers, status, code):
    path, acquisition, _ = shared
    assert_error(client(acquisition, {DEVICE: path}).get(route(kind), params=HISTORY, headers=headers), status, code)


def test_unknown_device_does_not_read_a_different_device_archive(shared):
    path, acquisition, _ = shared
    assert_error(client(acquisition, {DEVICE: path}).get(route(device="UNKNOWN"), params=HISTORY), 404, "NOT_FOUND")


def test_extension_preserves_default_health_source_context_and_legacy_live_flow_contract(shared):
    path, acquisition, _ = shared
    before = client(acquisition)
    after = client(acquisition, {DEVICE: path})
    for url in ("/v1/health", "/v1/health?includeSourceContext=false", "/v1/health?includeSourceContext=true",
                "/v1/devices", "/v1/connections", f"/v1/devices/{DEVICE}/observations", "/v1/ingestion"):
        assert before.get(url).json() == after.get(url).json()
    for api in (before, after):
        assert_error(api.get(f"/v1/devices/{DEVICE}/trends", params={
            "signals": "actualFlowLpm", "fromUtc": QUERY["fromUtc"], "toUtc": QUERY["toUtc"]}),
            501, "GATE_NOT_IMPLEMENTED")


@pytest.mark.parametrize("isolated", [False, True])
def test_cli_injects_only_configured_replay_archive_paths_without_starting_services(tmp_path, monkeypatch, isolated):
    import uvicorn

    replay = source()
    snapshot = source(PEER, 19342, "snapshot")
    config = tmp_path / "sources.json"
    config.write_text(json.dumps({"sources": [s.model_dump(mode="json") for s in (replay, snapshot)]}))
    if isolated:
        directory = tmp_path / "modules"
        path = ProcessCollector([replay, snapshot], directory).archive_paths[DEVICE]
        extra = ["--isolate", "--archive-directory", str(directory)]
    else:
        path = tmp_path / "shared.sqlite3"
        extra = ["--archive", str(path)]
    save(path, [sample()])
    captured = {}

    def capture(app, **kwargs):
        captured.update(app=app, kwargs=kwargs)

    monkeypatch.setattr(uvicorn, "run", capture)
    monkeypatch.setenv("AEGIS_API_TOKEN", TOKEN)
    monkeypatch.setenv("AEGIS_EMULATOR_TOKEN", TOKEN)
    monkeypatch.setattr(sys, "argv", ["aegis-api", "--emulators", str(config), *extra])
    main()
    api = TestClient(captured["app"], base_url="http://127.0.0.1", headers={"X-Aegis-Token": TOKEN})
    assert api.get(route(), params=HISTORY).status_code == 200
    assert_error(api.get(route(device=PEER), params=HISTORY), 501, "RAW_ARCHIVE_NOT_SUPPORTED")
    assert captured["kwargs"]["host"] == "127.0.0.1"
