from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import sysconfig
from threading import Barrier
import time

import pytest

from aegis.adapter_input import SyntheticReplayAdapter
from aegis.adapter_store import AdapterStore, StoreError
from test_adapter_input import AT, fixture, page, record, wire


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows production storage")
FIRST_RECEIPT = "2026-11-09T12:00:00Z"


def source_for(*pages):
    return SyntheticReplayAdapter(json.dumps(fixture(*pages), indent=2).encode() + b"\n")


def assert_empty(store):
    assert store.committed_cursor is None
    assert not store.ended
    assert store.records == ()
    assert store.report()["counts"]["committedPages"] == 0


def test_persisted_schema_fixture_receipt_and_cursor_resume(tmp_path):
    first = page(end=False)
    second = page([record("r1", provided=True)], after="delivery-1", next="delivery-2")
    source = source_for(first, second)
    raw_page = json.dumps(first, indent=3).encode() + b"\n"
    path = tmp_path / "store.sqlite"

    with AdapterStore(path, source) as store:
        assert_empty(store)
        result = store.commit_page(raw_page, AT)
        assert result["pageCommitStatus"] == "LOCAL_STORE_COMMITTED"
        assert result["sourceAcknowledgement"] == "NOT_SENT"
        assert result["legacyArchiveDelivery"] == "NOT_CONNECTED"
        assert result["durability"]["directorySync"] == "UNSUPPORTED"
        assert result["durability"]["powerLoss"] == "NOT_VERIFIED"

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
        assert reader.execute("PRAGMA application_id").fetchone() == (0x41445031,)
        assert reader.execute("PRAGMA user_version").fetchone() == (1,)
        assert {row[0] for row in reader.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )} == {"store_meta", "store_state", "adapter_pages", "adapter_records"}
        assert reader.execute("SELECT fixture_wire, fixture_wire_sha FROM store_meta").fetchone() == (
            source.fixture_wire, hashlib.sha256(source.fixture_wire).hexdigest()
        )
        assert reader.execute("SELECT wire, wire_sha, received_at_utc FROM adapter_pages").fetchone() == (
            raw_page, hashlib.sha256(raw_page).hexdigest(), FIRST_RECEIPT
        )
        assert reader.execute("SELECT cursor_key, ended, page_count, record_count FROM store_state").fetchone() == (
            '"delivery-1"', 0, 1, 1
        )

    with AdapterStore(path, source) as store:
        assert store.committed_cursor == "delivery-1"
        assert not store.ended
        assert store.records[0]["record"] == record()
        assert store.records[0]["firstReceivedAtUtc"] == FIRST_RECEIPT
        store.commit_page(wire(second), AT + timedelta(seconds=1))

    with AdapterStore(path, source) as store:
        assert store.committed_cursor == "delivery-2"
        assert store.ended
        assert [entry["firstReceivedAtUtc"] for entry in store.records] == [
            FIRST_RECEIPT, "2026-11-09T12:00:01Z"
        ]
        assert store.records[1]["record"]["sourceMetadata"]["sequence"] == 2**60


def test_precommit_write_exception_rolls_back_and_allows_retry(tmp_path, monkeypatch):
    incoming = page()
    path = tmp_path / "store.sqlite"
    source = source_for(incoming)
    with AdapterStore(path, source) as store:
        write = store._write_candidate

        def write_then_raise(*args):
            write(*args)
            raise OSError(errno.ENOSPC, "injected precommit write failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write_candidate", write_then_raise)
            with pytest.raises(StoreError) as failure:
                store.commit_page(wire(incoming), AT)
        assert failure.value.code == "STORE_FULL"
        assert_empty(store)

    with AdapterStore(path, source) as store:
        assert_empty(store)
        result = store.commit_page(wire(incoming), AT)
        assert result["pageCommitStatus"] == "LOCAL_STORE_COMMITTED"


CRASH_WORKER = """
from datetime import datetime
from pathlib import Path
import sys

from aegis.adapter_input import SyntheticReplayAdapter
from aegis.adapter_store import AdapterStore

path, fixture, payload, ready = map(Path, sys.argv[1:5])
if sys.argv[5] == "publish":
    from aegis.windows_sqlite import WindowsStorePath
    def pause_publication(self, stage):
        assert stage.is_file() and not self.path.exists()
        ready.touch()
        sys.stdin.buffer.read(1)
        raise AssertionError("parent released worker without terminating it")
    WindowsStorePath.publish = pause_publication
with AdapterStore(path, SyntheticReplayAdapter(fixture.read_bytes())) as store:
    commit = store._commit

    def pause_at_boundary():
        assert store._connection.in_transaction
        if sys.argv[5] == "after":
            commit()
            assert not store._connection.in_transaction
        ready.touch()
        sys.stdin.buffer.read(1)
        raise AssertionError("parent released worker without terminating it")

    store._commit = pause_at_boundary
    store.commit_page(payload.read_bytes(), datetime.fromisoformat(sys.argv[6]))
"""


@pytest.mark.parametrize("boundary", ["publish", "before", "after"])
def test_native_process_kill_at_commit_boundary_recovers_without_duplicate(tmp_path, boundary):
    incoming = page()
    path = tmp_path / "store.sqlite"
    source = source_for(incoming)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_bytes(source.fixture_wire)
    payload = tmp_path / "page.json"
    payload.write_bytes(wire(incoming))
    ready = tmp_path / "transaction-ready"
    if boundary != "publish":
        with AdapterStore(path, source):
            pass

    # Launch the base executable directly so kill() targets the SQLite writer, not the venv launcher.
    python_path = os.pathsep.join([
        str(Path(__file__).resolve().parents[1] / "src"),
        sysconfig.get_path("purelib"),
    ])
    with subprocess.Popen(
        [sys._base_executable, "-c", CRASH_WORKER, str(path), str(fixture_path),
         str(payload), str(ready), boundary, AT.isoformat()],
        env={**os.environ, "PYTHONPATH": python_path},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as child:
        deadline = time.monotonic() + 10
        try:
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "writer failed to reach the requested transaction boundary"
            assert child.poll() is None
        finally:
            if child.poll() is None:
                child.kill()
            stdout, stderr = child.communicate(timeout=5)
        assert child.returncode != 0
        assert (stdout, stderr) == (b"", b"")

    if boundary == "publish":
        assert not path.exists()
        stages = list(tmp_path.glob(".aegis-adp-*"))
        assert len(stages) == 1
        orphan = stages[0].read_bytes()
    with AdapterStore(path, source) as store:
        if boundary != "after":
            assert_empty(store)
        else:
            assert store.committed_cursor == "delivery-1"
            assert store.records[0]["firstReceivedAtUtc"] == FIRST_RECEIPT
        later = AT + timedelta(seconds=1)
        result = store.commit_page(wire(incoming), later)
        expected = "ALREADY_LOCAL_STORE_COMMITTED" if boundary == "after" else "LOCAL_STORE_COMMITTED"
        receipt = FIRST_RECEIPT if boundary == "after" else "2026-11-09T12:00:01Z"
        assert result["pageCommitStatus"] == expected
        assert result["sourceAcknowledgement"] == "NOT_SENT"
        assert store.committed_cursor == "delivery-1"
        assert store.report()["counts"]["committedPages"] == 1
        assert [entry["firstReceivedAtUtc"] for entry in store.records] == [receipt]
    if boundary == "publish":
        assert stages[0].read_bytes() == orphan


def test_concurrent_writers_persist_one_page_and_preserve_winner_receipt(tmp_path):
    incoming = page()
    path = tmp_path / "store.sqlite"
    source = source_for(incoming)
    with AdapterStore(path, source):
        pass
    ready = Barrier(2, timeout=5)

    def submit(received_at):
        with AdapterStore(path, source) as store:
            ready.wait()
            result = store.commit_page(wire(incoming), received_at)
            assert result["sourceAcknowledgement"] == "NOT_SENT"
            return result["pageCommitStatus"]

    receipts = (AT, AT + timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, receipts, timeout=10))
    assert sorted(results) == ["ALREADY_LOCAL_STORE_COMMITTED", "LOCAL_STORE_COMMITTED"]
    winner = receipts[results.index("LOCAL_STORE_COMMITTED")].isoformat().replace("+00:00", "Z")
    with AdapterStore(path, source) as store:
        assert store.committed_cursor == "delivery-1"
        assert store.report()["counts"]["committedPages"] == 1
        assert [entry["firstReceivedAtUtc"] for entry in store.records] == [winner]


def test_native_exclusive_file_handle_rejects_open_and_preserves_bytes(tmp_path):
    import _winapi

    path = tmp_path / "store.sqlite"
    source = source_for(page())
    with AdapterStore(path, source):
        pass
    before = path.read_bytes()
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 0, 0, _winapi.OPEN_EXISTING, 0, 0)
    try:
        with pytest.raises(StoreError):
            with AdapterStore(path, source):
                pytest.fail("production store opened an exclusively held file")
    finally:
        _winapi.CloseHandle(handle)
    assert path.read_bytes() == before
    with AdapterStore(path, source) as store:
        assert_empty(store)


def test_native_readonly_file_rejects_store_and_preserves_bytes(tmp_path):
    path = tmp_path / "store.sqlite"
    incoming = page()
    source = source_for(incoming)
    with AdapterStore(path, source) as store:
        store.commit_page(wire(incoming), AT)
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}

    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            with path.open("r+b"):
                pytest.fail("Windows did not enforce the read-only file attribute")
        with pytest.raises(StoreError):
            with AdapterStore(path, source):
                pytest.fail("production store opened a read-only file for writing")
        assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)

    with AdapterStore(path, source) as store:
        assert store.committed_cursor == "delivery-1"
        assert store.records[0]["firstReceivedAtUtc"] == FIRST_RECEIPT


@pytest.mark.parametrize("damage", ["partial", "corrupt"])
def test_rejected_partial_or_corrupt_store_preserves_bytes(tmp_path, damage):
    path = tmp_path / "store.sqlite"
    incoming = page()
    source = source_for(incoming)
    with AdapterStore(path, source) as store:
        store.commit_page(wire(incoming), AT)

    if damage == "partial":
        path.write_bytes(path.read_bytes()[:96])
    else:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE store_state SET cursor_key='null'")

    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(StoreError) as failure:
        with AdapterStore(path, source):
            pytest.fail("production store accepted damaged evidence")
    assert failure.value.code == ("STORE_NOT_OURS" if damage == "partial" else "STORE_INTEGRITY")
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before


@pytest.mark.parametrize("fault", ["flush", "contending_destination"])
def test_fresh_publication_fails_without_exposing_partial_or_overwriting(tmp_path, monkeypatch, fault):
    from aegis import windows_sqlite

    path = tmp_path / "store.sqlite"
    source = source_for(page())
    if fault == "flush":
        def fail(_fd):
            raise OSError(errno.EIO, "injected stage flush failure")
        monkeypatch.setattr(windows_sqlite.os, "fsync", fail)
    else:
        publish = windows_sqlite.WindowsStorePath.publish

        def collide(self, stage):
            self.path.write_bytes(b"unowned destination")
            publish(self, stage)
        monkeypatch.setattr(windows_sqlite.WindowsStorePath, "publish", collide)
    with pytest.raises(StoreError):
        AdapterStore(path, source)
    if fault == "flush":
        assert not path.exists()
    else:
        assert path.read_bytes() == b"unowned destination"
    assert not list(tmp_path.glob(".aegis-adp-*"))


def test_windows_native_staging_and_live_path_protection(tmp_path):
    import _winapi

    path = tmp_path / "store.sqlite"
    source = source_for(page())
    with AdapterStore(path, source) as store:
        with pytest.raises(PermissionError):
            path.rename(tmp_path / "moved.sqlite")
        store.commit_page(wire(page()), AT)
        assert store.report()["durability"] == {
            "transactionSync": "SQLITE_WAL_FULL", "directorySync": "UNSUPPORTED", "powerLoss": "NOT_VERIFIED"}
    unsafe = tmp_path / "alias"
    _winapi.CreateJunction(str(tmp_path), str(unsafe))
    try:
        before = path.read_bytes()
        with pytest.raises(StoreError, match="UNSAFE_STORE_PATH"):
            AdapterStore(unsafe / path.name, source)
        assert path.read_bytes() == before
    finally:
        unsafe.rmdir()


def test_rejected_existing_wal_evidence_is_not_checkpointed_on_close(tmp_path):
    path = tmp_path / "store.sqlite"
    source = source_for(page())
    with AdapterStore(path, source) as store:
        store.commit_page(wire(page()), AT)
    with closing(sqlite3.connect(path)) as connection:
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        with connection:
            connection.execute("UPDATE store_state SET cursor_key='null'")
    wal = path.with_name(path.name + "-wal")
    before_db, before_wal = path.read_bytes(), wal.read_bytes()
    with pytest.raises(StoreError, match="STORE_INTEGRITY"):
        AdapterStore(path, source)
    assert path.read_bytes() == before_db
    assert wal.read_bytes() == before_wal


def test_unexpected_rollback_journal_is_preserved_for_explicit_recovery(tmp_path):
    path = tmp_path / "store.sqlite"
    source = source_for(page())
    with AdapterStore(path, source):
        pass
    journal = path.with_name(path.name + "-journal")
    journal.write_bytes(b"unexpected prior recovery evidence")
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(StoreError, match="STORE_RECOVERY_REQUIRED"):
        AdapterStore(path, source)
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before


def test_partial_wal_header_is_rejected_instead_of_declaring_empty_recovery(tmp_path):
    path = tmp_path / "store.sqlite"
    source = source_for(page())
    with AdapterStore(path, source):
        pass
    wal = path.with_name(path.name + "-wal")
    wal.write_bytes(b"partial WAL header")
    before = path.read_bytes()
    with pytest.raises(StoreError, match="STORE_RECOVERY_REQUIRED"):
        AdapterStore(path, source)
    assert path.read_bytes() == before
    assert wal.read_bytes() == b"partial WAL header"


def test_two_processes_initialize_and_commit_same_request_once(tmp_path):
    source = source_for(page())
    path = tmp_path / "store.sqlite"
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_bytes(source.fixture_wire)
    payload = tmp_path / "page.json"
    payload.write_bytes(wire(page()))
    gate = tmp_path / "go"
    script = """
from datetime import datetime
from pathlib import Path
import sys, time
from aegis.adapter_input import SyntheticReplayAdapter
from aegis.adapter_store import AdapterStore
store, fixture, page, ready, gate = map(Path, sys.argv[1:6])
ready.touch()
deadline = time.monotonic() + 10
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(.01)
assert gate.exists()
with AdapterStore(store, SyntheticReplayAdapter(fixture.read_bytes())) as writer:
    print(writer.commit_page(page.read_bytes(), datetime.fromisoformat(sys.argv[6]))["pageCommitStatus"])
"""
    python_path = os.pathsep.join([str(Path(__file__).resolve().parents[1] / "src"), sysconfig.get_path("purelib")])
    children = []
    try:
        receipts = [AT, AT + timedelta(seconds=1)]
        ready = [tmp_path / f"ready-{index}" for index in range(2)]
        for marker, receipt in zip(ready, receipts):
            children.append(subprocess.Popen(
                [sys._base_executable, "-c", script, str(path), str(fixture_path), str(payload),
                 str(marker), str(gate), receipt.isoformat()],
                env={**os.environ, "PYTHONPATH": python_path}, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW))
        deadline = time.monotonic() + 10
        while not all(marker.exists() for marker in ready) and time.monotonic() < deadline:
            time.sleep(.02)
        assert all(marker.exists() for marker in ready)
        gate.touch()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0 and not stderr
            results.append(stdout.strip())
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
    assert sorted(results) == [b"ALREADY_LOCAL_STORE_COMMITTED", b"LOCAL_STORE_COMMITTED"]
    winner = receipts[results.index(b"LOCAL_STORE_COMMITTED")].isoformat().replace("+00:00", "Z")
    with AdapterStore(path, source) as store:
        assert store.report()["counts"]["committedPages"] == 1
        assert store.committed_cursor == "delivery-1"
        assert [row["firstReceivedAtUtc"] for row in store.records] == [winner]
