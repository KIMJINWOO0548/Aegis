import json
import subprocess
import sys

import pytest


def test_python_heartbeat_emits_correlated_lifecycle_and_exits():
    trace = "ac901964-a7a3-453c-9941-4641655b6d30"
    result = subprocess.run([
        sys.executable, "-m", "aegis.heartbeat", "--count", "3", "--interval-ms", "1",
        "--trace-id", trace,
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["eventName"] for event in events] == ["SERVICE_STARTED", "SERVICE_STOPPED"]
    assert [event["sourceSequence"] for event in events] == [1, 2]
    assert all(event["traceId"] == trace for event in events)
    assert all(event["schemaVersion"] == "3.4.0" for event in events)
    assert events[-1]["safeContext"]["heartbeatCount"] == 3
    assert events[-1]["durationMs"] >= 0


@pytest.mark.parametrize("args", [
    ["--count", "0"], ["--count", "1001"], ["--interval-ms", "-1"],
    ["--trace-id", "Bearer-SENSITIVE_CANARY"],
])
def test_python_heartbeat_rejects_unbounded_or_unsafe_options(args):
    result = subprocess.run([sys.executable, "-m", "aegis.heartbeat", *args],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "SENSITIVE_CANARY" not in result.stderr
