import json
from uuid import UUID

import pytest

from aegis.observability import make_event, write_event


def test_jsonl_preserves_trace_and_only_allowed_context(tmp_path):
    path = tmp_path / "events.jsonl"
    event = make_event(
        "EWMA_WARNING_RAISED", "ml_worker", "trace-demo-02",
        warningId="warn-demo-02", severity="WARN",
        safeContext={"signalKeys": ["actualFlowLpm"], "quality": "GOOD"},
    )
    write_event(path, event)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["traceId"] == "trace-demo-02"
    assert saved["warningId"] == "warn-demo-02"
    assert saved["schemaVersion"] == "3.4.0"
    assert saved["safeContext"] == {"signalKeys": ["actualFlowLpm"], "quality": "GOOD"}
    assert saved["occurredAtUtc"].endswith(("Z", "+00:00"))
    UUID(saved["eventId"])


def test_secrets_and_raw_sensor_rows_never_reach_final_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    event = make_event("API_REQUEST_FAILED", "local_api", "trace-demo-02")
    event.update({
        "password": "CANARY_PASSWORD", "authorization": "Bearer CANARY_TOKEN",
        "raw_payload": [{"value": 123.4}], "full_email": "alice@example.com",
        "reasonCode": "Bearer CANARY_REASON", "modelVersion": "C:\\private\\CANARY_MODEL",
        "safeContext": {
            "quality": "GOOD", "signalKeys": ["actualFlowLpm", "CANARY_SIGNAL"],
            "password": "CANARY_NESTED", "nested": {"access_token": "CANARY_DEEP"},
            "raw_prompt": "CANARY_PROMPT", "private_path": "C:\\private\\CANARY_PATH",
            "source": "CANARY_SOURCE", "value": 123.4,
        },
    })
    write_event(path, event)
    raw = path.read_text(encoding="utf-8")
    assert "CANARY" not in raw
    assert "alice@example.com" not in raw
    assert "123.4" not in raw
    saved = json.loads(raw)
    assert saved["safeContext"] == {"quality": "GOOD", "signalKeys": ["actualFlowLpm"]}


@pytest.mark.parametrize("key,value", [
    ("eventName", "SENSOR_SAMPLE"), ("component", "untrusted"),
    ("traceId", "Bearer PRIVATE_TOKEN"), ("eventName", "COLLECTOR_STARTED\nforged"),
])
def test_invalid_required_fields_do_not_create_a_log(tmp_path, key, value):
    path = tmp_path / "events.jsonl"
    event = make_event("SERVICE_STARTED", "collector", "trace-demo-02")
    event[key] = value
    with pytest.raises(ValueError):
        write_event(path, event)
    assert not path.exists()


def test_writer_appends_valid_events_and_surfaces_storage_failure(tmp_path):
    path = tmp_path / "events.jsonl"
    event = make_event("SERVICE_STARTED", "collector", "trace-demo-02")
    write_event(path, event)
    write_event(path, event)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    with pytest.raises(OSError):
        write_event(tmp_path, event)
