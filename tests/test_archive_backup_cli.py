"""Real backup CLI processes preserve raw evidence and refuse unsafe destinations."""

import asyncio
import copy
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from aegis.archive import RawArchive


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
DEVICE = "BACKUP-SYNTHETIC-01"
PEER = "BACKUP-SYNTHETIC-02"
RUN = "11111111-1111-4111-8111-111111111111"
OLD_RUN = "22222222-2222-4222-8222-222222222222"
TABLES = ("raw_batches", "ingest_cursors", "ingest_gaps")


def sample(sequence, *, run=RUN, device=DEVICE, value=9, quality="GOOD"):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
            "runId": run, "sequence": sequence,
            "observedAtUtc": (AT + timedelta(seconds=sequence)).isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": "numeric.synthetic", "value": value,
                         "unit": "count", "quality": quality}]}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "private-source-marker.sqlite"

    async def seed():
        archive = RawArchive(path)
        await archive.start()
        try:
            await archive.append([sample(100, run=OLD_RUN)], AT)
            await archive.append([sample(0, value=None, quality="BAD"), sample(1, value=-2.25)],
                                 AT + timedelta(seconds=1), equipment_id="SYNTHETIC-EQUIPMENT",
                                 module_id="SYNTHETIC-MODULE")
            await archive.append([sample(4, value=None, quality="MISSING"),
                                  sample(5, value=9007199254740994)],
                                 AT + timedelta(seconds=2), gap=(2, 3))
            await archive.append([sample(7, device=PEER, value=None, quality="STALE")],
                                 AT + timedelta(seconds=3))
        finally:
            await archive.close()

    asyncio.run(seed())
    return path


def db_state(path):
    connection = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        return {table: connection.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()
                for table in TABLES}
    finally:
        connection.close()


def process(command, source, *, destination=None, manifest=None):
    arguments = [sys.executable, "-m", "aegis.archive_backup", command, "--source", str(source)]
    if destination is not None:
        arguments.extend(["--destination", str(destination)])
    if manifest is not None:
        arguments.extend(["--manifest", str(manifest)])
    return subprocess.run(arguments, cwd=source.parent,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
        capture_output=True, text=True, timeout=15)


def success(result):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def assert_manifest(body, artifact):
    assert set(body) == {"format", "artifactSha256", "snapshot"}
    assert body["format"] == "aegis-archive-backup-1"
    assert re.fullmatch(r"[a-f0-9]{64}", body["artifactSha256"])
    assert body["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert isinstance(body["snapshot"], dict) and body["snapshot"]
    assert str(artifact.parent) not in json.dumps(body)


def assert_error(result, *private_paths):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error", "publication"}
    assert re.fullmatch(r"[A-Z][A-Z0-9_]*", body["error"])
    assert body["publication"] == "NOT_PUBLISHED"
    assert "Traceback" not in result.stderr
    for path in private_paths:
        assert str(path) not in result.stderr
        assert path.name not in result.stderr


def prepare_backup(source, tmp_path):
    backup = tmp_path / "private-backup-marker.sqlite"
    result = process("backup", source, destination=backup)
    body = success(result)
    assert_manifest(body, backup)
    manifest = tmp_path / "private-manifest-marker.json"
    # The unwrapped stdout must itself be the expected manifest file.
    manifest.write_text(result.stdout, encoding="utf-8")
    return backup, manifest, body


def input_for(command, source, tmp_path):
    if command == "backup":
        return source, None
    backup, manifest, _ = prepare_backup(source, tmp_path)
    return backup, manifest


def test_cli_roundtrip_preserves_exact_rows_first_receipts_cursors_gaps_and_resume(source, tmp_path):
    before = db_state(source)
    original_bytes = source.read_bytes()
    assert [len(before[table]) for table in TABLES] == [6, 2, 1]
    assert (DEVICE, RUN, 5) in before["ingest_cursors"]
    assert before["ingest_gaps"] == [(DEVICE, RUN, 2, 3, "REPLAY_OVERFLOW")]
    backup, manifest, reference = prepare_backup(source, tmp_path)
    manifest_bytes = manifest.read_bytes()
    backup_bytes = backup.read_bytes()
    assert db_state(backup) == before
    assert success(process("verify", backup, manifest=manifest)) == {
        "status": "VERIFIED", "manifest": reference}
    restored = tmp_path / "private-restored-marker.sqlite"
    restored_manifest = success(process("restore", backup, destination=restored, manifest=manifest))
    assert_manifest(restored_manifest, restored)
    assert restored_manifest["snapshot"] == reference["snapshot"]
    assert db_state(restored) == before
    assert source.read_bytes() == original_bytes and db_state(source) == before
    assert backup.read_bytes() == backup_bytes and manifest.read_bytes() == manifest_bytes

    async def resume():
        archive = RawArchive(restored)
        await archive.start()
        try:
            assert await archive.cursor(DEVICE) == (RUN, 5)
            # Changed retry receipt/hierarchy must not replace the first receipt.
            await archive.append([sample(5, value=9007199254740994)], AT + timedelta(days=1),
                                 equipment_id="RETRY", module_id="RETRY")
            assert db_state(restored) == before
            await archive.append([sample(6, value=-3)], AT + timedelta(seconds=8))
            assert await archive.cursor(DEVICE) == (RUN, 6)
        finally:
            await archive.close()

    asyncio.run(resume())
    after = db_state(restored)
    assert len(after["raw_batches"]) == 7
    assert all(row in after["raw_batches"] for row in before["raw_batches"])
    assert after["ingest_gaps"] == before["ingest_gaps"]
    assert db_state(source) == before and source.read_bytes() == original_bytes
    assert backup.read_bytes() == backup_bytes and manifest.read_bytes() == manifest_bytes


@pytest.mark.parametrize("command", ["verify", "restore"])
@pytest.mark.parametrize("field", ["artifactSha256", "snapshot"])
def test_cli_rejects_tampered_expected_manifest_without_publishing(source, tmp_path, command, field):
    backup, manifest, reference = prepare_backup(source, tmp_path)
    tampered = copy.deepcopy(reference)
    if field == "artifactSha256":
        digest = reference[field]
        tampered[field] = ("0" if digest[0] != "0" else "1") + digest[1:]
    else:
        tampered[field]["unexpectedTampering"] = True
    manifest.write_text(json.dumps(tampered), encoding="utf-8")
    backup_bytes, manifest_bytes = backup.read_bytes(), manifest.read_bytes()
    target = tmp_path / "private-target-marker.sqlite"
    result = process(command, backup, destination=target if command == "restore" else None,
                     manifest=manifest)
    assert_error(result, backup, manifest, target)
    assert not os.path.lexists(target)
    assert backup.read_bytes() == backup_bytes and manifest.read_bytes() == manifest_bytes


@pytest.mark.parametrize("command", ["verify", "restore"])
def test_cli_rejects_changed_sqlite_content_even_when_database_integrity_is_ok(source, tmp_path, command):
    backup, manifest, _ = prepare_backup(source, tmp_path)
    original = db_state(source)
    with sqlite3.connect(backup) as connection:
        connection.execute("UPDATE raw_batches SET received_at_utc=? WHERE device_id=? AND sequence=5",
                           ((AT + timedelta(days=1)).isoformat(), DEVICE))
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    changed_bytes = backup.read_bytes()
    target = tmp_path / "private-target-marker.sqlite"
    result = process(command, backup, destination=target if command == "restore" else None,
                     manifest=manifest)
    assert_error(result, backup, manifest, target)
    assert not os.path.lexists(target)
    assert backup.read_bytes() == changed_bytes and db_state(source) == original


@pytest.mark.parametrize("command", ["backup", "restore"])
@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling_symlink", "hardlink"])
def test_cli_preserves_existing_destination_and_aliases(source, tmp_path, command, kind):
    input_path, manifest = input_for(command, source, tmp_path)
    input_bytes = input_path.read_bytes()
    target = tmp_path / "private-target-marker.sqlite"
    protected = tmp_path / "private-protected-marker"
    if kind == "directory":
        target.mkdir()
        protected = target / "sentinel"
        protected.write_bytes(b"existing directory data")
    elif kind == "hardlink":
        os.link(input_path, target)
    elif kind in ("symlink", "dangling_symlink"):
        if kind == "symlink":
            protected.write_bytes(b"existing symlink referent")
        target.symlink_to(protected)
    else:
        target.write_bytes(b"existing target data")
    before_stat = target.lstat()
    protected_bytes = protected.read_bytes() if protected.is_file() else None
    target_bytes = target.read_bytes() if target.is_file() else None
    result = process(command, input_path, destination=target, manifest=manifest)
    assert_error(result, input_path, target, protected)
    after_stat = target.lstat()
    assert (after_stat.st_dev, after_stat.st_ino, after_stat.st_mode, after_stat.st_mtime_ns) == (
        before_stat.st_dev, before_stat.st_ino, before_stat.st_mode, before_stat.st_mtime_ns)
    if target_bytes is not None:
        assert target.read_bytes() == target_bytes
    if protected_bytes is not None:
        assert protected.read_bytes() == protected_bytes
    elif kind == "dangling_symlink":
        assert not os.path.lexists(protected)
    assert input_path.read_bytes() == input_bytes


@pytest.mark.parametrize("command", ["backup", "restore"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_cli_preserves_existing_destination_sidecars(source, tmp_path, command, suffix):
    input_path, manifest = input_for(command, source, tmp_path)
    input_bytes = input_path.read_bytes()
    target = tmp_path / "private-target-marker.sqlite"
    sidecar = Path(str(target) + suffix)
    sidecar.write_bytes(b"existing user-owned SQLite companion")
    before_stat = sidecar.stat()
    result = process(command, input_path, destination=target, manifest=manifest)
    assert_error(result, input_path, target, sidecar)
    assert not os.path.lexists(target)
    assert sidecar.read_bytes() == b"existing user-owned SQLite companion"
    after_stat = sidecar.stat()
    assert (after_stat.st_dev, after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_dev, before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns)
    assert input_path.read_bytes() == input_bytes


@pytest.mark.parametrize("command", ["backup", "restore"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_cli_refuses_absent_source_companion_as_destination(source, tmp_path, command, suffix):
    input_path, manifest = input_for(command, source, tmp_path)
    input_bytes = input_path.read_bytes()
    target = Path(str(input_path) + suffix)
    assert not os.path.lexists(target)
    result = process(command, input_path, destination=target, manifest=manifest)
    assert_error(result, input_path, target)
    assert not os.path.lexists(target)
    assert input_path.read_bytes() == input_bytes


@pytest.mark.parametrize("command", ["backup", "verify", "restore"])
@pytest.mark.parametrize("kind", ["missing", "corrupt"])
def test_cli_does_not_create_or_rewrite_missing_or_corrupt_source(source, tmp_path, command, kind):
    _, manifest = input_for(command, source, tmp_path)
    invalid = tmp_path / "private-invalid-source-marker.sqlite"
    if kind == "corrupt":
        invalid.write_bytes(b"synthetic invalid SQLite content")
    target = tmp_path / "private-target-marker.sqlite"
    result = process(command, invalid, destination=target if command != "verify" else None,
                     manifest=manifest)
    assert_error(result, invalid, target)
    assert not os.path.lexists(target)
    if kind == "missing":
        assert not os.path.lexists(invalid)
    else:
        assert invalid.read_bytes() == b"synthetic invalid SQLite content"


@pytest.mark.parametrize("command", ["verify", "restore"])
def test_cli_requires_expected_manifest_and_emits_only_structured_error(source, tmp_path, command):
    input_bytes = source.read_bytes()
    target = tmp_path / "private-target-marker.sqlite"
    result = process(command, source, destination=target if command == "restore" else None)
    assert_error(result, source, target)
    assert not os.path.lexists(target) and source.read_bytes() == input_bytes


@pytest.mark.parametrize("command", ["verify", "restore"])
def test_cli_rejects_corrupt_manifest_without_partial_stdout_or_source_change(source, tmp_path, command):
    backup, manifest, _ = prepare_backup(source, tmp_path)
    backup_bytes = backup.read_bytes()
    manifest.write_text('{"format":', encoding="utf-8")
    target = tmp_path / "private-target-marker.sqlite"
    result = process(command, backup, destination=target if command == "restore" else None,
                     manifest=manifest)
    assert_error(result, backup, manifest, target)
    assert not os.path.lexists(target) and backup.read_bytes() == backup_bytes
    assert manifest.read_text(encoding="utf-8") == '{"format":'
