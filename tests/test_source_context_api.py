"""Opt-in current source declarations do not change the strict legacy health payload."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app
from aegis.archive import RawArchive
from aegis.collector import Collector, SIGNAL_UNITS, SourceConfig
from aegis.processes import ProcessCollector


TOKEN = "synthetic-context-api-" + "x" * 32
LEGACY_FIXTURE = {"status": "OK", "schemaVersion": "3.4.0", "stage": "D0a", "mock": True, "mode": "FIXTURE"}


def row(device, producer, method, access, evidence, timestamp, sequence):
    return {"deviceId": device, "declaredOrigin": "SYNTHETIC", "producerKind": producer,
            "acquisitionMethod": method, "sourceAccessMode": access, "originEvidence": evidence,
            "sourceTimestampPolicy": timestamp, "sourceSequencePolicy": sequence}


def context(rows):
    return {"schemaVersion": "source-context-1", "scope": "CURRENT_CONFIGURATION",
            "archivedRowProvenance": "NOT_INFERRED", "realEquipmentSupported": False,
            "hardRealtimeGuaranteed": False, "sources": rows}


def client(collector=None):
    # No lifespan is entered: these diagnostics must not start acquisition or open an archive.
    return TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                      headers={"X-Aegis-Token": TOKEN})


def test_default_false_and_unrecognized_query_keep_the_exact_legacy_health_shape():
    api = client()
    for url in ("/v1/health", "/v1/health?includeSourceContext=false",
                "/v1/health?include_source_context=true"):
        response = api.get(url)
        assert response.status_code == 200
        assert response.json() == LEGACY_FIXTURE
        assert "sourceContext" not in response.json()  # Even a null extra field breaks strict clients.


def test_fixture_context_explicitly_separates_synthetic_fixed_data_from_unexposed_sequence():
    response = client().get("/v1/health?includeSourceContext=true")
    assert response.status_code == 200
    body = response.json()
    assert {k: v for k, v in body.items() if k != "sourceContext"} == LEGACY_FIXTURE
    assert body["sourceContext"] == context([row("EQ-SUPPLY-01", "FIXTURE", "FIXED_FIXTURE",
        "NOT_APPLICABLE", "BUILT_IN_FIXTURE", "FIXTURE_DEFINED", "NOT_EXPOSED")])
    assert body["sourceContext"]["realEquipmentSupported"] is False
    assert body["sourceContext"]["hardRealtimeGuaranteed"] is False


@pytest.mark.parametrize("isolated", [False, True])
def test_mixed_simulator_methods_are_per_source_and_expose_no_configuration_secrets(tmp_path, isolated):
    sources = [
        SourceConfig(deviceId="SNAPSHOT-01", baseUrl="http://127.0.0.1:19401",
            tokenEnvironment="PRIVATE_SNAPSHOT_TOKEN", equipmentId="PRIVATE_EQUIPMENT", moduleId="PRIVATE_MODULE"),
        SourceConfig(deviceId="REPLAY-01", baseUrl="http://127.0.0.1:19402", transport="replay",
            tokenEnvironment="PRIVATE_REPLAY_TOKEN"),
    ]
    if isolated:
        collector = ProcessCollector(sources, tmp_path / "PRIVATE_ARCHIVE")
    else:
        collector = Collector(sources, {"PRIVATE_SNAPSHOT_TOKEN": TOKEN, "PRIVATE_REPLAY_TOKEN": TOKEN},
                              archive=RawArchive(tmp_path / "PRIVATE_ARCHIVE.sqlite3"))
    api = client(collector)
    legacy = api.get("/v1/health").json()
    assert legacy == {"status": "DEGRADED", "schemaVersion": "3.4.0", "stage": "EMULATOR_LINK",
                      "mock": True, "mode": "EMULATORS"}
    response = api.get("/v1/health?includeSourceContext=true")
    assert response.status_code == 200
    body = response.json()
    assert {k: v for k, v in body.items() if k != "sourceContext"} == legacy
    assert body["sourceContext"] == context([
        row("SNAPSHOT-01", "SIMULATOR", "SNAPSHOT_POLL", "READ_ONLY", "CONTRACT_DECLARED", "PRODUCER_REQUIRED", "PRODUCER_REQUIRED"),
        row("REPLAY-01", "SIMULATOR", "SOURCE_BUFFER_REPLAY", "READ_ONLY", "CONTRACT_DECLARED", "PRODUCER_REQUIRED", "PRODUCER_REQUIRED"),
    ])
    assert "PRIVATE_" not in response.text and str(tmp_path) not in response.text
    assert "127.0.0.1" not in response.text and TOKEN not in response.text
    assert "observedAtUtc" not in response.text and "receivedAtUtc" not in response.text
    assert all(worker.process is None for worker in collector._workers) if isolated else collector._tasks == []


@pytest.mark.parametrize("transport,method", [("snapshot", "SNAPSHOT_POLL"), ("replay", "SOURCE_BUFFER_REPLAY")])
def test_current_received_simulator_data_never_promotes_declared_origin_or_realtime_guarantee(tmp_path, transport, method):
    async def check():
        now = datetime.now(timezone.utc)
        payload = {"protocolVersion": "1.0" if transport == "snapshot" else "1.1", "source": "SIMULATOR",
            "deviceId": "ACTIVE-01", "runId": str(uuid4()), "sequence": 0, "observedAtUtc": now.isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"}
                        for key, unit in SIGNAL_UNITS.items()]}
        page = {"streamVersion": "1.0", "deviceId": "ACTIVE-01", "runId": payload["runId"],
                "oldestSequence": 0, "latestSequence": 0, "snapshots": [payload]}
        archive = RawArchive(tmp_path / "active.sqlite3") if transport == "replay" else None
        if archive is not None:
            await archive.start()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload if transport == "snapshot" else page))) as upstream:
            collector = Collector([SourceConfig(deviceId="ACTIVE-01", baseUrl="http://127.0.0.1:19403", transport=transport)],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=upstream, archive=archive, utc_now=lambda: now)
            api = client(collector)
            try:
                before = api.get("/v1/health?includeSourceContext=true").json()["sourceContext"]
                await collector.poll_once(0)
                response = api.get("/v1/health?includeSourceContext=true")
                assert response.json()["status"] == "OK"
                assert response.json()["mock"] is True
                assert response.json()["sourceContext"] == before == context([
                    row("ACTIVE-01", "SIMULATOR", method, "READ_ONLY", "CONTRACT_DECLARED", "PRODUCER_REQUIRED", "PRODUCER_REQUIRED")])
                assert collector.observations("ACTIVE-01")[0].observedAtUtc == now
            finally:
                await collector.stop()
    asyncio.run(check())


def test_opt_in_query_is_typed_and_documented_without_new_routes():
    app = create_app(TOKEN)
    parameters = app.openapi()["paths"]["/v1/health"]["get"].get("parameters", [])
    query = [p for p in parameters if p["name"] == "includeSourceContext"]
    assert len(query) == 1 and query[0]["in"] == "query" and not query[0]["required"]
    assert query[0]["schema"]["type"] == "boolean" and query[0]["schema"]["default"] is False
    response = client().get("/v1/health?includeSourceContext=not-a-boolean")
    assert response.status_code == 422
    assert "sourceContext" not in response.json()


@pytest.mark.parametrize("headers,status", [
    ({"X-Aegis-Token": "wrong"}, 401),
    ({"Origin": "https://invalid.example"}, 403),
    ({"Host": "invalid.example"}, 400),
])
def test_opt_in_keeps_existing_auth_host_and_origin_boundaries(headers, status):
    response = client().get("/v1/health?includeSourceContext=true", headers=headers)
    assert response.status_code == status and "sourceContext" not in response.json()
