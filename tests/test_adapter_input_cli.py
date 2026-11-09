"""Actual adapter CLI processes admit synthetic contracts without persisting raw data."""

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aegis.archive import RawArchive


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
STREAM = "11111111-1111-4111-8111-111111111111"
SOURCE_RUN = "22222222-2222-4222-8222-222222222222"
DEVICE = "PRIVATE-DEVICE-MARKER"
TOKEN1 = "PRIVATE-AFTER-TOKEN-ONE"
TOKEN2 = "PRIVATE-AFTER-TOKEN-TWO"
RAW_KEY = "PRIVATE-RAW-SIGNAL-MARKER"


def record(identity="private-record-one", *, provided=False, value=456789.25):
    return {"recordId": identity, "deviceId": DEVICE,
            "sourceMetadata": {"observedAtUtc": AT.isoformat() if provided else None,
                "timestampStatus": "PROVIDED" if provided else "NOT_PROVIDED",
                "sequence": 7 if provided else None,
                "sequenceStatus": "PROVIDED" if provided else "NOT_PROVIDED",
                "runId": SOURCE_RUN if provided else None,
                "runIdStatus": "PROVIDED" if provided else "NOT_PROVIDED"},
            "signals": [{"signalKey": RAW_KEY, "value": value,
                         "unit": "PRIVATE-UNIT-MARKER", "quality": "GOOD"}]}


def page(records=None, *, after=None, next_token=TOKEN1, end=True):
    return {"schemaVersion": "adapter-input-1", "adapterId": "synthetic-01",
            "adapterVersion": "1.0", "streamId": STREAM, "declaredOrigin": "SYNTHETIC",
            "originEvidence": "DECLARED_ONLY", "acquisitionMethod": "SOURCE_BUFFER_REPLAY",
            "sourceAccessMode": "READ_ONLY", "afterToken": after, "nextToken": next_token,
            "endOfStream": end, "records": [record()] if records is None else records}


def fixture(pages=None):
    return {"schemaVersion": "synthetic-adapter-fixture-1", "adapterId": "synthetic-01",
            "adapterVersion": "1.0", "streamId": STREAM,
            "pages": [{"afterToken": item["afterToken"], "response": item}
                      for item in ([page()] if pages is None else pages)]}


def write_fixture(tmp_path, data=None):
    path = tmp_path / "private-fixture-filename-marker.json"
    path.write_text(json.dumps(fixture() if data is None else data), encoding="utf-8")
    return path


def process(path, *extra, timeout=15):
    try:
        return subprocess.run([sys.executable, "-m", "aegis.adapter_input", "--fixture", str(path), *extra],
            cwd=path.parent,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail("Adapter CLI blocked instead of completing within the test deadline.")


def assert_private(result, path):
    output = result.stdout + result.stderr
    for private in (str(path), path.name, DEVICE, STREAM, SOURCE_RUN, TOKEN1, TOKEN2,
                    RAW_KEY, "PRIVATE-UNIT-MARKER", "private-record-one", "456789.25"):
        assert private not in output
    assert "Traceback" not in output


def report(result, path, *, status="END_OF_SYNTHETIC_INPUT"):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    body = json.loads(result.stdout)
    assert body["status"] == status
    assert body["persistenceStatus"] == "NOT_PERSISTED"
    assert body["legacyArchiveIntegration"] == "DISABLED"
    assert body["realEquipmentSupported"] is False and body["hardRealtimeGuaranteed"] is False
    assert body["declaredOrigin"] == "SYNTHETIC"
    assert body["acquisitionMethod"] == "SOURCE_BUFFER_REPLAY"
    assert body["sourceAccessMode"] == "READ_ONLY"
    assert_private(result, path)
    return body


def assert_error(result, path):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str) and body["error"]
    assert_private(result, path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def legacy_archive(tmp_path):
    path = tmp_path / "legacy-raw.sqlite"

    async def seed():
        archive = RawArchive(path)
        await archive.start()
        try:
            samples = [{"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": "LEGACY-SYNTHETIC",
                "runId": SOURCE_RUN, "sequence": sequence, "observedAtUtc": AT.isoformat(),
                "deviceStateVersion": 1, "modeId": "PRODUCTION",
                "signals": [{"signalKey": "legacy.numeric", "value": sequence,
                             "unit": "count", "quality": "GOOD"}]} for sequence in (0, 4)]
            await archive.append([samples[0]], AT)
            await archive.append([samples[1]], AT, gap=(1, 3))
        finally:
            await archive.close()

    asyncio.run(seed())
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    finally:
        connection.close()
    return path


def directory_evidence(directory):
    return {str(path.relative_to(directory)): digest(path) for path in directory.rglob("*")
            if path.is_file()}


def test_cli_preserves_nullable_and_provided_metadata_counts_without_legacy_writes(tmp_path, legacy_archive):
    path = write_fixture(tmp_path, fixture([page([record(), record("private-record-two", provided=True)])]))
    before = directory_evidence(tmp_path)
    body = report(process(path), path)
    assert body["counts"]["acceptedRecords"] == 2
    assert body["counts"]["missingSourceTimestampRecords"] == 1
    assert body["counts"]["missingSourceSequenceRecords"] == 1
    assert body["counts"]["pageReads"] == 1
    assert directory_evidence(tmp_path) == before


def test_cli_no_new_data_ends_immediately_without_inventing_a_record(tmp_path):
    path = write_fixture(tmp_path, fixture([page([], next_token=None, end=False)]))
    body = report(process(path), path, status="NO_NEW_DATA")
    assert body["counts"]["acceptedRecords"] == 0
    assert body["counts"]["missingSourceTimestampRecords"] == 0
    assert body["counts"]["missingSourceSequenceRecords"] == 0
    assert body["counts"]["pageReads"] == 1


def test_cli_identical_replayed_record_is_not_counted_twice(tmp_path):
    first = page(end=False)
    second = page(after=TOKEN1, next_token=TOKEN2)
    path = write_fixture(tmp_path, fixture([first, second]))
    before = digest(path)
    body = report(process(path), path)
    assert body["counts"]["acceptedRecords"] == 1
    assert body["counts"]["missingSourceTimestampRecords"] == 1
    assert body["counts"]["missingSourceSequenceRecords"] == 1
    assert body["counts"]["pageReads"] == 2
    assert digest(path) == before


def test_cli_changed_replayed_record_fails_without_partial_stdout_or_legacy_changes(tmp_path, legacy_archive):
    first = page(end=False)
    second = page([record(value=-3)], after=TOKEN1, next_token=TOKEN2)
    path = write_fixture(tmp_path, fixture([first, second]))
    before = directory_evidence(tmp_path)
    assert_error(process(path), path)
    assert directory_evidence(tmp_path) == before


def test_cli_rejects_a_whole_page_when_a_later_record_is_invalid(tmp_path, legacy_archive):
    bad = record("private-record-two", provided=True)
    bad["sourceMetadata"]["sequence"] = True
    path = write_fixture(tmp_path, fixture([page([record(), bad])]))
    before = directory_evidence(tmp_path)
    assert_error(process(path), path)
    assert directory_evidence(tmp_path) == before


def test_cli_delayed_synthetic_page_times_out_after_bounded_attempts(tmp_path):
    data = fixture()
    data["pages"][0]["delayMs"] = 50
    path = write_fixture(tmp_path, data)
    before = digest(path)
    assert_error(process(path, "--timeout-ms", "1", "--max-attempts", "2"), path)
    assert digest(path) == before


@pytest.mark.parametrize("case", ["fixture_version", "page_version", "adapter_version", "origin",
    "source_access", "missing_metadata_key", "timestamp_nullness", "sequence_nullness",
    "invalid_run", "extra_page_key", "repeated_record_id"])
def test_cli_rejects_unsupported_or_inconsistent_source_contracts(tmp_path, case):
    data = fixture()
    response = data["pages"][0]["response"]
    metadata = response["records"][0]["sourceMetadata"]
    if case == "fixture_version":
        data["schemaVersion"] = "synthetic-adapter-fixture-2"
    elif case == "page_version":
        response["schemaVersion"] = "adapter-input-2"
    elif case == "adapter_version":
        data["adapterVersion"] = response["adapterVersion"] = "2.0"
    elif case == "origin":
        response["declaredOrigin"] = "DEVICE"
    elif case == "source_access":
        response["sourceAccessMode"] = "READ_WRITE"
    elif case == "missing_metadata_key":
        del metadata["timestampStatus"]
    elif case == "timestamp_nullness":
        metadata["timestampStatus"] = "PROVIDED"
    elif case == "sequence_nullness":
        metadata["sequence"] = 1
    elif case == "invalid_run":
        metadata["runId"], metadata["runIdStatus"] = "invalid-source-run", "PROVIDED"
    elif case == "extra_page_key":
        response["unrecognizedField"] = True
    else:
        response["records"].append(copy.deepcopy(response["records"][0]))
    path = write_fixture(tmp_path, data)
    before = digest(path)
    assert_error(process(path), path)
    assert digest(path) == before


@pytest.mark.parametrize("arguments", [("--timeout-ms", "0"), ("--timeout-ms", "10001"),
    ("--max-attempts", "0"), ("--max-attempts", "4"), ("--max-pages", "0"),
    ("--max-pages", "65"), ("--timeout-ms", "true")])
def test_cli_rejects_invalid_execution_limits_without_disclosing_fixture_path(tmp_path, arguments):
    path = write_fixture(tmp_path)
    assert_error(process(path, *arguments), path)


@pytest.mark.parametrize("case", ["duplicate_key", "nan", "oversize"])
def test_cli_rejects_duplicate_json_keys_nonfinite_values_and_oversize_input(tmp_path, case):
    path = write_fixture(tmp_path)
    if case == "duplicate_key":
        content = path.read_text(encoding="utf-8")
        path.write_text('{"schemaVersion":"synthetic-adapter-fixture-1",' + content[1:], encoding="utf-8")
    elif case == "nan":
        data = fixture([page([record(value=float("nan"))])])
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        content = path.read_bytes()
        path.write_bytes(content + b" " * (1024 * 1024 + 1 - len(content)))
    before = digest(path)
    assert_error(process(path), path)
    assert digest(path) == before


@pytest.mark.parametrize("case", ["missing", "directory", "symlink", "fifo"])
def test_cli_refuses_nonregular_or_followed_fixture_paths_without_blocking(tmp_path, case):
    if case == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures require POSIX.")
    path = tmp_path / "private-fixture-filename-marker.json"
    target = tmp_path / "private-symlink-target-marker.json"
    if case == "directory":
        path.mkdir()
    elif case == "symlink":
        target.write_text(json.dumps(fixture()), encoding="utf-8")
        path.symlink_to(target)
    elif case == "fifo":
        os.mkfifo(path)
    original_mode = path.lstat().st_mode if os.path.lexists(path) else None
    result = process(path, timeout=2)
    assert_error(result, path)
    assert (path.lstat().st_mode if os.path.lexists(path) else None) == original_mode
    if case == "symlink":
        assert path.is_symlink() and json.loads(target.read_text(encoding="utf-8")) == fixture()


@pytest.mark.parametrize("case", ["page_count", "records_per_page", "total_records"])
def test_cli_fixture_page_and_record_limits_are_checked_before_admission(tmp_path, case):
    if case == "records_per_page":
        pages = [page([record(f"private-record-{index}") for index in range(65)])]
    else:
        lengths = [1] * 65 if case == "page_count" else [64] * 16 + [1]
        pages = []
        for index, length in enumerate(lengths):
            records = [record(f"private-record-{index}-{item}") for item in range(length)]
            pages.append(page(records, after=None if index == 0 else f"private-token-{index}",
                              next_token=f"private-token-{index + 1}", end=index == len(lengths) - 1))
    path = write_fixture(tmp_path, fixture(pages))
    before = digest(path)
    assert_error(process(path), path)
    assert digest(path) == before
