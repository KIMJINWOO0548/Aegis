import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from aegis.api import create_app
from aegis.collector import Collector, SourceConfig, SIGNAL_UNITS


TOKEN = "fault-response-test-" + "x" * 32


def test_faulty_quality_and_semantically_bad_values_generate_actions_not_normal(tmp_path):
    async def check():
        now = [datetime.now(timezone.utc)]
        payload = {"protocolVersion": "1.0", "source": "SIMULATOR", "deviceId": "EQ-A",
            "runId": str(uuid4()), "sequence": 0, "observedAtUtc": now[0].isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"} for key, unit in SIGNAL_UNITS.items()]}
        payload["signals"][-1]["value"] = 100
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as upstream:
            collector = Collector([SourceConfig(deviceId="EQ-A", baseUrl="http://127.0.0.1:9101")],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=upstream, evaluate=True, utc_now=lambda: now[0],
                log_path=tmp_path / "quality.jsonl")
            client = TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                                headers={"X-Aegis-Token": TOKEN})
            for index, (quality, value, reason) in enumerate([
                ("MISSING", None, "MISSING"), ("BAD", None, "BAD"), ("GOOD", -5, "OUT_OF_RANGE")]):
                payload.update(sequence=index, observedAtUtc=now[0].isoformat())
                payload["signals"][1].update(quality=quality, value=value)
                await collector.poll_once(0)
                diagnostics = client.get("/v1/devices/EQ-A/diagnostics").json()
                assert diagnostics["enabled"] is True
                assert diagnostics["evaluation"] == "INSUFFICIENT_DATA"
                assert any(issue["signalKey"] == "actualFlowLpm" and issue["reasonCode"] == reason
                           and issue["message"] for issue in diagnostics["issues"])
                health = client.get("/v1/devices/EQ-A/health").json()
                assert health["observationEvaluation"] != "NORMAL"
                assert health["dataQuality"] != "GOOD"
                assert client.get("/v1/warnings").json() == []
                now[0] += timedelta(seconds=1)
            payload.update(sequence=3, observedAtUtc=now[0].isoformat())
            payload["signals"][1].update(quality="GOOD", value=10)
            await collector.poll_once(0)
            events = [json.loads(line) for line in (tmp_path / "quality.jsonl").read_text().splitlines()]
            assert [event["eventName"] for event in events] == ["SIGNAL_QUALITY_CHANGED"] * 4
            assert [event["reasonCode"] for event in events] == ["MISSING", "BAD", "OUT_OF_RANGE", "QUALITY_RECOVERED"]
            assert TOKEN not in (tmp_path / "quality.jsonl").read_text()
    asyncio.run(check())


def test_connection_only_mode_has_explicit_disabled_diagnostics():
    collector = Collector([SourceConfig(deviceId="EQ-A", baseUrl="http://127.0.0.1:9101")],
                          {"AEGIS_EMULATOR_TOKEN": TOKEN})
    client = TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                        headers={"X-Aegis-Token": TOKEN})
    response = client.get("/v1/devices/EQ-A/diagnostics")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["modelVersion"] is None
    assert response.json()["issues"] == []


def test_degradation_creates_stable_warning_and_timeline_then_recovery_resolves(tmp_path):
    async def check():
        at = [datetime.now(timezone.utc)]
        payload = {"protocolVersion": "1.0", "source": "SIMULATOR", "deviceId": "EQ-A",
            "runId": str(uuid4()), "sequence": 0, "observedAtUtc": at[0].isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"} for key, unit in SIGNAL_UNITS.items()]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as upstream:
            collector = Collector([SourceConfig(deviceId="EQ-A", baseUrl="http://127.0.0.1:9101")],
                {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=upstream, evaluate=True, utc_now=lambda: at[0],
                log_path=tmp_path / "warnings.jsonl")
            client = TestClient(create_app(TOKEN, collector=collector), base_url="http://127.0.0.1",
                                headers={"X-Aegis-Token": TOKEN})
            for second in range(120):
                payload.update(sequence=second, observedAtUtc=at[0].isoformat())
                payload["signals"][1]["value"] = 8
                await collector.poll_once(0)
                at[0] += timedelta(seconds=1)
            warnings = client.get("/v1/warnings").json()
            assert len(warnings) == 1 and warnings[0]["state"] == "OPEN"
            warning_id = warnings[0]["id"]
            assert client.get("/v1/devices/EQ-A/diagnostics").json()["evaluation"] == "ANOMALOUS"
            timeline = client.get(f"/v1/warnings/{warning_id}/timeline").json()
            assert timeline["events"][0]["eventName"] == "EWMA_WARNING_RAISED"
            for second in range(120, 500):
                payload.update(sequence=second, observedAtUtc=at[0].isoformat())
                payload["signals"][1]["value"] = 10
                await collector.poll_once(0)
                at[0] += timedelta(seconds=1)
            assert client.get("/v1/warnings").json()[0]["id"] == warning_id
            assert client.get("/v1/warnings").json()[0]["state"] == "RESOLVED"
            timeline = client.get(f"/v1/warnings/{warning_id}/timeline").json()
            assert timeline["events"][-1]["eventName"] == "ADVISORY_RECOVERY_CONFIRMED"
            raw = (tmp_path / "warnings.jsonl").read_text()
            events = [json.loads(line) for line in raw.splitlines()]
            assert [event["eventName"] for event in events] == ["EWMA_WARNING_RAISED", "ADVISORY_RECOVERY_CONFIRMED"]
            assert all(event["warningId"] == warning_id for event in events)
            assert all(not event["safeContext"] for event in events)
            assert TOKEN not in raw
            assert client.post(f"/v1/warnings/{warning_id}/ack").status_code == 404
    asyncio.run(check())
