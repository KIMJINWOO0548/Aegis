"""Bounded, read-only raw1.1 projections; never reuse the ingestion writer."""

import json
import re
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from aegis.archive import COLUMNS
from aegis.collector import RawSnapshot
from aegis.contracts import UtcTimestamp


MAX_CANDIDATE_BATCHES = 1024
MAX_MATCHING_BATCHES = 64
MAX_SIGNALS = 16
MAX_CATALOG_TAGS = 512
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_QUERY_BYTES = 8 * 1024 * 1024
MAX_RUN_GAPS = 1024
MAX_SQL_STEPS = 1_000_000
MAX_QUERY_SECONDS = 1.0
SQL_PROGRESS_INTERVAL = 1000
TIMESTAMP = TypeAdapter(UtcTimestamp)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SIGNAL_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
RUN_KEY = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class RawQueryError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _invalid():
    return RawQueryError(409, "RAW_ARCHIVE_INVALID", "The raw archive failed integrity validation.")


def _limit():
    return RawQueryError(413, "RAW_QUERY_LIMIT", "The raw query exceeds a supported resource limit.")


def _unavailable():
    return RawQueryError(503, "RAW_ARCHIVE_UNAVAILABLE", "The configured raw archive cannot be read.")


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid timestamp.")
    return TIMESTAMP.validate_python(value)


def _query(device, run, start, end, signals, max_batches):
    try:
        if (not isinstance(device, str) or not IDENTIFIER.fullmatch(device)
                or not isinstance(run, str) or not RUN_KEY.fullmatch(run)
                or type(max_batches) is not int or not 1 <= max_batches <= MAX_MATCHING_BATCHES):
            raise ValueError
        UUID(run)
        start, end = _timestamp(start), _timestamp(end)
        if not timedelta(0) < end - start <= timedelta(days=1):
            raise ValueError
        selected = None
        if signals is not None:
            if not isinstance(signals, str) or len(signals) > MAX_SIGNALS * 129 - 1:
                raise ValueError
            selected = signals.split(",")
            if (not 1 <= len(selected) <= MAX_SIGNALS or len(set(selected)) != len(selected)
                    or any(not SIGNAL_KEY.fullmatch(key) for key in selected)):
                raise ValueError
        return start, end, selected
    except (ValueError, TypeError, OverflowError):
        raise RawQueryError(422, "INVALID_QUERY", "Request parameters are invalid.") from None


def _schema(connection):
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise _invalid()
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 4").fetchall()
    if {row[0] for row in tables} != set(COLUMNS):
        raise _invalid()
    for name, columns in COLUMNS.items():
        fields = connection.execute(f"PRAGMA table_info({name})").fetchall()
        keys = tuple(row[1] for row in sorted(fields, key=lambda row: row[5]) if row[5])
        expected = columns[:3] if name == "raw_batches" else (("device_id",) if name == "ingest_cursors" else ())
        if tuple(row[1] for row in fields) != columns or keys != expected:
            raise _invalid()
    unique_gap = any(tuple(row[0] for row in connection.execute(
        "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index[1],))) == COLUMNS["ingest_gaps"][:4]
        for index in connection.execute("PRAGMA index_list(ingest_gaps)") if index[2] and not index[4])
    if not unique_gap:
        raise _invalid()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Non-finite JSON value.")


def _batch(row, device, run):
    try:
        stored_device, stored_run, sequence, observed, receipt, equipment, module, encoded = row
        if (stored_device != device or stored_run != run or type(sequence) is not int
                or not isinstance(encoded, str)
                or any(value is not None and (not isinstance(value, str) or len(value) > 128)
                       for value in (equipment, module))):
            raise ValueError
        original = json.loads(encoded, object_pairs_hook=_object, parse_constant=_constant)
        parsed = RawSnapshot.model_validate(original)
        if (parsed.deviceId != device or parsed.runId != UUID(run) or parsed.sequence != sequence
                or parsed.observedAtUtc != _timestamp(observed)):
            raise ValueError
        _timestamp(receipt)
        return parsed.observedAtUtc, original, receipt, equipment, module
    except (ValueError, TypeError, OverflowError, KeyError, RecursionError):
        raise _invalid() from None


def _read(connection, device, run, start, end, selected, max_batches, check_budget):
    _schema(connection)
    candidates = connection.execute("""SELECT sequence,length(CAST(payload_json AS BLOB))
        FROM raw_batches WHERE device_id=? AND run_id=? ORDER BY sequence LIMIT ?""",
        (device, run, MAX_CANDIDATE_BATCHES + 1)).fetchall()
    if len(candidates) > MAX_CANDIDATE_BATCHES:
        raise _limit()
    if any(type(size) is not int or size < 0 for _, size in candidates):
        raise _invalid()
    if (any(size > MAX_PAYLOAD_BYTES for _, size in candidates)
            or sum(size for _, size in candidates) > MAX_QUERY_BYTES):
        raise _limit()
    matches = []
    for sequence, _size in candidates:
        check_budget()
        row = connection.execute("""SELECT device_id,run_id,sequence,observed_at_utc,received_at_utc,
            equipment_id,module_id,payload_json FROM raw_batches
            WHERE device_id=? AND run_id=? AND sequence=?""", (device, run, sequence)).fetchone()
        observed, original, receipt, equipment, module = _batch(row, device, run)
        if start <= observed < end:
            matches.append((observed, original, receipt, equipment, module))
            if len(matches) > max_batches:
                raise _limit()
    gaps = connection.execute("""SELECT first_sequence,last_sequence,reason FROM ingest_gaps
        WHERE device_id=? AND run_id=? ORDER BY first_sequence,last_sequence LIMIT ?""",
        (device, run, MAX_RUN_GAPS + 1)).fetchall()
    if len(gaps) > MAX_RUN_GAPS:
        raise _limit()
    if any(type(first) is not int or type(last) is not int or not 0 <= first <= last
           or reason != "REPLAY_OVERFLOW" for first, last, reason in gaps):
        raise _invalid()
    matches.sort(key=lambda row: (row[0], row[1]["sequence"]))
    tags = {key: {"signalKey": key, "units": set(), "presentBatches": 0, "qualities": {}}
            for key in (selected or [])}
    batches = []
    for _observed, original, receipt, equipment, module in matches:
        check_budget()
        readings = {row["signalKey"]: row for row in original["signals"]}
        for key in (selected if selected is not None else readings):
            if key not in tags:
                if len(tags) >= MAX_CATALOG_TAGS:
                    raise _limit()
                tags[key] = {"signalKey": key, "units": set(), "presentBatches": 0, "qualities": {}}
            if key in readings:
                item, value = tags[key], readings[key]
                item["units"].add(value["unit"])
                item["presentBatches"] += 1
                item["qualities"][value["quality"]] = item["qualities"].get(value["quality"], 0) + 1
        if selected is not None:
            batch = {key: value for key, value in original.items() if key != "signals"}
            batch.update(receivedAtUtc=receipt, equipmentId=equipment, moduleId=module,
                signals=[{"signalKey": key, "present": key in readings,
                    "reading": {k: v for k, v in readings[key].items() if k != "signalKey"}
                    if key in readings else None} for key in selected])
            batches.append(batch)
    result = {"schemaVersion": "raw-query-1",
        "kind": "RAW_ARCHIVE_HISTORY" if selected is not None else "RAW_ARCHIVE_OBSERVED_TAGS",
        "deviceId": device, "runId": run,
        "window": {"fromUtc": start.isoformat(), "toUtc": end.isoformat(), "bounds": "[from,to)"},
        "selectionComplete": True, "sourceCoverage": "NOT_ASSERTED",
        "scannedBatches": len(candidates), "matchedBatches": len(matches),
        "sequenceGaps": {"scope": "SELECTED_RUN_SEQUENCE_ONLY", "items": [
            {"firstSequence": first, "lastSequence": last, "reason": reason} for first, last, reason in gaps]},
        "tags": [dict(item, units=sorted(item["units"]), absentBatches=len(matches) - item["presentBatches"])
                 for key, item in sorted(tags.items())],
        "limits": {"candidateBatches": MAX_CANDIDATE_BATCHES, "matchingBatches": max_batches,
            "signals": MAX_SIGNALS, "catalogTags": MAX_CATALOG_TAGS, "payloadBytes": MAX_PAYLOAD_BYTES,
            "queryBytes": MAX_QUERY_BYTES, "runGaps": MAX_RUN_GAPS,
            "sqlSteps": MAX_SQL_STEPS, "sqlSeconds": MAX_QUERY_SECONDS}}
    if selected is not None:
        result["batches"] = batches
    return result


def read_raw_archive(path, device_id, run_id, from_utc, to_utc, *, signals=None, max_batches=64):
    start, end, selected = _query(device_id, run_id, from_utc, to_utc, signals, max_batches)
    connection = None
    deadline, steps, interrupted = time.monotonic() + MAX_QUERY_SECONDS, 0, False

    def check_budget():
        if time.monotonic() >= deadline:
            raise _limit()

    def progress():
        nonlocal steps, interrupted
        steps += SQL_PROGRESS_INTERVAL
        interrupted = steps > MAX_SQL_STEPS or time.monotonic() >= deadline
        return int(interrupted)

    try:
        path = Path(path).resolve()
        if not path.is_file():
            raise _unavailable()
        check_budget()
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.2, isolation_level=None)
        connection.set_progress_handler(progress, SQL_PROGRESS_INTERVAL)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        result = _read(connection, device_id, run_id, start, end, selected, max_batches, check_budget)
        check_budget()
        return result
    except RawQueryError:
        raise
    except sqlite3.Error:
        raise (_limit() if interrupted else _unavailable()) from None
    except OSError:
        raise _unavailable() from None
    finally:
        if connection is not None:
            connection.close()
