"""Real adapter-store CLIs persist only explicitly bound synthetic local stores."""

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
from urllib.parse import quote

import pytest

from aegis.archive import RawArchive


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/adapter-input.synthetic.json"
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
RUN = "22222222-2222-4222-8222-222222222222"


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def write_fixture(tmp_path, data=None):
    path = tmp_path / "private-fixture-filename-marker.json"
    path.write_text(json.dumps(example() if data is None else data), encoding="utf-8")
    return path


def process(fixture, store, *extra, timeout=15):
    try:
        return subprocess.run([sys.executable, "-m", "aegis.adapter_store", "--fixture", str(fixture),
            "--store", str(store), *extra], cwd=fixture.parent,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail("Adapter-store CLI blocked instead of completing within the test deadline.")


def assert_private(result, fixture, store):
    output = result.stdout + result.stderr
    for private in (str(fixture), fixture.name, str(store), store.name,
                    "synthetic-token-a", "synthetic-token-b", "delivery-sample-a",
                    "delivery-sample-b", "SYNTHETIC-01", "synthetic.temperature", RUN,
                    "11111111-1111-4111-8111-111111111111", "1152921504606846976"):
        assert private not in output
    assert "Traceback" not in output


def report(result, fixture, store, *, status="END_OF_SYNTHETIC_INPUT", persisted=True):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    body = json.loads(result.stdout)
    assert body["schemaVersion"] == "adapter-store-report-1"
    assert body["status"] == status
    assert body["persistenceStatus"] == ("LOCAL_STORE_COMMITTED" if persisted else "NO_COMMITTED_PAGE")
    assert body["legacyArchiveDelivery"] == "NOT_CONNECTED"
    assert body["sourceAcknowledgement"] == "NOT_SENT"
    assert body["realEquipmentSupported"] is False and body["hardRealtimeGuaranteed"] is False
    assert_private(result, fixture, store)
    return body


def assert_error(result, fixture, store):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str) and body["error"]
    assert_private(result, fixture, store)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sqlite_snapshot(path):
    connection = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY 1,2").fetchall()
        tables = [name for kind, name, _, _ in schema if kind == "table" and not name.startswith("sqlite_")]
        rows = {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            rows[name] = sorted(connection.execute("SELECT * FROM " + quoted).fetchall(), key=repr)
        return {"applicationId": connection.execute("PRAGMA application_id").fetchone()[0],
                "version": connection.execute("PRAGMA user_version").fetchone()[0],
                "schema": schema, "rows": rows}
    finally:
        connection.close()


def prepare_store(tmp_path):
    fixture = write_fixture(tmp_path)
    store = tmp_path / "private-adapter-store-marker.sqlite"
    body = report(process(fixture, store), fixture, store)
    return fixture, store, body


@pytest.fixture
def legacy_archive(tmp_path):
    path = tmp_path / "legacy-raw.sqlite"

    async def seed():
        archive = RawArchive(path)
        await archive.start()
        try:
            samples = [{"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": "LEGACY-SYNTHETIC",
                "runId": RUN, "sequence": sequence, "observedAtUtc": AT.isoformat(),
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


def test_cli_commits_two_pages_and_restart_preserves_all_rows_receipts_and_cursor(tmp_path, legacy_archive):
    legacy_state, legacy_bytes = sqlite_snapshot(legacy_archive), legacy_archive.read_bytes()
    fixture = write_fixture(tmp_path)
    fixture_bytes = fixture.read_bytes()
    store = tmp_path / "private-adapter-store-marker.sqlite"
    first = report(process(fixture, store), fixture, store)
    assert first["counts"]["committedPages"] == 2
    assert first["counts"]["committedRecords"] == 2
    assert first["counts"]["thisRunCommittedPages"] == 2
    assert first["counts"]["pageReads"] == 2
    assert first["counts"]["missingSourceTimestampRecords"] == 1
    assert first["counts"]["missingSourceSequenceRecords"] == 1
    committed = sqlite_snapshot(store)
    assert committed["applicationId"] != 0 and committed["applicationId"] != legacy_state["applicationId"]
    assert committed["rows"] and any(committed["rows"].values())
    committed_bytes = store.read_bytes()
    second = report(process(fixture, store), fixture, store)
    assert second["counts"]["committedPages"] == 2 and second["counts"]["committedRecords"] == 2
    assert second["counts"]["thisRunCommittedPages"] == 0 and second["counts"]["pageReads"] == 0
    assert second["counts"]["missingSourceTimestampRecords"] == 1
    assert second["counts"]["missingSourceSequenceRecords"] == 1
    assert sqlite_snapshot(store) == committed and store.read_bytes() == committed_bytes
    assert sqlite_snapshot(legacy_archive) == legacy_state and legacy_archive.read_bytes() == legacy_bytes
    assert fixture.read_bytes() == fixture_bytes


def reverse_keys(value):
    if isinstance(value, dict):
        return {key: reverse_keys(item) for key, item in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_keys(item) for item in value]
    return value


def test_cli_same_canonical_fixture_accepts_whitespace_and_key_order_changes_without_writes(tmp_path):
    fixture, store, _ = prepare_store(tmp_path)
    committed, store_bytes = sqlite_snapshot(store), store.read_bytes()
    fixture.write_text(json.dumps(reverse_keys(example()), indent=4), encoding="utf-8")
    changed_fixture_bytes = fixture.read_bytes()
    body = report(process(fixture, store), fixture, store)
    assert body["counts"]["thisRunCommittedPages"] == 0 and body["counts"]["pageReads"] == 0
    assert body["counts"]["committedPages"] == 2 and body["counts"]["committedRecords"] == 2
    assert sqlite_snapshot(store) == committed and store.read_bytes() == store_bytes
    assert fixture.read_bytes() == changed_fixture_bytes


@pytest.mark.parametrize("change", ["content", "stream"])
def test_cli_changed_fixture_binding_is_rejected_without_changing_durable_store(tmp_path, change):
    fixture, store, _ = prepare_store(tmp_path)
    committed, store_bytes = sqlite_snapshot(store), store.read_bytes()
    data = example()
    if change == "content":
        data["pages"][0]["response"]["records"][0]["signals"][0]["value"] = -3.5
    else:
        data["streamId"] = "33333333-3333-4333-8333-333333333333"
        for page in data["pages"]:
            page["response"]["streamId"] = data["streamId"]
    fixture.write_text(json.dumps(data), encoding="utf-8")
    changed_fixture_bytes = fixture.read_bytes()
    assert_error(process(fixture, store), fixture, store)
    assert sqlite_snapshot(store) == committed and store.read_bytes() == store_bytes
    assert fixture.read_bytes() == changed_fixture_bytes


@pytest.mark.parametrize("ended", [False, True])
def test_cli_empty_terminal_page_and_transient_no_data_have_distinct_durable_results(tmp_path, ended):
    data = example()
    empty = copy.deepcopy(data["pages"][0])
    empty["response"].update(records=[], nextToken=None, endOfStream=ended)
    data["pages"] = [empty]
    fixture = write_fixture(tmp_path, data)
    store = tmp_path / "private-adapter-store-marker.sqlite"
    status = "END_OF_SYNTHETIC_INPUT" if ended else "NO_NEW_DATA"
    first = report(process(fixture, store), fixture, store, status=status, persisted=ended)
    assert first["counts"]["committedPages"] == int(ended)
    assert first["counts"]["committedRecords"] == 0
    assert first["counts"]["thisRunCommittedPages"] == int(ended)
    assert first["counts"]["pageReads"] == 1
    assert first["counts"]["missingSourceTimestampRecords"] == 0
    assert first["counts"]["missingSourceSequenceRecords"] == 0
    committed, store_bytes = sqlite_snapshot(store), store.read_bytes()
    second = report(process(fixture, store), fixture, store, status=status, persisted=ended)
    assert second["counts"]["committedPages"] == int(ended)
    assert second["counts"]["committedRecords"] == 0
    assert second["counts"]["thisRunCommittedPages"] == 0
    assert second["counts"]["pageReads"] == (0 if ended else 1)
    assert sqlite_snapshot(store) == committed and store.read_bytes() == store_bytes


def test_cli_legacy_raw_archive_cannot_be_used_as_adapter_store(tmp_path, legacy_archive):
    fixture = write_fixture(tmp_path)
    before, original_bytes = sqlite_snapshot(legacy_archive), legacy_archive.read_bytes()
    assert_error(process(fixture, legacy_archive), fixture, legacy_archive)
    assert sqlite_snapshot(legacy_archive) == before and legacy_archive.read_bytes() == original_bytes


@pytest.mark.parametrize("kind", ["empty", "unknown_sqlite", "non_sqlite"])
def test_cli_existing_unowned_files_are_refused_without_initializing_or_replacing_them(tmp_path, kind):
    fixture = write_fixture(tmp_path)
    store = tmp_path / "private-unowned-store-marker.sqlite"
    if kind == "unknown_sqlite":
        connection = sqlite3.connect(store)
        try:
            connection.execute("CREATE TABLE private_sentinel(value TEXT)")
            connection.execute("INSERT INTO private_sentinel VALUES ('synthetic protected data')")
            connection.commit()
        finally:
            connection.close()
    else:
        store.write_bytes(b"" if kind == "empty" else b"synthetic unowned data")
    original_bytes, inode = store.read_bytes(), store.stat().st_ino
    assert_error(process(fixture, store), fixture, store)
    assert store.read_bytes() == original_bytes and store.stat().st_ino == inode


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "hardlink"])
def test_cli_refuses_store_aliases_even_when_referent_is_a_valid_owned_store(tmp_path, kind):
    fixture, original, _ = prepare_store(tmp_path)
    original_bytes = original.read_bytes()
    store = tmp_path / "private-store-alias-marker.sqlite"
    if kind == "hardlink":
        os.link(original, store)
    else:
        store.symlink_to(original if kind == "symlink" else tmp_path / "private-missing-referent")
    before = store.lstat()
    assert_error(process(fixture, store), fixture, store)
    after = store.lstat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (before.st_dev, before.st_ino, before.st_mode)
    assert original.read_bytes() == original_bytes
    if kind == "dangling_symlink":
        assert not os.path.lexists(tmp_path / "private-missing-referent")


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_cli_reserved_sqlite_companion_names_are_not_created_as_stores(tmp_path, suffix):
    fixture = write_fixture(tmp_path)
    store = tmp_path / ("private-store-marker.sqlite" + suffix)
    assert_error(process(fixture, store), fixture, store)
    assert not os.path.lexists(store)


def test_cli_missing_store_parent_is_reported_without_creating_directories(tmp_path):
    fixture = write_fixture(tmp_path)
    parent = tmp_path / "private-missing-parent-marker"
    store = parent / "private-store-marker.sqlite"
    before = fixture.read_bytes()
    assert_error(process(fixture, store), fixture, store)
    assert not os.path.lexists(parent) and fixture.read_bytes() == before


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_cli_refuses_nonregular_fixture_before_store_writes_without_blocking(tmp_path, kind):
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures require POSIX.")
    fixture = tmp_path / "private-fixture-filename-marker.json"
    target = tmp_path / "private-fixture-target-marker.json"
    if kind == "fifo":
        os.mkfifo(fixture)
    else:
        target.write_text(json.dumps(example()), encoding="utf-8")
        fixture.symlink_to(target)
    store = tmp_path / "private-adapter-store-marker.sqlite"
    assert_error(process(fixture, store, timeout=2), fixture, store)
    assert not os.path.lexists(store)
    if kind == "symlink":
        assert fixture.is_symlink() and json.loads(target.read_text(encoding="utf-8")) == example()


@pytest.mark.parametrize("arguments", [("--timeout-ms", "0"), ("--timeout-ms", "10001"),
    ("--max-attempts", "0"), ("--max-attempts", "4"), ("--max-pages", "0"), ("--max-pages", "65")])
def test_cli_invalid_execution_limits_cannot_initialize_store(tmp_path, arguments):
    fixture = write_fixture(tmp_path)
    store = tmp_path / "private-adapter-store-marker.sqlite"
    assert_error(process(fixture, store, *arguments), fixture, store)
    assert not os.path.lexists(store)


def test_cli_timeout_never_claims_a_commit_or_changes_fixture_or_legacy_archive(tmp_path, legacy_archive):
    data = example()
    data["pages"][0]["delayMs"] = 50
    fixture = write_fixture(tmp_path, data)
    store = tmp_path / "private-adapter-store-marker.sqlite"
    source_bytes, legacy_bytes = fixture.read_bytes(), legacy_archive.read_bytes()
    assert_error(process(fixture, store, "--timeout-ms", "1", "--max-attempts", "2"), fixture, store)
    assert fixture.read_bytes() == source_bytes and legacy_archive.read_bytes() == legacy_bytes
