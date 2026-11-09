"""Storage diagnostics CLI reports measurements without changing archive evidence."""

import asyncio
import json
import os
import re
import shutil
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
DEVICE = "DIAG-SYNTHETIC-01"
RUN = "11111111-1111-4111-8111-111111111111"
TABLES = ("raw_batches", "ingest_cursors", "ingest_gaps")
CHECKS = ("archiveRetention", "backupMtimeReview", "receiptAge", "archiveCapacity",
          "backupCapacity", "commitLatency")
RAW_MARKER = "PRIVATE-RAW-SIGNAL-MARKER"


def sample(sequence, *, value=456789.25, quality="GOOD"):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": DEVICE,
            "runId": RUN, "sequence": sequence,
            "observedAtUtc": (AT - timedelta(days=2) + timedelta(seconds=sequence)).isoformat(),
            "deviceStateVersion": 1, "modeId": "PRODUCTION",
            "signals": [{"signalKey": RAW_MARKER, "value": value,
                         "unit": "count", "quality": quality}]}


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "private-archive-marker.sqlite"
    backup_dir = tmp_path / "private-backup-directory-marker"
    backup_dir.mkdir()

    async def seed():
        archive = RawArchive(source)
        await archive.start()
        try:
            await archive.append([sample(0, value=None, quality="BAD"), sample(1)],
                                 AT - timedelta(days=2), equipment_id="SYNTHETIC-EQUIPMENT",
                                 module_id="SYNTHETIC-MODULE")
            await archive.append([sample(4, value=None, quality="MISSING"), sample(5, value=-2.25)],
                                 AT - timedelta(days=1), gap=(2, 3))
        finally:
            await archive.close()

    asyncio.run(seed())
    use_delete_journal(source)
    backup = backup_dir / "private-backup-filename-marker.sqlite"
    # The fixture is closed and self-contained; this is not a live-file backup operation.
    shutil.copyfile(source, backup)
    stamp = (AT - timedelta(days=2)).timestamp()
    os.utime(backup, (stamp, stamp))
    return source, backup_dir, backup


def use_delete_journal(path):
    """Seal only this closed synthetic fixture before read-only CLI inspection."""
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    finally:
        connection.close()


def refresh_matching_cursor_receipt(path):
    connection = sqlite3.connect(path)
    try:
        changed = connection.execute("""UPDATE raw_batches SET received_at_utc=?
            WHERE device_id=? AND run_id=? AND sequence=5 AND EXISTS (
                SELECT 1 FROM ingest_cursors c WHERE c.device_id=raw_batches.device_id
                AND c.run_id=raw_batches.run_id AND c.sequence=raw_batches.sequence)""",
            ((AT - timedelta(seconds=1)).isoformat(), DEVICE, RUN))
        assert changed.rowcount == 1
        connection.commit()
    finally:
        connection.close()


def db_state(path):
    connection = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        return {table: connection.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()
                for table in TABLES}
    finally:
        connection.close()


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def policy(**limits):
    return {"format": "aegis-storage-policy-1", "policyId": "synthetic-retention-policy",
            "version": 1, **limits}


def telemetry(*, at=AT - timedelta(seconds=1), run=RUN, sequence=5):
    return {"format": "aegis-ingestion-capture-1", "capturedAtUtc": at.isoformat(),
            "ingestion": [{"deviceId": DEVICE, "transport": "replay", "archiveEnabled": True,
                           "lastCommittedRunId": run, "lastCommittedSequence": sequence,
                           "archiveCommitMs": 12.5, "acceptedBatches": 4}]}


def process(source, backup_dir, *, backups=(), policy_path=None, telemetry_path=None, at=AT,
            extra=()):
    arguments = [sys.executable, "-m", "aegis.storage_diagnostics", "--archive", str(source),
                 "--backup-dir", str(backup_dir)]
    for backup in backups:
        arguments.extend(["--backup", str(backup)])
    if policy_path is not None:
        arguments.extend(["--policy", str(policy_path)])
    if telemetry_path is not None:
        arguments.extend(["--telemetry", str(telemetry_path)])
    if at is not None:
        arguments.extend(["--at", at.isoformat() if isinstance(at, datetime) else at])
    return subprocess.run([*arguments, *extra], cwd=source.parent,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
        capture_output=True, text=True, timeout=15)


def report(result):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    body = json.loads(result.stdout)
    assert body["format"] == "aegis-storage-diagnostics-1"
    assert body["actionsPerformed"] == [] and body["sampling"] == "NON_ATOMIC"
    assert body.get("status") != "PASS"
    assert isinstance(body["clockMode"], str) and body["clockMode"]
    assert set(CHECKS) <= set(body["checks"])
    assert RAW_MARKER not in result.stdout and "456789.25" not in result.stdout
    return body


def assert_error(result, *private_paths):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"error"}
    assert re.fullmatch(r"[A-Z][A-Z0-9_]*", body["error"])
    assert "Traceback" not in result.stderr
    for path in private_paths:
        assert str(path) not in result.stderr and path.name not in result.stderr


def test_cli_unconfigured_report_preserves_exact_archive_cursor_gap_and_backups(files):
    source, backup_dir, backup = files
    original = db_state(source)
    source_bytes, backup_bytes = source.read_bytes(), backup.read_bytes()
    assert [len(original[table]) for table in TABLES] == [4, 1, 1]
    assert original["ingest_cursors"] == [(DEVICE, RUN, 5)]
    assert original["ingest_gaps"] == [(DEVICE, RUN, 2, 3, "REPLAY_OVERFLOW")]
    result = process(source, backup_dir, backups=[backup])
    body = report(result)
    assert datetime.fromisoformat(body["evaluatedAtUtc"].replace("Z", "+00:00")) == AT
    assert body["policy"]["status"] == "NOT_CONFIGURED"
    assert body["archive"]["status"] == "MEASURED"
    assert body["backups"]["status"] == "MEASURED"
    assert body["capacity"]["archive"]["status"] == "MEASURED"
    assert body["capacity"]["backup"]["status"] == "MEASURED"
    assert body["commitLatency"]["status"] == "NOT_MEASURED"
    assert all(body["checks"][name]["status"] == "NOT_CONFIGURED" for name in CHECKS)
    assert str(backup) not in result.stdout and backup.name not in result.stdout
    assert source.read_bytes() == source_bytes and db_state(source) == original
    assert backup.read_bytes() == backup_bytes


def test_cli_without_backup_or_telemetry_does_not_invent_measurements(files, tmp_path):
    source, backup_dir, _ = files
    policy_path = write_json(tmp_path / "policy.json", policy(backupMtimeReviewDays=1,
                                                             maxArchiveCommitMs=5))
    body = report(process(source, backup_dir, policy_path=policy_path))
    assert body["backups"]["status"] == "NOT_CONFIGURED"
    assert body["commitLatency"]["status"] == "NOT_MEASURED"
    assert body["checks"]["backupMtimeReview"]["status"] == "UNAVAILABLE"
    assert body["checks"]["commitLatency"]["status"] == "UNAVAILABLE"


def test_cli_thresholds_report_exceeded_and_keep_all_inputs_unchanged(files, tmp_path):
    source, backup_dir, backup = files
    refresh_matching_cursor_receipt(source)
    policy_path = write_json(tmp_path / "policy.json", policy(
        archiveReceiptRetentionDays=1, backupMtimeReviewDays=1, minAvailableBytes=2**63 - 1,
        minAvailablePercent=100, maxReceiptAgeSeconds=0.5, maxArchiveCommitMs=5,
        maxTelemetryAgeSeconds=60))
    capture = write_json(tmp_path / "telemetry.json", telemetry())
    paths = (source, backup, policy_path, capture)
    original = {path: path.read_bytes() for path in paths}
    body = report(process(source, backup_dir, backups=[backup], policy_path=policy_path,
                          telemetry_path=capture))
    assert all(body["checks"][name]["status"] == "EXCEEDED" for name in CHECKS)
    assert {path: path.read_bytes() for path in paths} == original


def test_cli_configured_measured_values_can_be_within_limits_without_global_pass(files, tmp_path):
    source, backup_dir, backup = files
    refresh_matching_cursor_receipt(source)
    policy_path = write_json(tmp_path / "policy.json", policy(
        archiveReceiptRetentionDays=3, backupMtimeReviewDays=3, minAvailableBytes=0,
        minAvailablePercent=0, maxReceiptAgeSeconds=3 * 86400, maxArchiveCommitMs=13,
        maxTelemetryAgeSeconds=60))
    capture = write_json(tmp_path / "capture.json", telemetry())
    paths = (source, backup, policy_path, capture)
    original = {path: path.read_bytes() for path in paths}
    body = report(process(source, backup_dir, backups=[backup], policy_path=policy_path,
                          telemetry_path=capture))
    assert all(body["checks"][name]["status"] == "WITHIN_LIMIT" for name in CHECKS)
    assert {path: path.read_bytes() for path in paths} == original


def test_cli_empty_archive_receipt_measurements_are_unavailable(files, tmp_path):
    _, backup_dir, _ = files
    empty = tmp_path / "private-empty-archive-marker.sqlite"

    async def create():
        archive = RawArchive(empty)
        await archive.start()
        await archive.close()

    asyncio.run(create())
    use_delete_journal(empty)
    original_bytes = empty.read_bytes()
    policy_path = write_json(tmp_path / "policy.json", policy(archiveReceiptRetentionDays=1,
                                                             maxReceiptAgeSeconds=60))
    body = report(process(empty, backup_dir, policy_path=policy_path))
    assert body["archive"]["status"] == "MEASURED"
    assert body["checks"]["archiveRetention"]["status"] == "UNAVAILABLE"
    assert body["checks"]["receiptAge"]["status"] == "UNAVAILABLE"
    assert empty.read_bytes() == original_bytes


@pytest.mark.parametrize("capture", [
    telemetry(at=AT - timedelta(hours=1)), telemetry(at=AT + timedelta(seconds=1)),
    telemetry(sequence=4), telemetry(run="22222222-2222-4222-8222-222222222222"),
])
def test_cli_commit_threshold_requires_fresh_matching_cursor_capture(files, tmp_path, capture):
    source, backup_dir, _ = files
    policy_path = write_json(tmp_path / "policy.json", policy(maxArchiveCommitMs=5,
                                                             maxTelemetryAgeSeconds=60))
    capture_path = write_json(tmp_path / "capture.json", capture)
    body = report(process(source, backup_dir, policy_path=policy_path, telemetry_path=capture_path))
    assert body["checks"]["commitLatency"]["status"] == "UNAVAILABLE"


def test_cli_explicit_future_evaluation_time_is_used_instead_of_wall_clock(files, tmp_path):
    source, backup_dir, _ = files
    future = AT + timedelta(days=365)
    policy_path = write_json(tmp_path / "policy.json", policy(maxReceiptAgeSeconds=60))
    body = report(process(source, backup_dir, policy_path=policy_path, at=future))
    assert datetime.fromisoformat(body["evaluatedAtUtc"].replace("Z", "+00:00")) == future
    assert body["checks"]["receiptAge"]["status"] == "EXCEEDED"


@pytest.mark.parametrize("kind", ["missing", "corrupt"])
def test_cli_archive_failure_is_reported_without_creating_or_repairing_it(files, tmp_path, kind):
    _, backup_dir, _ = files
    source = tmp_path / "private-broken-archive-marker.sqlite"
    if kind == "corrupt":
        source.write_bytes(b"synthetic invalid SQLite file")
    policy_path = write_json(tmp_path / "policy.json", policy(maxReceiptAgeSeconds=60))
    result = process(source, backup_dir, policy_path=policy_path)
    body = report(result)
    assert body["archive"]["status"] == ("MISSING" if kind == "missing" else "INVALID")
    assert body["checks"]["receiptAge"]["status"] == "UNAVAILABLE"
    assert str(source) not in result.stdout and source.name not in result.stdout
    if kind == "missing":
        assert not os.path.lexists(source)
    else:
        assert source.read_bytes() == b"synthetic invalid SQLite file"


def test_cli_missing_requested_backup_is_unavailable_and_is_not_created(files, tmp_path):
    source, backup_dir, _ = files
    missing = backup_dir / "private-missing-backup-marker.sqlite"
    policy_path = write_json(tmp_path / "policy.json", policy(backupMtimeReviewDays=1))
    result = process(source, backup_dir, backups=[missing], policy_path=policy_path)
    body = report(result)
    assert body["backups"]["status"] == "MISSING"
    assert body["checks"]["backupMtimeReview"]["status"] == "UNAVAILABLE"
    assert missing.name not in result.stdout and not os.path.lexists(missing)


@pytest.mark.parametrize("bad", [
    policy(unrecognizedLimit=1), policy(minAvailableBytes=True), policy(maxArchiveCommitMs=-1),
    policy(maxReceiptAgeSeconds=float("nan")), policy(minAvailablePercent=None),
    {**policy(), "version": True}, {**policy(), "format": "unrelated-format"},
])
def test_cli_bad_policy_fails_with_private_structured_stderr(files, tmp_path, bad):
    source, backup_dir, _ = files
    source_bytes = source.read_bytes()
    policy_path = write_json(tmp_path / "private-policy-marker.json", bad)
    policy_bytes = policy_path.read_bytes()
    result = process(source, backup_dir, policy_path=policy_path)
    assert_error(result, source, backup_dir, policy_path)
    assert source.read_bytes() == source_bytes and policy_path.read_bytes() == policy_bytes


@pytest.mark.parametrize("at", ["not-a-date", "2026-11-09T12:00:00", "2026-11-09T25:00:00Z"])
def test_cli_invalid_evaluation_time_has_no_partial_report(files, at):
    source, backup_dir, _ = files
    result = process(source, backup_dir, at=at)
    assert_error(result, source, backup_dir)


def test_cli_rejects_too_many_explicit_backups_without_leaking_filenames(files):
    source, backup_dir, backup = files
    result = process(source, backup_dir, backups=[backup] * 33)
    assert_error(result, source, backup_dir, backup)


def test_cli_policy_reference_is_independent_of_json_whitespace_and_key_order(files, tmp_path):
    source, backup_dir, _ = files
    data = policy(maxReceiptAgeSeconds=60)
    first = write_json(tmp_path / "first-policy.json", data)
    second = tmp_path / "second-policy.json"
    second.write_text(json.dumps(dict(reversed(list(data.items()))), indent=3), encoding="utf-8")
    first_body = report(process(source, backup_dir, policy_path=first))
    second_body = report(process(source, backup_dir, policy_path=second))
    assert first_body["policy"] == second_body["policy"]
    assert data["policyId"] in json.dumps(first_body["policy"])
    assert first_body["checks"]["receiptAge"] == second_body["checks"]["receiptAge"]


def test_cli_malformed_policy_json_has_structured_error_without_writes(files, tmp_path):
    source, backup_dir, _ = files
    policy_path = tmp_path / "private-policy-marker.json"
    policy_path.write_text('{"format":', encoding="utf-8")
    result = process(source, backup_dir, policy_path=policy_path)
    assert_error(result, source, backup_dir, policy_path)
    assert policy_path.read_text(encoding="utf-8") == '{"format":'
