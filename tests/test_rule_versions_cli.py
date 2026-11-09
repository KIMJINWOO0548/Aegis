"""Real module CLIs preserve evidence and require an explicit matching rule reference."""

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

from aegis.archive import RawArchive
from aegis.limit_rules import LimitRuleConfig


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
REFERENCE = AT + timedelta(seconds=5)
DEVICE = "LOCK-SYNTHETIC-01"
PEER = "LOCK-PEER-01"
RUN = "11111111-1111-4111-8111-111111111111"


def rule(rule_id="alpha", *, version="1", device=DEVICE, upper=10, unit="count", max_age=10000):
    return {"ruleId": rule_id, "ruleVersion": version,
            "target": {"deviceId": device, "signalKey": "numeric.alpha"},
            "unit": unit, "lowerBound": 0, "upperBound": upper, "maxAgeMs": max_age}


def config(rules=None, *, version="1", identity="synthetic-rule-lock"):
    return {"schemaVersion": "numeric-limit-rules-1", "configId": identity, "configVersion": version,
            "rules": rules if rules is not None else [rule()]}


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def manifest(data):
    # Independently construct the declared wire format through the existing rule model.
    normalized = LimitRuleConfig.model_validate(data)
    entries = []
    for item in normalized.rules:
        snapshot = item.model_dump(mode="json")
        entries.append({"ruleId": item.ruleId, "ruleVersion": item.ruleVersion,
                        "ruleSha256": canonical_digest(snapshot), "ruleSnapshot": snapshot})
    return {"schemaVersion": "numeric-rule-lock-1", "ruleSchemaVersion": "numeric-limit-rules-1",
            "evaluationSchemaVersion": "numeric-limit-evaluation-1", "configId": normalized.configId,
            "rules": sorted(entries, key=lambda entry: (entry["ruleId"], entry["ruleVersion"]))}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def sample(sequence=0, *, quality="GOOD", value=9, observed=None, tag="numeric.alpha", unit="count"):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": DEVICE, "runId": RUN,
            "sequence": sequence, "observedAtUtc": observed or (AT + timedelta(seconds=sequence)).isoformat(),
            "deviceStateVersion": 8, "modeId": "PRODUCTION",
            "signals": [{"signalKey": tag, "value": value, "unit": unit, "quality": quality}]}


def save(path, rows, *, gap=None):
    async def write():
        archive = RawArchive(path)
        await archive.start()
        try:
            for index, row in enumerate(rows):
                await archive.append([row], AT + timedelta(seconds=4), equipment_id="STORED-EQUIPMENT",
                                     module_id="STORED-MODULE", gap=gap if index == 0 else None)
        finally:
            await archive.close()
    asyncio.run(write())


def db_state(path):
    db = sqlite3.connect(path)
    try:
        return {table: db.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()
                for table in ("raw_batches", "ingest_cursors", "ingest_gaps")}
    finally:
        db.close()


def process(arguments, directory):
    return subprocess.run([sys.executable, "-m", *arguments], cwd=directory,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
        capture_output=True, text=True, timeout=15)


def build(config_path, previous=None):
    args = ["aegis.rule_versions", "--config", str(config_path)]
    if previous is not None:
        args.extend(["--previous-lock", str(previous)])
    return process(args, config_path.parent)


def review(config_path, archive_path, lock=None, *, start=AT, end=None):
    args = ["aegis.limit_rules", "--config", str(config_path), "--archive", str(archive_path),
            "--device", DEVICE, "--run-id", RUN, "--from-utc", start.isoformat(),
            "--to-utc", (end or AT + timedelta(seconds=2)).isoformat(),
            "--evaluated-at-utc", REFERENCE.isoformat()]
    if lock is not None:
        args.extend(["--rule-lock", str(lock)])
    return process(args, config_path.parent)


def assert_error(result, code, *private_values):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"status", "code", "message"}
    assert body["status"] == "UNAVAILABLE" and body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert "Traceback" not in result.stderr
    for value in private_values:
        assert str(value) not in result.stderr


def assert_success(result):
    assert result.returncode == 0 and result.stderr == ""
    return json.loads(result.stdout)


def test_builder_roundtrip_and_verified_review_use_actual_module_entry_points_without_writes(tmp_path):
    config_path = write_json(tmp_path / "rules.json", config())
    archive = tmp_path / "raw.sqlite3"
    save(archive, [sample(0), sample(1)])
    before = db_state(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    initial_paths = {path.name for path in tmp_path.iterdir()}
    built = build(config_path)
    reference = assert_success(built)
    assert reference == manifest(config())
    assert {path.name for path in tmp_path.iterdir()} == initial_paths
    assert build(config_path).stdout == built.stdout
    lock_path = tmp_path / "reference.json"
    lock_path.write_text(built.stdout, encoding="utf-8")
    lock_bytes = lock_path.read_bytes()
    body = assert_success(review(config_path, archive, lock_path))
    verification = body["ruleVersionVerification"]
    assert verification == {"schemaVersion": "numeric-rule-lock-check-1", "status": "VERIFIED",
        "lockSha256": canonical_digest(reference), "configId": "synthetic-rule-lock",
        "lockRuleCount": 1, "checkedRuleCount": 1,
        "checkedRules": [{key: reference["rules"][0][key] for key in ("ruleId", "ruleVersion", "ruleSha256")}]}
    assert body["counts"]["WITHIN_LIMITS"] == 2
    evidence = body["results"][0]["sourceEvidence"]
    assert evidence["reading"] == {"value": 9, "unit": "count", "quality": "GOOD"}
    assert type(evidence["reading"]["value"]) is int
    assert evidence["observedAtUtc"] == AT.isoformat()
    assert evidence["receivedAtUtc"] == (AT + timedelta(seconds=4)).isoformat()
    assert evidence["equipmentId"] == "STORED-EQUIPMENT" and evidence["moduleId"] == "STORED-MODULE"
    assert lock_path.read_bytes() == lock_bytes and db_state(archive) == before
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


def test_without_flag_report_is_exactly_the_legacy_report_and_has_no_verification_claim(tmp_path):
    data = config()
    config_path = write_json(tmp_path / "rules.json", data)
    lock_path = write_json(tmp_path / "lock.json", manifest(data))
    archive = tmp_path / "raw.sqlite3"
    save(archive, [sample()])
    legacy = assert_success(review(config_path, archive))
    verified = assert_success(review(config_path, archive, lock_path))
    assert "ruleVersionVerification" not in legacy
    verification = verified.pop("ruleVersionVerification")
    assert verification["status"] == "VERIFIED" and verified == legacy


@pytest.mark.parametrize("change", ["bound", "unit", "target_device", "target_module", "freshness"])
def test_same_id_version_conflicts_even_with_config_version_bump_before_archive_access(tmp_path, change):
    original = config()
    lock_path = write_json(tmp_path / "PRIVATE-reference.json", manifest(original))
    changed = copy.deepcopy(original)
    changed["configVersion"] = "2"
    item = changed["rules"][0]
    if change == "bound": item["upperBound"] = 8
    elif change == "unit": item["unit"] = "volt"
    elif change == "target_device": item["target"]["deviceId"] = PEER
    elif change == "target_module": item["target"].update(equipmentId="OTHER-EQUIPMENT", moduleId="OTHER-MODULE")
    else: item["maxAgeMs"] = 5000
    config_path = write_json(tmp_path / "rules.json", changed)
    archive = tmp_path / "PRIVATE-missing.sqlite3"
    before = lock_path.read_bytes()
    assert_error(review(config_path, archive, lock_path), "RULE_VERSION_CONFLICT", archive, lock_path)
    assert not archive.exists() and lock_path.read_bytes() == before
    assert_error(build(config_path, lock_path), "RULE_VERSION_CONFLICT", archive, lock_path)


def test_new_version_is_unregistered_until_explicit_append_and_old_evidence_remains_reproducible(tmp_path):
    first = config()
    first_path = write_json(tmp_path / "v1.json", first)
    lock_path = write_json(tmp_path / "v1-lock.json", manifest(first))
    archive = tmp_path / "raw.sqlite3"
    save(archive, [sample()])
    before = db_state(archive)
    old_lock = lock_path.read_bytes()
    old = assert_success(review(first_path, archive, lock_path))
    second = config([rule(version="2", upper=8)], version="2")
    second_path = write_json(tmp_path / "v2.json", second)
    assert_error(review(second_path, archive, lock_path), "RULE_VERSION_UNREGISTERED", lock_path)
    built = build(second_path, lock_path)
    merged = assert_success(built)
    entries = {(row["ruleId"], row["ruleVersion"]): row for row in merged["rules"]}
    assert set(entries) == {("alpha", "1"), ("alpha", "2")}
    assert entries["alpha", "1"] == manifest(first)["rules"][0]
    merged_path = tmp_path / "v2-lock.json"
    merged_path.write_text(built.stdout, encoding="utf-8")
    current = assert_success(review(second_path, archive, merged_path))
    replay = assert_success(review(first_path, archive, merged_path))
    a, b, replayed = old["results"][0], current["results"][0], replay["results"][0]
    assert a["outcome"] == "WITHIN_LIMITS" and b["outcome"] == "ABOVE_UPPER"
    assert a["sourceEvidence"] == b["sourceEvidence"] == replayed["sourceEvidence"]
    assert a["projectionEvidenceSha256"] == b["projectionEvidenceSha256"] == replayed["projectionEvidenceSha256"]
    assert a == replayed and replay["configSnapshot"] == old["configSnapshot"]
    assert assert_success(review(first_path, archive, merged_path)) == replay
    assert lock_path.read_bytes() == old_lock and db_state(archive) == before


def test_builder_keeps_removed_historical_rules_and_same_content_reuse_is_stable(tmp_path):
    initial = config([rule(), rule("retired", version="old")])
    previous = write_json(tmp_path / "previous.json", manifest(initial))
    current = write_json(tmp_path / "current.json", config(version="2"))
    before = previous.read_bytes()
    result = build(current, previous)
    body = assert_success(result)
    assert body == manifest(initial)
    assert build(current, previous).stdout == result.stdout
    assert previous.read_bytes() == before


def test_all_current_config_rules_are_verified_including_unselected_devices(tmp_path):
    initial = config([rule(), rule("peer", device=PEER)])
    lock_path = write_json(tmp_path / "reference.json", manifest(initial))
    changed = copy.deepcopy(initial)
    changed["rules"][1]["upperBound"] = 8
    config_path = write_json(tmp_path / "rules.json", changed)
    archive = tmp_path / "PRIVATE-missing.sqlite3"
    assert_error(review(config_path, archive, lock_path), "RULE_VERSION_CONFLICT", archive, lock_path)
    assert not archive.exists()


def test_scope_mismatch_is_not_rebound_to_a_different_configuration(tmp_path):
    lock_path = write_json(tmp_path / "reference.json", manifest(config(identity="other-scope")))
    config_path = write_json(tmp_path / "rules.json", config())
    archive = tmp_path / "PRIVATE-missing.sqlite3"
    assert_error(review(config_path, archive, lock_path), "RULE_LOCK_SCOPE_MISMATCH", archive, lock_path)
    assert_error(build(config_path, lock_path), "RULE_LOCK_SCOPE_MISMATCH", lock_path)
    assert not archive.exists()


def test_verified_empty_selection_retains_configuration_and_version_reference(tmp_path):
    data = config()
    config_path = write_json(tmp_path / "rules.json", data)
    lock_path = write_json(tmp_path / "reference.json", manifest(data))
    archive = tmp_path / "raw.sqlite3"
    save(archive, [sample()])
    body = assert_success(review(config_path, archive, lock_path,
        start=AT + timedelta(seconds=10), end=AT + timedelta(seconds=11)))
    assert body["selectionStatus"] == "NO_SAMPLES" and body["results"] == []
    assert all(count == 0 for count in body["counts"].values())
    assert body["configSnapshot"]["rules"]
    assert body["ruleVersionVerification"]["status"] == "VERIFIED"
    assert body["ruleVersionVerification"]["checkedRuleCount"] == 1


@pytest.mark.parametrize("kind", ["BAD", "MISSING", "STALE", "ABSENT", "OLD_SOURCE", "WRONG_UNIT"])
def test_verified_rules_do_not_promote_unavailable_evidence_to_within_limits(tmp_path, kind):
    data = config()
    config_path = write_json(tmp_path / "rules.json", data)
    lock_path = write_json(tmp_path / "reference.json", manifest(data))
    archive = tmp_path / "raw.sqlite3"
    row = sample()
    start = AT
    if kind in {"BAD", "MISSING", "STALE"}: row["signals"][0].update(quality=kind, value=None)
    elif kind == "ABSENT": row["signals"][0]["signalKey"] = "not.selected"
    elif kind == "WRONG_UNIT": row["signals"][0]["unit"] = "volt"
    else:
        start = AT - timedelta(seconds=60)
        row["observedAtUtc"] = start.isoformat()
    save(archive, [row])
    before = db_state(archive)
    body = assert_success(review(config_path, archive, lock_path, start=start))
    assert body["ruleVersionVerification"]["status"] == "VERIFIED"
    assert body["counts"]["WITHIN_LIMITS"] == 0 and body["counts"]["UNAVAILABLE"] == 1
    assert body["results"][0]["outcome"] == "UNAVAILABLE" and db_state(archive) == before


def test_locked_review_keeps_run_gaps_and_reader_limits_without_partial_output(tmp_path):
    data = config()
    config_path = write_json(tmp_path / "rules.json", data)
    lock_path = write_json(tmp_path / "reference.json", manifest(data))
    archive = tmp_path / "raw.sqlite3"
    save(archive, [sample(3), sample(4)], gap=(0, 2))
    before = db_state(archive)
    args = ["aegis.limit_rules", "--config", str(config_path), "--rule-lock", str(lock_path),
            "--archive", str(archive), "--device", DEVICE, "--run-id", RUN,
            "--from-utc", AT.isoformat(), "--to-utc", (AT + timedelta(seconds=6)).isoformat(),
            "--evaluated-at-utc", (AT + timedelta(seconds=7)).isoformat()]
    body = assert_success(process(args, tmp_path))
    assert body["archiveSelection"]["sequenceGaps"] == {"scope": "SELECTED_RUN_SEQUENCE_ONLY", "items": [
        {"firstSequence": 0, "lastSequence": 2, "reason": "REPLAY_OVERFLOW"}]}
    assert_error(process([*args, "--max-batches", "1"], tmp_path), "RAW_QUERY_LIMIT", archive, lock_path)
    assert db_state(archive) == before


@pytest.mark.parametrize("kind", ["missing", "malformed", "schema", "digest", "inner_id", "inner_version",
                                  "duplicate_entry", "unknown_field", "duplicate_json_key", "invalid_utf8", "oversized"])
def test_actual_clis_reject_invalid_reference_without_traceback_paths_or_archive_access(tmp_path, kind):
    data = config()
    config_path = write_json(tmp_path / "rules.json", data)
    lock_path = tmp_path / "PRIVATE-reference.json"
    value = manifest(data)
    if kind == "schema": value["schemaVersion"] = "PRIVATE-unknown"
    elif kind == "digest": value["rules"][0]["ruleSnapshot"]["upperBound"] = 8
    elif kind == "inner_id": value["rules"][0]["ruleId"] = "other"
    elif kind == "inner_version": value["rules"][0]["ruleVersion"] = "other"
    elif kind == "duplicate_entry": value["rules"] *= 2
    elif kind == "unknown_field": value["PRIVATE-extra"] = True
    if kind != "missing":
        if kind == "malformed": lock_path.write_text("PRIVATE-invalid-content", encoding="utf-8")
        elif kind == "invalid_utf8": lock_path.write_bytes(b'\xff')
        elif kind == "oversized": lock_path.write_bytes(b' ' * (256 * 1024 + 1))
        elif kind == "duplicate_json_key":
            lock_path.write_text('{"configId":"PRIVATE-other",' + json.dumps(value)[1:], encoding="utf-8")
        else: write_json(lock_path, value)
    archive = tmp_path / "PRIVATE-missing.sqlite3"
    code = "RULE_LOCK_LIMIT" if kind == "oversized" else "INVALID_RULE_LOCK"
    assert_error(review(config_path, archive, lock_path), code, archive, lock_path, "PRIVATE")
    assert_error(build(config_path, lock_path), code, lock_path, "PRIVATE")
    assert not archive.exists()


def test_actual_builder_preserves_existing_config_error_contract(tmp_path):
    config_path = tmp_path / "PRIVATE-missing-config.json"
    assert_error(build(config_path), "INVALID_RULE_CONFIG", config_path)
