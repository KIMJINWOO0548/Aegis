import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from aegis.api import create_app
from aegis.collector import Collector, SIGNAL_UNITS, SourceConfig


TOKEN = "test-live-token-" + "x" * 32


def test_live_api_never_returns_fixture_observations_or_warnings():
    now = datetime.now(timezone.utc).isoformat()
    payload = {"protocolVersion": "1.0", "source": "SIMULATOR", "deviceId": "EQ-SUPPLY-02",
        "runId": str(uuid4()), "sequence": 10, "observedAtUtc": now, "deviceStateVersion": 1,
        "modeId": "PRODUCTION", "signals": [{"signalKey": k, "value": 25.0, "unit": v, "quality": "GOOD"}
                                              for k, v in SIGNAL_UNITS.items()]}
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as upstream:
            collector = Collector([SourceConfig(deviceId="EQ-SUPPLY-02", baseUrl="http://127.0.0.1:9102")],
                                  {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=upstream)
            client = TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                                headers={"X-Aegis-Token": TOKEN})
            assert client.get("/v1/health").json()["mode"] == "EMULATORS"
            assert client.get("/v1/devices").json() == [{"deviceId": "EQ-SUPPLY-02", "readOnly": True, "source": "SIMULATOR"}]
            assert client.get("/v1/devices/EQ-SUPPLY-02/observations").json() == []
            assert client.get("/v1/devices/EQ-SUPPLY-02/health").json()["observationEvaluation"] == "UNAVAILABLE"
            await collector.poll_once(0)
            rows = client.get("/v1/devices/EQ-SUPPLY-02/observations").json()
            assert len(rows) == 8 and all(row["value"] == 25.0 for row in rows)
            assert client.get("/v1/connections").json()[0]["state"] == "CONNECTED"
            assert client.get("/v1/devices/EQ-SUPPLY-01/observations").status_code == 404
            assert client.get("/v1/warnings").json() == []
            assert client.get("/v1/warnings/warn-demo-02/timeline").status_code == 404
            assert client.get("/v1/devices/EQ-SUPPLY-02/trends", params={
                "signals": "actualFlowLpm", "fromUtc": now, "toUtc": "2099-01-01T00:00:00Z"}).status_code == 501
            assert client.post("/v1/lab/scenarios/DEG-02/start").status_code == 501
    asyncio.run(check())


def test_fixture_mode_connections_are_empty_and_mode_is_explicit():
    client = TestClient(create_app(TOKEN), base_url="http://127.0.0.1", headers={"X-Aegis-Token": TOKEN})
    assert client.get("/v1/health").json()["mode"] == "FIXTURE"
    assert client.get("/v1/connections").json() == []
