"""D0a JSONL events: trusted identifiers and an explicit metadata allowlist."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .contracts import (
    EVENT_OPTIONAL_FIELDS, EVENT_REQUIRED_FIELDS, VERSION,
    ObservationQuality, SignalKey, Source, StructuredEvent,
)


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
IDENTIFIER_FIELDS = {
    "eventId", "traceId", "deviceId", "warningId", "incidentId", "requestId",
    "runId", "modelVersion", "sourceVersion", "configRevision", "aggregationVersion",
}


def make_event(event_name: str, component: str, trace_id: str, **fields) -> dict:
    return {
        "schemaVersion": VERSION, "eventId": str(uuid4()),
        "occurredAtUtc": datetime.now(timezone.utc), "component": component,
        "eventName": event_name, "severity": "INFO", "traceId": trace_id,
        "outcome": "SUCCESS", **fields,
    }


def _safe_context(context: dict) -> dict:
    # Unknown/nested context is discarded, so raw payloads never enter diagnostics.
    safe = {}
    if isinstance(context.get("quality"), str) and context["quality"] in ObservationQuality:
        safe["quality"] = context["quality"]
    if isinstance(context.get("source"), str) and context["source"] in Source:
        safe["source"] = context["source"]
    signals = context.get("signalKeys")
    if isinstance(signals, list):
        safe["signalKeys"] = [value for value in signals if isinstance(value, str) and value in SignalKey]
    count = context.get("heartbeatCount")
    if type(count) is int and 0 <= count <= 1000:
        safe["heartbeatCount"] = count
    return safe


def write_event(path: Path, event: dict) -> None:
    allowed = set(EVENT_REQUIRED_FIELDS) | set(EVENT_OPTIONAL_FIELDS)
    cleaned = {key: value for key, value in event.items() if key in allowed}
    for key in IDENTIFIER_FIELDS & cleaned.keys():
        value = cleaned[key]
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            if key in EVENT_REQUIRED_FIELDS:
                raise ValueError(f"Invalid event identifier: {key}")
            cleaned.pop(key)
    reason = cleaned.get("reasonCode")
    if reason is not None and (not isinstance(reason, str) or not REASON_CODE.fullmatch(reason)):
        cleaned.pop("reasonCode")
    context = cleaned.pop("safeContext", None)
    if isinstance(context, dict):
        cleaned["safeContext"] = _safe_context(context)
    payload = StructuredEvent.model_validate(cleaned).model_dump(mode="json", exclude_none=True)
    line = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
