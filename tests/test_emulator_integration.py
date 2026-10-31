"""Bounded real-process loopback checks, including emulator loss and restart."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_benchmark_rejects_unbounded_work_and_handles_small_latency_samples():
    from scripts.benchmark_emulators import latency_summary, validate_run

    for count, seconds in [(0, 10), (33, 10), (1, 0), (1, 61), (True, 10)]:
        with pytest.raises(ValueError):
            validate_run(count, seconds)
    assert latency_summary([1.0]) == {"samples": 1, "p50Ms": 1.0, "p95Ms": 1.0}
    assert latency_summary([4.0, 1.0, 3.0, 2.0])["p95Ms"] == 4.0


def test_real_emulators_disconnect_independently_and_accept_a_new_run(tmp_path):
    from scripts.benchmark_emulators import running_stack, wait_until

    with running_stack(2) as stack:
        client = stack.client
        ids = stack.device_ids
        assert client.get("/v1/health").json()["mode"] == "EMULATORS"
        assert {row["deviceId"] for row in client.get("/v1/devices").json()} == set(ids)
        assert client.get("/v1/health", headers={"X-Aegis-Token": "wrong"}).status_code == 401

        def ready():
            rows = client.get("/v1/connections").json()
            return rows if all(row["state"] == "CONNECTED" and row["lastSequence"] >= 4 for row in rows) else None

        before = wait_until(ready, stack.processes, seconds=15)
        initial = {}
        for device_id in ids:
            rows = client.get(f"/v1/devices/{device_id}/observations").json()
            assert len(rows) == len({row["signalKey"] for row in rows}) == 8
            assert all(row["deviceId"] == device_id and row["quality"] == "GOOD" for row in rows)
            assert all((datetime.now(timezone.utc) - datetime.fromisoformat(row["receivedAtUtc"])).total_seconds() < 3 for row in rows)
            assert client.get(f"/v1/devices/{device_id}/health").json()["modelLifecycle"] == "NO_MODEL"
            initial[device_id] = rows[0]["observationId"].split(":")[0]

        started = time.monotonic()
        stack.stop_emulator(0)
        survivor_count = before[1]["acceptedBatches"]

        def isolated():
            rows = client.get("/v1/connections").json()
            return rows if rows[0]["state"] in {"DISCONNECTED", "STALE"} and rows[1]["acceptedBatches"] > survivor_count else None

        offline = wait_until(isolated, stack.active_processes, seconds=8)
        stale_rows = client.get(f"/v1/devices/{ids[0]}/observations").json()
        assert len(stale_rows) == 8 and all(row["quality"] != "GOOD" for row in stale_rows)
        unavailable = client.get(f"/v1/devices/{ids[0]}/health").json()
        assert unavailable["modelLifecycle"] == "NO_MODEL"
        assert unavailable["observationEvaluation"] == "UNAVAILABLE"
        assert client.get(f"/v1/devices/{ids[1]}/health").json()["observationEvaluation"] == "INSUFFICIENT_DATA"
        assert all(row["quality"] == "GOOD" for row in client.get(f"/v1/devices/{ids[1]}/observations").json())
        assert client.get("/v1/warnings").json() == []
        assert client.get("/v1/warnings/warn-demo-02/timeline").status_code == 404

        stack.start_emulator(0)

        def recovered():
            rows = client.get("/v1/connections").json()
            observations = client.get(f"/v1/devices/{ids[0]}/observations").json()
            new_run = observations and observations[0]["observationId"].split(":")[0] != initial[ids[0]]
            return rows if rows[0]["state"] == "CONNECTED" and new_run else None

        after = wait_until(recovered, stack.active_processes, seconds=8)
        assert after[0]["acceptedBatches"] > offline[0]["acceptedBatches"]
        assert after[0]["lastSequence"] < before[0]["lastSequence"]
        assert after[0]["rejectedBatches"] == after[1]["rejectedBatches"] == 0
        assert after[1]["acceptedBatches"] >= offline[1]["acceptedBatches"]
        assert all(row["quality"] == "GOOD" for row in client.get(f"/v1/devices/{ids[0]}/observations").json())
        assert client.get("/v1/warnings").json() == []
        evidence = {"scope": "Real separate Python emulator and API processes on IPv4 loopback; synthetic signals only.",
                    "deviceIds": ids, "beforeStop": before, "whileStopped": offline, "afterRestart": after,
                    "stopThroughRecoveryElapsedSeconds": round(time.monotonic() - started, 3),
                    "verified": ["two fresh eight-signal sources", "unavailable source has no GOOD readings",
                                 "other source continues", "new run accepts sequence reset", "no fixture warnings"],
                    "ownedChildrenCleanedUp": False}
    assert all(process.poll() is not None for process in stack.processes)
    evidence["ownedChildrenCleanedUp"] = True
    path = Path(__file__).resolve().parents[1] / "docs/evidence/emulator-integration.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def test_real_faulty_values_produce_distinct_actions_while_another_device_stays_valid():
    from scripts.benchmark_emulators import running_stack, wait_until

    seen = {}
    with running_stack(2, evaluate=True, scenarios=["DATA-QUALITY", "NORMAL"]) as stack:
        first, second = stack.device_ids
        def all_fault_phases():
            diagnostics = stack.client.get(f"/v1/devices/{first}/diagnostics").json()
            health = stack.client.get(f"/v1/devices/{first}/health").json()
            for issue in diagnostics["issues"]:
                if issue["signalKey"] == "actualFlowLpm" and issue["reasonCode"] in {"MISSING", "BAD", "OUT_OF_RANGE"}:
                    seen[issue["reasonCode"]] = {"diagnostics": diagnostics, "health": health}
                    assert diagnostics["evaluation"] != "NORMAL"
                    assert health["dataQuality"] != "GOOD"
                    assert issue["message"]
            other = stack.client.get(f"/v1/devices/{second}/health").json()
            assert other["dataQuality"] == "GOOD"
            assert stack.client.get("/v1/warnings").json() == []
            return seen if set(seen) == {"MISSING", "BAD", "OUT_OF_RANGE"} else None

        wait_until(lambda: all(row["state"] == "CONNECTED" for row in stack.client.get("/v1/connections").json()),
                   stack.processes)
        wait_until(all_fault_phases, stack.processes, seconds=58)
        raw = stack.client.get(f"/v1/devices/{first}/observations").json()
        invalid = next(row for row in raw if row["signalKey"] == "actualFlowLpm")
        assert invalid["value"] == -5 and invalid["quality"] == "GOOD"
        assert seen["OUT_OF_RANGE"]["health"]["dataQuality"] == "BAD"
        evidence = {"scope": "Real separate processes; 60-second quality scenario, normal peer.",
                    "observedPhases": seen, "rawOutOfRange": invalid, "normalPeer": second,
                    "noMlWarningFromInvalidInput": True}
    evidence["ownedChildrenCleanedUp"] = all(process.poll() is not None for process in stack.processes)
    path = Path(__file__).resolve().parents[1] / "docs/evidence/emulator-fault-response.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
