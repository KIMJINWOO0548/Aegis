"""Actual stored-record query CLIs preserve native evidence and fail without partial output."""

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aegis.adapter_input import SyntheticReplayAdapter
from aegis.adapter_store import AdapterStore
from aegis.archive import RawArchive


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
STREAM = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
RAW_DEVICE = "RAW-SYNTHETIC-01"


@pytest.fixture
def sources(tmp_path):
    fixture = json.loads((ROOT / "examples/adapter-input.synthetic.json").read_text(encoding="utf-8"))
    adapter = SyntheticReplayAdapter(fixture)
    adapter_path = tmp_path / "private-adapter-저장소-marker.sqlite"
    with AdapterStore(adapter_path, adapter) as store:
        store.commit_page(json.dumps(fixture["pages"][0]["response"]).encode(), AT)
    adapter_spec = {"storeId": "adapter", "kind": "ADAPTER_STORE_V1", "path": str(adapter_path),
                    "expectedFixtureSha256": hashlib.sha256(adapter.fixture_wire).hexdigest()}
    raw_path = tmp_path / "private-raw-store-marker.sqlite"
    raw_sample = {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": RAW_DEVICE,
                  "runId": RUN, "sequence": 7, "observedAtUtc": "2025-01-01T00:00:00Z",
                  "deviceStateVersion": 1, "modeId": "PRODUCTION",
                  "signals": [{"signalKey": "raw.numeric", "value": -3.25,
                               "unit": "count", "quality": "GOOD"}]}

    async def seed_raw():
        archive = RawArchive(raw_path)
        await archive.start()
        try:
            await archive.append([raw_sample], AT, gap=(0, 6))
        finally:
            await archive.close()

    asyncio.run(seed_raw())
    connection = sqlite3.connect(raw_path)
    try:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    finally:
        connection.close()
    return {"adapter": adapter_spec, "raw": {"storeId": "raw", "kind": "RAW_V1", "path": str(raw_path)},
            "fixture": fixture, "rawSample": raw_sample}


def config(tmp_path, specs):
    path = tmp_path / "private-조회-설정-marker.json"
    path.write_text(json.dumps({"schemaVersion": "stored-record-sources-1", "sources": specs},
                               ensure_ascii=False), encoding="utf-8")
    return path


def arguments(path, *, start=AT, end=AT + timedelta(seconds=1), extra=()):
    def text(value):
        return value.isoformat() if isinstance(value, datetime) else value
    return [sys.executable, "-m", "aegis.stored_records", "--sources", str(path),
            "--received-from", text(start), "--received-to", text(end), *extra]


def environment():
    return {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def process(path, *, start=AT, end=AT + timedelta(seconds=1), extra=(), timeout=15):
    try:
        return subprocess.run(arguments(path, start=start, end=end, extra=extra), cwd=path.parent,
                              env=environment(), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail("Stored-record query blocked instead of completing within the test deadline.")


def assert_private(result, path, sources=None):
    output = (result.stdout or "") + (result.stderr or "")
    private = [str(path), path.name, "synthetic-token-a", "synthetic-token-b", "PRIVATE-AUTH-MARKER"]
    if sources is not None:
        for name in ("adapter", "raw"):
            source = Path(sources[name]["path"])
            private.extend([str(source), source.name])
    assert all(value not in output for value in private)
    assert "Traceback" not in output


def report(result, path, sources):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    body = json.loads(result.stdout)
    assert body["schemaVersion"] == "stored-record-query-1"
    assert body["timeBasis"] == "FIRST_RECEIPT"
    assert body["adapterToRawV1WriteDelivery"] == "NOT_CONNECTED"
    assert body["sourceAcknowledgement"] == "NOT_SENT"
    assert isinstance(body["records"], list)
    assert_private(result, path, sources)
    return body


def assert_error(result, path, sources=None):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str) and body["error"]
    assert_private(result, path, sources)


def source_bytes(sources):
    return {name: Path(sources[name]["path"]).read_bytes() for name in ("adapter", "raw")}


def test_cli_queries_both_native_stores_by_first_receipt_without_changing_them(tmp_path, sources):
    path = config(tmp_path, [sources["adapter"], sources["raw"]])
    before, config_bytes = source_bytes(sources), path.read_bytes()
    names = {item.name for item in tmp_path.iterdir()}
    body = report(process(path), path, sources)
    assert len(body["records"]) == 2
    records = {item["identity"]["storeId"]: item for item in body["records"]}
    assert set(records) == {"adapter", "raw"}
    for name, kind in (("adapter", "ADAPTER_STORE_V1"), ("raw", "RAW_V1")):
        item = records[name]
        identity = item["identity"]
        assert identity["storeKind"] == kind
        assert isinstance(identity["nativeKeyKind"], str) and identity["nativeKeyKind"]
        assert identity["nativeKey"] and isinstance(item["provenance"], dict)
        assert datetime.fromisoformat(item["firstReceivedAtUtc"].replace("Z", "+00:00")) == AT
    adapter = records["adapter"]
    original = sources["fixture"]["pages"][0]["response"]["records"][0]
    assert adapter["sourceMetadata"] == original["sourceMetadata"]
    assert original["recordId"] in json.dumps(adapter["identity"]["nativeKey"])
    assert STREAM in json.dumps(adapter["identity"]["nativeKey"])
    for field, expected in {"declaredOrigin": "SYNTHETIC", "originEvidence": "DECLARED_ONLY",
                            "acquisitionMethod": "SOURCE_BUFFER_REPLAY", "sourceAccessMode": "READ_ONLY"}.items():
        assert adapter["provenance"][field] == expected
    raw = records["raw"]
    assert RAW_DEVICE in json.dumps(raw["identity"]["nativeKey"]) and RUN in json.dumps(raw["identity"]["nativeKey"])
    assert raw["sourceMetadata"]["sequence"] == 7
    assert raw["sourceMetadata"]["sequenceStatus"] == "PROVIDED"
    assert raw["sourceMetadata"]["timestampStatus"] == "PROVIDED"
    assert source_bytes(sources) == before and path.read_bytes() == config_bytes
    # Ordinary read-only SQLite may manage WAL/SHM metadata; user files stay intact.
    companions = {Path(sources[name]["path"]).name + suffix
                  for name in ("adapter", "raw") for suffix in ("-wal", "-shm")}
    assert {item.name for item in tmp_path.iterdir()} - companions == names - companions


def test_cli_signal_projection_preserves_missing_quality_and_distinguishes_absent_tag(tmp_path, sources):
    path = config(tmp_path, [sources["adapter"]])
    body = report(process(path, extra=("--device", "SYNTHETIC-01", "--signals",
        "synthetic.temperature,synthetic.optional,synthetic.absent", "--max-records", "1")), path, sources)
    assert len(body["records"]) == 1
    signals = {item["signalKey"]: item for item in body["records"][0]["signals"]}
    assert set(signals) == {"synthetic.temperature", "synthetic.optional", "synthetic.absent"}
    assert signals["synthetic.temperature"] == {"signalKey": "synthetic.temperature", "present": True,
        "reading": {"value": -2.5, "unit": "degC", "quality": "GOOD"}}
    assert signals["synthetic.optional"] == {"signalKey": "synthetic.optional", "present": True,
        "reading": {"value": None, "unit": "count", "quality": "MISSING"}}
    assert signals["synthetic.absent"] == {"signalKey": "synthetic.absent", "present": False, "reading": None}


@pytest.mark.parametrize("start,end", [(AT - timedelta(seconds=1), AT),
                                      (AT + timedelta(seconds=1), AT + timedelta(seconds=2))])
def test_cli_receipt_window_excludes_records_outside_half_open_bounds(tmp_path, sources, start, end):
    path = config(tmp_path, [sources["adapter"], sources["raw"]])
    assert report(process(path, start=start, end=end), path, sources)["records"] == []


@pytest.mark.parametrize("fault", ["missing", "corrupt", "wrong_kind", "wrong_binding"])
def test_cli_any_failed_source_prevents_partial_stdout_and_does_not_repair_sources(tmp_path, sources, fault):
    adapter_spec = copy.deepcopy(sources["adapter"])
    adapter_path = Path(adapter_spec["path"])
    if fault == "missing":
        adapter_path.unlink()
    elif fault == "corrupt":
        adapter_path.write_bytes(b"synthetic corrupt adapter store")
    elif fault == "wrong_kind":
        adapter_spec["kind"] = "RAW_V1"
        del adapter_spec["expectedFixtureSha256"]
    else:
        adapter_spec["expectedFixtureSha256"] = "0" * 64
    # A healthy source precedes the failed one; no result may be streamed early.
    path = config(tmp_path, [sources["raw"], adapter_spec])
    raw_bytes = Path(sources["raw"]["path"]).read_bytes()
    adapter_bytes = adapter_path.read_bytes() if adapter_path.exists() else None
    assert_error(process(path), path, sources)
    assert Path(sources["raw"]["path"]).read_bytes() == raw_bytes
    assert (adapter_path.read_bytes() if adapter_path.exists() else None) == adapter_bytes


@pytest.mark.parametrize("fault", ["extra", "empty_sources", "duplicate_store_id", "unknown_kind", "raw_hash"])
def test_cli_invalid_source_configuration_has_only_private_structured_stderr(tmp_path, sources, fault):
    specs = [copy.deepcopy(sources["adapter"])]
    if fault == "empty_sources":
        specs = []
    elif fault == "duplicate_store_id":
        specs.append(copy.deepcopy(specs[0]))
    elif fault == "unknown_kind":
        specs[0]["kind"] = "UNKNOWN_KIND"
    elif fault == "raw_hash":
        specs = [{**sources["raw"], "expectedFixtureSha256": "0" * 64}]
    else:
        specs[0]["authentication"] = "PRIVATE-AUTH-MARKER"
    path = config(tmp_path, specs)
    before = source_bytes(sources)
    assert_error(process(path), path, sources)
    assert source_bytes(sources) == before


@pytest.mark.parametrize("start,end", [
    (AT, AT), (AT + timedelta(seconds=1), AT),
    ("2026-01-01T00:00:00", "2026-01-02T00:00:00Z"),
    ("0001-01-01T00:00:00+14:00", "0001-01-02T00:00:00Z"),
    ("9999-12-30T00:00:00Z", "9999-12-31T23:59:59-12:00"),
])
def test_cli_invalid_or_utc_overflow_window_never_emits_partial_json(tmp_path, sources, start, end):
    path = config(tmp_path, [sources["adapter"]])
    assert_error(process(path, start=start, end=end), path, sources)


@pytest.mark.parametrize("limit", ["0", "-1", "true", "not-an-int"])
def test_cli_invalid_record_limit_has_no_partial_report(tmp_path, sources, limit):
    path = config(tmp_path, [sources["adapter"]])
    assert_error(process(path, extra=("--max-records", limit)), path, sources)


@pytest.mark.parametrize("fault", ["duplicate", "escaped_duplicate", "nan", "oversize", "invalid_utf8"])
def test_cli_bounded_utf8_configuration_rejects_ambiguous_or_invalid_json(tmp_path, sources, fault):
    path = config(tmp_path, [sources["adapter"]])
    content = path.read_text(encoding="utf-8")
    if fault in ("duplicate", "escaped_duplicate"):
        key = "schemaVersion" if fault == "duplicate" else r"\u0073chemaVersion"
        path.write_text('{"' + key + '":"stored-record-sources-1",' + content[1:], encoding="utf-8")
    elif fault == "nan":
        data = json.loads(content)
        data["sources"][0]["expectedFixtureSha256"] = float("nan")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif fault == "oversize":
        original = path.read_bytes()
        path.write_bytes(original + b" " * (64 * 1024 + 1 - len(original)))
    else:
        path.write_bytes(b"\xff" + path.read_bytes())
    config_bytes, before = path.read_bytes(), source_bytes(sources)
    assert_error(process(path), path, sources)
    assert path.read_bytes() == config_bytes and source_bytes(sources) == before


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_cli_configuration_requires_regular_nofollow_file_and_never_blocks(tmp_path, sources, kind):
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures require POSIX.")
    target = config(tmp_path, [sources["adapter"]])
    path = tmp_path / "private-nonregular-config-marker.json"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to(target)
    before = source_bytes(sources)
    assert_error(process(path, timeout=2), path, sources)
    assert source_bytes(sources) == before
    assert path.is_symlink() if kind == "symlink" else path.exists()


def test_cli_closed_stdout_pipe_cannot_report_success_or_modify_sources(tmp_path, sources):
    path = config(tmp_path, [sources["adapter"], sources["raw"]])
    before = source_bytes(sources)
    assert len(report(process(path), path, sources)["records"]) == 2
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # No reader exists before the child starts: every write must fail.
    try:
        result = subprocess.run(arguments(path), cwd=path.parent, env=environment(), stdout=write_fd,
                                stderr=subprocess.PIPE, text=True, timeout=15)
    finally:
        os.close(write_fd)
    assert result.returncode != 0
    assert_private(result, path, sources)
    assert source_bytes(sources) == before
