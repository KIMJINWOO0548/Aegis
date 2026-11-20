"""Windows fail-closed behavior; synthesize inputs without modifying an existing archive."""

import os

import pytest

from test_archive_backup import seed as seed_raw_archive
from test_input_archive import descriptor

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows platform boundary")


def contents(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir()}


def test_nullable_archive_refuses_existing_and_new_destinations(tmp_path):
    from aegis.input_archive import InputArchive, StoreError

    existing = tmp_path / "existing.sqlite"
    seed_raw_archive(existing)
    before = contents(tmp_path)
    for path in (existing, tmp_path / "new.sqlite"):
        with pytest.raises(StoreError) as refusal:
            InputArchive(path, descriptor())
        assert refusal.value.code == "PLATFORM_UNVERIFIED"
    assert contents(tmp_path) == before


def test_archive_publication_refuses_backup_and_restore_without_writes(tmp_path):
    from aegis.archive_backup import BackupError, backup_archive, restore_archive

    source = tmp_path / "raw.sqlite"
    seed_raw_archive(source)
    existing = tmp_path / "preserved.sqlite"
    existing.write_bytes(b"synthetic destination")
    before = contents(tmp_path)
    for operation in (
        lambda: backup_archive(source, tmp_path / "new.sqlite"),
        lambda: backup_archive(source, existing),
        lambda: restore_archive(source, tmp_path / "restored.sqlite", {}),
    ):
        with pytest.raises(BackupError) as refusal:
            operation()
        assert refusal.value.code == "PLATFORM_UNVERIFIED"
        assert not refusal.value.published
    assert contents(tmp_path) == before


def test_storage_diagnostics_refuses_without_creating_or_repairing_files(tmp_path):
    from aegis.storage_diagnostics import DiagnosticError, diagnose_storage

    source = tmp_path / "raw.sqlite"
    seed_raw_archive(source)
    before = contents(tmp_path)
    for path in (source, tmp_path / "missing.sqlite"):
        with pytest.raises(DiagnosticError) as refusal:
            diagnose_storage(path, tmp_path)
        assert refusal.value.code == "PLATFORM_UNVERIFIED"
    assert contents(tmp_path) == before
