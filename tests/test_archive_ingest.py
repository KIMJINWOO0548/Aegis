"""Synthetic archive ingestion preserves nullable originals and confirms local writes only."""

import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = ("adapterId", "adapterVersion", "streamId", "declaredOrigin", "originEvidence",
           "acquisitionMethod", "sourceAccessMode")


def api():
    from aegis import archive_ingest
    return archive_ingest


def fixture():
    previous = json.loads((ROOT / "examples/adapter-input.synthetic.json").read_text(encoding="utf-8"))
    for entry in previous["pages"]:
        entry["response"]["schemaVersion"] = "archive-write-1"
    return {"schemaVersion": "archive-write-fixture-1",
            "descriptor": {key: previous["pages"][0]["response"][key] for key in HEADERS},
            "pages": previous["pages"]}


def wire(value):
    return json.dumps(value).encode("utf-8")


def write_fixture(tmp_path, data=None):
    path = tmp_path / "private-fixture-marker.json"
    path.write_bytes(wire(fixture() if data is None else data))
    return path


def process(path, store, *extra, stdout=subprocess.PIPE, timeout=15):
    try:
        return subprocess.run([sys.executable, "-m", "aegis.archive_ingest", "--fixture", str(path),
            "--store", str(store), *extra], cwd=path.parent,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, stdout=stdout,
            stderr=subprocess.PIPE, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail("Archive ingestion blocked instead of completing within the test deadline.")


def assert_private(result, path, store):
    output = (result.stdout or "") + result.stderr
    for value in (str(path), path.name, str(store), store.name, "delivery-sample-a", "delivery-sample-b",
                  "synthetic-token-a", "synthetic-token-b", "SYNTHETIC-01", "1152921504606846976",
                  "11111111-1111-4111-8111-111111111111", "PRIVATE-IO-MARKER"):
        assert value not in output
    assert "Traceback" not in output


def report(result, path, store, *, status="END_OF_SYNTHETIC_INPUT", persisted=True):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    body = json.loads(result.stdout)
    assert body["schemaVersion"] == "archive-ingest-report-1"
    assert body["status"] == status
    assert body["persistenceStatus"] == ("LOCAL_ARCHIVE_COMMITTED" if persisted else "NO_COMMITTED_PAGE")
    assert body["writeDelivery"] == "LOCAL_ARCHIVE" and body["sourceAcknowledgement"] == "NOT_SENT"
    assert body["executionSource"] == "SYNTHETIC_FIXTURE_ONLY" and body["realDriverExecuted"] is False
    assert body["realEquipmentSupported"] is False and body["hardRealtimeGuaranteed"] is False
    assert "descriptor" not in body and "records" not in body
    assert_private(result, path, store)
    return body


def assert_error(result, path, store, code=None):
    assert result.returncode == 2 and result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str) and body["error"]
    if code is not None:
        assert body["error"] == code
    assert_private(result, path, store)


def test_shipped_example_and_two_actual_cli_runs_preserve_originals_and_first_receipts(tmp_path):
    a = api()
    data = json.loads((ROOT / "examples/archive-write.synthetic.json").read_text(encoding="utf-8"))
    assert data == fixture()
    path = write_fixture(tmp_path, data)
    store = tmp_path / "private-archive-marker.sqlite"
    first = report(process(path, store), path, store)
    assert first["counts"]["committedPages"] == 2 and first["counts"]["committedRecords"] == 2
    assert first["counts"]["thisRunCommittedPages"] == 2 and first["counts"]["pageReads"] == 2
    assert first["counts"]["missingSourceTimestampRecords"] == 1
    assert first["counts"]["missingSourceSequenceRecords"] == 1
    with a.InputArchive(store, data["descriptor"]) as archive:
        originals = archive.records
        assert [item["record"] for item in originals] == [entry["response"]["records"][0] for entry in data["pages"]]
    before = store.read_bytes()
    second = report(process(path, store), path, store)
    assert second["counts"]["thisRunCommittedPages"] == 0 and second["counts"]["pageReads"] == 0
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.records == originals and archive.ended
    assert store.read_bytes() == before


def test_session_captures_host_receipt_after_read_and_preserves_missing_source_metadata(tmp_path, monkeypatch):
    a = api()
    data = fixture()
    source = a.SyntheticArchiveSource(data)
    clock = [AT]
    read = source.read

    async def delayed_receipt(request):
        value = await read(request)
        clock[0] = AT + timedelta(seconds=5)
        return value

    monkeypatch.setattr(source, "read", delayed_receipt)
    with a.InputArchive(tmp_path / "archive.sqlite", source.descriptor) as archive:
        session = a.ArchiveIngestSession(source, archive, utc_now=lambda: clock[0])
        outcome = asyncio.run(session.poll_once())
        assert outcome["pageCommitStatus"] == "LOCAL_ARCHIVE_COMMITTED"
        assert outcome["sourceAcknowledgement"] == "NOT_SENT"
        entry = archive.records[0]
        assert entry["record"]["sourceMetadata"] == data["pages"][0]["response"]["records"][0]["sourceMetadata"]
        assert entry["firstReceivedAtUtc"] == "2026-01-01T00:00:05Z"


def test_session_refreshes_external_committed_cursor_before_reading(tmp_path):
    a = api()
    data = fixture()
    source = a.SyntheticArchiveSource(data)
    path = tmp_path / "archive.sqlite"
    with a.InputArchive(path, source.descriptor) as archive:
        session = a.ArchiveIngestSession(source, archive, utc_now=lambda: AT + timedelta(seconds=1))
        with a.InputArchive(path, source.descriptor) as other:
            other.append_page(wire(data["pages"][0]["response"]), AT)
        asyncio.run(session.poll_once())
        assert source.read_calls == 1 and archive.ended and len(archive.records) == 2
        assert archive.records[0]["firstReceivedAtUtc"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("ended", [False, True])
def test_changed_committed_fixture_prefix_fails_before_read_even_at_eof(tmp_path, ended):
    a = api()
    data = fixture()
    store = tmp_path / "archive.sqlite"
    with a.InputArchive(store, data["descriptor"]) as archive:
        for entry in data["pages"][:2 if ended else 1]:
            archive.append_page(wire(entry["response"]), AT)
        before = archive.records
    original_bytes = store.read_bytes()
    data["pages"][0]["response"]["records"][0]["signals"][0]["value"] = -3.5
    source = a.SyntheticArchiveSource(data)
    with a.InputArchive(store, data["descriptor"]) as archive:
        with pytest.raises(a.IngestError) as error:
            a.ArchiveIngestSession(source, archive)
        assert error.value.code == "FIXTURE_PREFIX_MISMATCH" and source.read_calls == 0
        assert archive.records == before
    path = write_fixture(tmp_path, data)
    assert_error(process(path, store), path, store, "FIXTURE_PREFIX_MISMATCH")
    assert store.read_bytes() == original_bytes


def test_uncommitted_tail_can_change_while_prior_wire_and_receipt_are_preserved(tmp_path):
    a = api()
    data = fixture()
    store = tmp_path / "archive.sqlite"
    original_wire = json.dumps(data["pages"][0]["response"], indent=3).encode()
    with a.InputArchive(store, data["descriptor"]) as archive:
        archive.append_page(original_wire, AT)
        first = archive.records[0]
    data["pages"][1]["response"]["records"][0]["signals"][0]["value"] = 8
    path = write_fixture(tmp_path, data)
    result = report(process(path, store), path, store)
    assert result["counts"]["thisRunCommittedPages"] == 1 and result["counts"]["pageReads"] == 1
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.records[0] == first
        assert archive.refresh().pages[0].wire == original_wire
        assert archive.records[1]["record"]["signals"][0]["value"] == 8


def test_concurrent_different_tail_cannot_turn_own_retransmission_into_completed_claim(tmp_path, monkeypatch):
    a = api()
    data = fixture()
    source = a.SyntheticArchiveSource(data)
    path = tmp_path / "archive.sqlite"
    read = source.read

    async def competing_writer(request):
        payload = await read(request)
        with a.InputArchive(path, source.descriptor) as other:
            other.append_page(wire(data["pages"][0]["response"]), AT)
            changed = copy.deepcopy(data["pages"][1]["response"])
            changed["records"][0]["signals"][0]["value"] = 99
            other.append_page(wire(changed), AT)
        return payload

    monkeypatch.setattr(source, "read", competing_writer)
    with a.InputArchive(path, source.descriptor) as archive:
        session = a.ArchiveIngestSession(source, archive, utc_now=lambda: AT)
        with pytest.raises(a.IngestError) as error:
            asyncio.run(a._run_session(session, 64))
        assert error.value.code == "FIXTURE_PREFIX_MISMATCH"
        assert archive.ended and len(archive.records) == 2
        assert archive.records[1]["record"]["signals"][0]["value"] == 99


def test_original_uuid_spelling_is_preserved_and_mixed_headers_fail_before_creation(tmp_path):
    a = api()
    data = fixture()
    upper = "ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF"
    data["descriptor"]["streamId"] = upper
    for entry in data["pages"]:
        entry["response"]["streamId"] = upper
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    report(process(path, store), path, store)
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.refresh().descriptor["streamId"] == upper
    mixed = copy.deepcopy(data)
    mixed["descriptor"]["streamId"] = upper.lower()
    mixed_path = write_fixture(tmp_path, mixed)
    absent = tmp_path / "absent.sqlite"
    assert_error(process(mixed_path, absent), mixed_path, absent, "DESCRIPTOR_MISMATCH")
    assert not absent.exists()
    absent.write_bytes(b"")
    assert_error(process(mixed_path, absent), mixed_path, absent, "DESCRIPTOR_MISMATCH")
    assert absent.read_bytes() == b""


def test_terminal_delivery_token_backedge_is_rejected_before_store_creation(tmp_path):
    data = fixture()
    second = data["pages"][1]
    second["response"]["endOfStream"] = False
    third = copy.deepcopy(second)
    third["afterToken"] = "synthetic-token-b"
    third["response"].update(afterToken="synthetic-token-b", nextToken="synthetic-token-a", endOfStream=True)
    third["response"]["records"][0]["recordId"] = "delivery-sample-c"
    data["pages"].append(third)
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    assert_error(process(path, store), path, store)
    assert not store.exists()


def test_actual_cli_collect_then_stored_records_cli_queries_nullable_originals(tmp_path):
    api()
    data = fixture()
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    start = datetime.now(timezone.utc) - timedelta(seconds=1)
    report(process(path, store), path, store)
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    before = store.read_bytes()
    config = tmp_path / "query-sources.json"
    config.write_text(json.dumps({"schemaVersion": "stored-record-sources-1", "sources": [
        {"storeId": "collected", "kind": "INPUT_ARCHIVE_V1", "path": str(store)}]}), encoding="utf-8")
    queried = subprocess.run([sys.executable, "-m", "aegis.stored_records", "--sources", str(config),
        "--received-from", start.isoformat(), "--received-to", end.isoformat()], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, timeout=15)
    assert queried.returncode == 0, queried.stderr
    assert queried.stderr == ""
    body = json.loads(queried.stdout)
    assert body["sourceAcknowledgement"] == "NOT_SENT" and body["timeBasis"] == "FIRST_RECEIPT"
    records = body["records"]
    assert len(records) == 2
    assert all(row["identity"]["storeKind"] == "INPUT_ARCHIVE_V1" for row in records)
    assert [row["sourceMetadata"] for row in records] == [
        entry["response"]["records"][0]["sourceMetadata"] for entry in data["pages"]]
    assert records[1]["signals"][0]["reading"]["value"] == 2**60
    assert all(start <= datetime.fromisoformat(row["firstReceivedAtUtc"].replace("Z", "+00:00")) < end
               for row in records)
    assert "synthetic-token-a" not in queried.stdout and str(store) not in queried.stdout
    assert store.read_bytes() == before


def test_timeout_retries_are_bounded_and_never_capture_a_receipt_or_commit(tmp_path):
    a = api()
    data = fixture()
    data["pages"][0]["delayMs"] = 50
    source = a.SyntheticArchiveSource(data)
    receipts = []
    with a.InputArchive(tmp_path / "archive.sqlite", source.descriptor) as archive:
        session = a.ArchiveIngestSession(source, archive, timeout_ms=1, max_attempts=2,
                                        utc_now=lambda: receipts.append(AT) or AT)
        with pytest.raises(a.IngestError) as error:
            asyncio.run(session.poll_once())
        assert error.value.code == "SOURCE_TIMEOUT"
        assert source.read_calls == 2 and receipts == []
        assert archive.committed_cursor is None and archive.records == ()


@pytest.mark.parametrize("during_read", [True, False])
def test_cancellation_before_append_never_creates_a_committed_page(tmp_path, during_read):
    a = api()
    data = fixture()
    if during_read:
        data["pages"][0]["delayMs"] = 10000
    source = a.SyntheticArchiveSource(data)
    with a.InputArchive(tmp_path / "archive.sqlite", source.descriptor) as archive:
        def now():
            if not during_read:
                asyncio.current_task().cancel()
            return AT
        session = a.ArchiveIngestSession(source, archive, utc_now=now)

        async def check():
            task = asyncio.create_task(session.poll_once())
            if during_read:
                await asyncio.sleep(0.01)
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(check())
        assert archive.records == () and archive.committed_cursor is None


def test_source_io_failure_is_sanitized_and_cannot_advance_archive(tmp_path, monkeypatch):
    a = api()
    source = a.SyntheticArchiveSource(fixture())
    async def fail(_request):
        raise OSError("PRIVATE-IO-MARKER")
    monkeypatch.setattr(source, "read", fail)
    with a.InputArchive(tmp_path / "archive.sqlite", source.descriptor) as archive:
        with pytest.raises(a.IngestError) as error:
            asyncio.run(a.ArchiveIngestSession(source, archive).poll_once())
        assert error.value.code == "SOURCE_UNAVAILABLE" and "PRIVATE" not in str(error.value)
        assert archive.records == () and archive.committed_cursor is None


def test_read_limit_preserves_a_valid_prefix_then_restart_resumes(tmp_path):
    a = api()
    data = fixture()
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    assert_error(process(path, store, "--max-pages", "1"), path, store, "READ_LIMIT")
    with a.InputArchive(store, data["descriptor"]) as archive:
        first = archive.records[0]
        assert len(archive.records) == 1 and not archive.ended
    resumed = report(process(path, store), path, store)
    assert resumed["counts"]["thisRunCommittedPages"] == 1 and resumed["counts"]["pageReads"] == 1
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.records[0] == first and len(archive.records) == 2 and archive.ended


@pytest.mark.parametrize("ended", [False, True])
def test_empty_no_data_stops_and_terminal_eof_is_durable(tmp_path, ended):
    a = api()
    data = fixture()
    data["pages"] = [data["pages"][0]]
    data["pages"][0]["response"].update(records=[], nextToken=None, endOfStream=ended)
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    status = "END_OF_SYNTHETIC_INPUT" if ended else "NO_NEW_DATA"
    first = report(process(path, store), path, store, status=status, persisted=ended)
    assert first["counts"]["committedPages"] == int(ended) and first["counts"]["committedRecords"] == 0
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.committed_cursor is None and archive.ended is ended
    second = report(process(path, store), path, store, status=status, persisted=ended)
    assert second["counts"]["thisRunCommittedPages"] == 0
    assert second["counts"]["pageReads"] == (0 if ended else 1)


@pytest.mark.parametrize("fault", ["origin", "header", "missing_chain", "hidden", "loop", "last_record", "conflict"])
def test_invalid_complete_fixture_never_creates_store_or_changes_existing_target(tmp_path, fault):
    data = fixture()
    if fault == "origin":
        data["descriptor"]["declaredOrigin"] = "DEVICE"
        for entry in data["pages"]:
            entry["response"]["declaredOrigin"] = "DEVICE"
    elif fault == "header":
        data["pages"][1]["response"]["streamId"] = "33333333-3333-4333-8333-333333333333"
    elif fault == "missing_chain":
        data["pages"] = [data["pages"][0]]
    elif fault == "hidden":
        data["pages"][0]["response"]["endOfStream"] = True
    elif fault == "loop":
        data["pages"][1]["response"].update(endOfStream=False, nextToken="synthetic-token-a")
    elif fault == "last_record":
        data["pages"][1]["response"]["records"][0]["sourceMetadata"]["sequence"] = True
    else:
        data["pages"][1]["response"]["records"] = copy.deepcopy(data["pages"][0]["response"]["records"])
        data["pages"][1]["response"]["records"][0]["signals"][0]["value"] = 99
    path = write_fixture(tmp_path, data)
    store = tmp_path / "new.sqlite"
    assert_error(process(path, store), path, store)
    assert not os.path.lexists(store)
    store.write_bytes(b"synthetic protected target")
    assert_error(process(path, store), path, store)
    assert store.read_bytes() == b"synthetic protected target"


@pytest.mark.parametrize("fault", ["duplicate", "nan", "oversize", "fifo"])
def test_fixture_reader_is_bounded_strict_and_nonblocking(tmp_path, fault):
    if fault == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures require POSIX.")
    if fault == "fifo":
        path = tmp_path / "private-fixture-marker.json"
        os.mkfifo(path)
    else:
        path = write_fixture(tmp_path)
        if fault == "duplicate":
            content = path.read_text(encoding="utf-8")
            path.write_text('{"schemaVersion":"archive-write-fixture-1",' + content[1:], encoding="utf-8")
        elif fault == "nan":
            data = fixture()
            data["pages"][1]["response"]["records"][0]["signals"][0]["value"] = float("nan")
            path.write_bytes(wire(data))
        else:
            content = path.read_bytes()
            path.write_bytes(content + b" " * (1024 * 1024 + 1 - len(content)))
    store = tmp_path / "new.sqlite"
    assert_error(process(path, store, timeout=2), path, store)
    assert not os.path.lexists(store)


@pytest.mark.parametrize("arguments", [("--timeout-ms", "0"), ("--max-attempts", "4"), ("--max-pages", "65")])
def test_invalid_policy_and_missing_parent_fail_before_store_creation(tmp_path, arguments):
    path = write_fixture(tmp_path)
    store = tmp_path / "missing-parent" / "archive.sqlite"
    assert_error(process(path, store, *arguments), path, store)
    assert not store.parent.exists()


def test_broken_stdout_after_commit_is_unknown_but_durable_archive_remains(tmp_path):
    a = api()
    data = fixture()
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = process(path, store, stdout=write_fd)
    finally:
        os.close(write_fd)
    assert result.returncode == 2 and json.loads(result.stderr) == {"error": "STORE_RESULT_UNKNOWN"}
    assert_private(result, path, store)
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.ended and len(archive.records) == 2


@pytest.mark.parametrize("limited", [False, True])
def test_close_error_after_commit_is_unknown_and_success_json_is_not_emitted(tmp_path, monkeypatch, capsys, limited):
    a = api()
    data = fixture()
    path = write_fixture(tmp_path, data)
    store = tmp_path / "archive.sqlite"
    close = a.InputArchive.close
    failed = [False]
    def close_then_fail(self):
        close(self)
        if not failed[0]:
            failed[0] = True
            raise OSError("PRIVATE-IO-MARKER")
    with monkeypatch.context() as patch:
        patch.setattr(a.InputArchive, "close", close_then_fail)
        arguments = ["--fixture", str(path), "--store", str(store)]
        if limited:
            arguments += ["--max-pages", "1"]
        assert a.main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err) == {"error": "STORE_RESULT_UNKNOWN"}
    with a.InputArchive(store, data["descriptor"]) as archive:
        assert archive.ended is not limited and len(archive.records) == (1 if limited else 2)
