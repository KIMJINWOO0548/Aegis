import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from aegis.collector import Collector, SourceConfig, load_sources


TOKEN = "emulator-test-token-" + "x" * 32
NOW = datetime.now(timezone.utc)
UNITS = {
    "setpointFlowLpm": "L/min", "actualFlowLpm": "L/min", "supplyPressureKpa": "kPa",
    "valveCommandPct": "%", "valveFeedbackPct": "%", "motorCurrentAmp": "A",
    "temperatureC": "degC", "cycleCount": "count",
}


def snapshot(device="EQ-SUPPLY-01", sequence=0, run=None, at=NOW):
    return {"protocolVersion": "1.0", "source": "SIMULATOR", "deviceId": device,
            "runId": run or str(uuid4()), "sequence": sequence, "observedAtUtc": at.isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": key, "value": 10.0, "unit": unit, "quality": "GOOD"}
                        for key, unit in UNITS.items()]}


def source(device="EQ-SUPPLY-01", port=9101):
    return SourceConfig(deviceId=device, baseUrl=f"http://127.0.0.1:{port}")


def run(coro):
    return asyncio.run(coro)


def test_accepts_only_valid_atomic_batch_and_collector_stamps_receipt():
    async def check():
        payload = snapshot()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client, utc_now=lambda: NOW)
        await collector.poll_once(0)
        rows = collector.observations("EQ-SUPPLY-01")
        assert len(rows) == 8
        assert all(row.receivedAtUtc == NOW and row.quality.value == "GOOD" for row in rows)
        assert collector.connections()[0]["acceptedBatches"] == 1
        assert collector.device_health("EQ-SUPPLY-01").modelLifecycle.value == "NO_MODEL"
        assert collector.device_health("EQ-SUPPLY-01").observationEvaluation.value == "INSUFFICIENT_DATA"
        await client.aclose()
    run(check())


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(deviceId="WRONG"),
    lambda p: p.update(protocolVersion="2.0"),
    lambda p: p.update(source="PHYSICAL_DEVICE"),
    lambda p: p.update(sequence=True),
    lambda p: p.update(expectedAlarmAt="private-truth"),
    lambda p: p["signals"].pop(),
    lambda p: p["signals"][0].update(value=None),
    lambda p: p["signals"][0].update(value=True),
    lambda p: p["signals"][0].update(unit="wrong"),
    lambda p: p["signals"].__setitem__(0, p["signals"][1]),
])
def test_rejects_invalid_batch_without_partial_publish(mutation):
    async def check():
        payload = snapshot()
        mutation(payload)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client, utc_now=lambda: NOW)
        await collector.poll_once(0)
        assert collector.observations("EQ-SUPPLY-01") == []
        assert collector.connections()[0]["state"] == "REJECTED"
        assert collector.connections()[0]["rejectedBatches"] == 1
        await client.aclose()
    run(check())


def test_duplicate_goes_stale_and_restart_and_sequence_gap_recover():
    async def check():
        clock = [0.0]
        payload = snapshot()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client,
                              utc_now=lambda: NOW, monotonic=lambda: clock[0])
        await collector.poll_once(0)
        clock[0] = 4
        await collector.poll_once(0)
        assert collector.connections()[0]["state"] == "STALE"
        assert collector.connections()[0]["acceptedBatches"] == 1
        assert all(row.quality.value == "STALE" for row in collector.observations("EQ-SUPPLY-01"))
        assert collector.device_health("EQ-SUPPLY-01").observationEvaluation.value == "UNAVAILABLE"
        payload.update(sequence=3, observedAtUtc=(NOW + timedelta(seconds=1)).isoformat())
        await collector.poll_once(0)
        assert collector.connections()[0]["droppedBatches"] == 2
        assert collector.connections()[0]["state"] == "CONNECTED"
        old_run = payload["runId"]
        payload.update(sequence=0, runId=str(uuid4()))
        await collector.poll_once(0)
        assert collector.connections()[0]["acceptedBatches"] == 3
        payload.update(sequence=4, runId=old_run)
        await collector.poll_once(0)
        assert collector.connections()[0]["state"] == "REJECTED"
        assert collector.connections()[0]["lastSequence"] == 0
        await client.aclose()
    run(check())


def test_failed_source_does_not_block_another_or_keep_good_quality():
    async def check():
        failed = [False]
        async def transport(request):
            assert request.headers["X-Aegis-Emulator-Token"] == TOKEN
            if request.url.port == 9101 and failed[0]:
                await asyncio.sleep(.04)
                raise httpx.ReadTimeout("test-only", request=request)
            return httpx.Response(200, json=snapshot("EQ-SUPPLY-01" if request.url.port == 9101 else "EQ-SUPPLY-02"))
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        collector = Collector([source(), source("EQ-SUPPLY-02", 9102)], {"AEGIS_EMULATOR_TOKEN": TOKEN},
                              client=client, utc_now=lambda: NOW)
        await collector.poll_once(0)
        failed[0] = True
        slow = asyncio.create_task(collector.poll_once(0))
        await collector.poll_once(1)
        assert not slow.done()
        assert collector.connections()[1]["state"] == "CONNECTED"
        await slow
        assert collector.connections()[0]["state"] == "DISCONNECTED"
        assert all(row.quality.value == "STALE" for row in collector.observations("EQ-SUPPLY-01"))
        assert collector.device_health("EQ-SUPPLY-01").dataQuality.value == "STALE"
        await client.aclose()
    run(check())


def test_large_body_redirect_and_unfresh_timestamp_are_rejected():
    async def check():
        responses = [httpx.Response(200, content=b" " * 65537),
                     httpx.Response(302, headers={"Location": "https://example.invalid"}),
                     httpx.Response(200, json=snapshot(at=NOW - timedelta(hours=1)))]
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0)))
        collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client, utc_now=lambda: NOW)
        for _ in range(3):
            await collector.poll_once(0)
            assert collector.connections()[0]["state"] in {"REJECTED", "DISCONNECTED"}
            assert collector.observations("EQ-SUPPLY-01") == []
        await client.aclose()
    run(check())


@pytest.mark.parametrize("url", ["http://example.com:9101", "http://localhost:9101", "https://127.0.0.1:9101",
                                 "http://user:secret@127.0.0.1:9101", "http://127.0.0.1:9101/path",
                                 "http://127.0.0.1:9101?token=secret", "http://127.0.0.1:9101#fragment"])
def test_sources_are_strict_literal_loopback_origins(url):
    with pytest.raises(ValueError):
        SourceConfig(deviceId="EQ-SUPPLY-01", baseUrl=url)


def test_config_bounds_duplicates_and_secrets_fail_at_startup(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [source().model_dump()]}), encoding="utf-8")
    assert len(load_sources(path)) == 1
    with pytest.raises(ValueError):
        Collector(load_sources(path), {})
    for sources in [[], [source().model_dump()] * 2, [source().model_dump()] * 33]:
        path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_sources(path)


def test_whole_response_deadline_stops_a_source_that_keeps_trickling_bytes():
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(.01)
                yield b" "

    async def check():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowBody())))
        collector = Collector([source()], {"AEGIS_EMULATOR_TOKEN": TOKEN}, client=client, timeout=.04)
        started = asyncio.get_running_loop().time()
        await collector.poll_once(0)
        assert asyncio.get_running_loop().time() - started < .3
        assert collector.connections()[0]["state"] == "DISCONNECTED"
        assert collector.connections()[0]["reasonCode"] == "POLL_TIMEOUT"
        await client.aclose()
    run(check())
