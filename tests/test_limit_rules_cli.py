"""Offline archive rule review keeps configuration, source evidence and query limits."""

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

import pytest

from aegis.archive import RawArchive
from aegis.raw_history import RawQueryError


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
REFERENCE = AT + timedelta(seconds=5)
DEVICE = "RULE-CONTROL-01"
PEER = "RULE-PEER-01"
RUN = "11111111-1111-4111-8111-111111111111"
OUTCOMES = ("WITHIN_LIMITS", "BELOW_LOWER", "ABOVE_UPPER", "UNAVAILABLE")


def rule(rule_id="alpha", *, device=DEVICE, tag="numeric.alpha", unit="count", lower=-10,
         upper=0, version="1", module=False, max_age=10000):
    target = {"deviceId": device, "signalKey": tag}
    if module:
        target.update(equipmentId="STORED-EQUIPMENT", moduleId="STORED-MODULE")
    return {"ruleId": rule_id, "ruleVersion": version, "target": target, "unit": unit,
            "lowerBound": lower, "upperBound": upper, "maxAgeMs": max_age}


def config(rules=None, *, version="1"):
    return {"schemaVersion": "numeric-limit-rules-1", "configId": "synthetic-limits",
            "configVersion": version, "rules": rules if rules is not None else [rule()]}


def sample(sequence=0, *, device=DEVICE, observed=None, signals=None):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": device,
        "runId": RUN, "sequence": sequence,
        "observedAtUtc": observed or (AT + timedelta(seconds=sequence)).isoformat(),
        "deviceStateVersion": 7, "modeId": "PRODUCTION", "signals": signals if signals is not None else [
            {"signalKey": "numeric.alpha", "value": -5, "unit": "count", "quality": "GOOD"}]}


def save(path, rows, *, receipt=None, gap=None):
    async def write():
        archive = RawArchive(path)
        await archive.start()
        try:
            for index, row in enumerate(rows):
                await archive.append([row], receipt or AT + timedelta(seconds=4),
                    equipment_id="STORED-EQUIPMENT", module_id="STORED-MODULE",
                    gap=gap if index == 0 else None)
        finally:
            await archive.close()
    asyncio.run(write())


def db_state(path):
    connection = sqlite3.connect(path)
    try:
        return {name: connection.execute("SELECT * FROM " + name + " ORDER BY 1,2,3").fetchall()
                for name in ("raw_batches", "ingest_cursors", "ingest_gaps")}
    finally:
        connection.close()


def load(path, data):
    from aegis.limit_rules import load_rule_config
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_rule_config(path)


def review(path, data=None, *, reference=REFERENCE, start=AT, end=None, max_batches=64, run=RUN):
    from aegis.limit_rules import review_archive
    loaded = load(path.parent / "rules.json", data or config())
    return review_archive(loaded, path, DEVICE, run, start.isoformat(),
        (end or AT + timedelta(seconds=3)).isoformat(), max_batches=max_batches, evaluated_at=reference)


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def command(config_path, archive_path, *, reference=REFERENCE, extras=()):
    args = [sys.executable, "-m", "aegis.limit_rules", "--config", str(config_path),
        "--archive", str(archive_path), "--device", DEVICE, "--run-id", RUN,
        "--from-utc", AT.isoformat(), "--to-utc", (AT + timedelta(seconds=3)).isoformat()]
    if reference is not None:
        args.extend(["--evaluated-at-utc", reference.isoformat() if isinstance(reference, datetime) else reference])
    return [*args, *extras]


def run_cli(config_path, archive_path, *, reference=REFERENCE, extras=()):
    return subprocess.run(command(config_path, archive_path, reference=reference, extras=extras),
        cwd=archive_path.parent, env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)},
        capture_output=True, text=True, timeout=15)


def assert_cli_error(result, code, *private_values):
    assert result.returncode == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert set(body) == {"status", "code", "message"}
    assert body["status"] == "UNAVAILABLE" and body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert "Traceback" not in result.stderr
    for value in private_values:
        assert str(value) not in result.stderr


def test_archive_review_reports_mixed_outcomes_and_preserves_originals_receipts_and_cursor(tmp_path):
    path = tmp_path / "raw.sqlite3"
    readings = [
        [{"signalKey": "numeric.alpha", "value": -5, "unit": "count", "quality": "GOOD"},
         {"signalKey": "numeric.beta", "value": None, "unit": "volt", "quality": "MISSING"}],
        [{"signalKey": "numeric.alpha", "value": -11, "unit": "count", "quality": "GOOD"}],
        [{"signalKey": "numeric.alpha", "value": 1, "unit": "count", "quality": "GOOD"},
         {"signalKey": "numeric.beta", "value": 3, "unit": "volt", "quality": "GOOD"}],
    ]
    rows = [sample(i, signals=signals) for i, signals in enumerate(readings)]
    save(path, rows)
    before = db_state(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rules = [rule(module=True), rule("beta", tag="numeric.beta", unit="volt", lower=0, upper=5),
             rule("peer", device=PEER)]
    result = review(path, config(rules))
    assert result["schemaVersion"] == "numeric-limit-review-1"
    assert result["evaluationMode"] == "OFFLINE_ARCHIVE_REVIEW"
    assert result["referenceTimeBasis"] == "EXPLICIT_REPLAY_UTC"
    assert datetime.fromisoformat(result["evaluatedAtUtc"].replace("Z", "+00:00")) == REFERENCE
    assert result["selectionStatus"] == "HAS_SAMPLES" and set(result["appliedRuleIds"]) == {"alpha", "beta"}
    assert result["counts"] == {"WITHIN_LIMITS": 2, "BELOW_LOWER": 1, "ABOVE_UPPER": 1, "UNAVAILABLE": 2}
    assert len(result["results"]) == 6
    keyed = {(entry["sourceEvidence"]["sequence"], entry["ruleId"]): entry for entry in result["results"]}
    assert [keyed[i, "alpha"]["outcome"] for i in range(3)] == ["WITHIN_LIMITS", "BELOW_LOWER", "ABOVE_UPPER"]
    missing = keyed[0, "beta"]["sourceEvidence"]
    absent = keyed[1, "beta"]["sourceEvidence"]
    assert missing["present"] is True and missing["reading"] == {"value": None, "unit": "volt", "quality": "MISSING"}
    assert absent["present"] is False and absent["reading"] is None
    evidence = keyed[0, "alpha"]["sourceEvidence"]
    assert evidence["reading"] == {"value": -5, "unit": "count", "quality": "GOOD"}
    assert type(evidence["reading"]["value"]) is int
    assert evidence["observedAtUtc"] == rows[0]["observedAtUtc"]
    assert evidence["receivedAtUtc"] == (AT + timedelta(seconds=4)).isoformat()
    assert evidence["equipmentId"] == "STORED-EQUIPMENT" and evidence["moduleId"] == "STORED-MODULE"
    assert evidence["source"] == "SIMULATOR" and evidence["runId"] == RUN
    selection = result["archiveSelection"]
    assert selection["kind"] == "RAW_ARCHIVE_HISTORY" and selection["sourceCoverage"] == "NOT_ASSERTED"
    assert selection["matchedBatches"] == 3 and selection["scannedBatches"] == 3
    assert selection["window"]["bounds"] == "[from,to)"
    assert selection["limits"]["matchingBatches"] == 64 and selection["limits"]["signals"] == 16
    assert "batches" not in selection and "tags" not in selection
    assert result["configSha256"] == canonical_digest(result["configSnapshot"])
    assert result["configSnapshot"]["rules"][0]["target"]["moduleId"] == "STORED-MODULE"
    assert db_state(path) == before and hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("kind", ["MISSING", "BAD", "STALE", "ABSENT", "WRONG_UNIT", "OLD_SOURCE"])
def test_archive_review_never_counts_invalid_or_unavailable_evidence_as_within_limits(tmp_path, kind):
    path = tmp_path / "raw.sqlite3"
    reading = {"signalKey": "numeric.alpha", "value": -5, "unit": "count", "quality": "GOOD"}
    observed = AT.isoformat()
    if kind in {"MISSING", "BAD", "STALE"}:
        reading.update(value=None, quality=kind)
    elif kind == "ABSENT": reading["signalKey"] = "some.other.tag"
    elif kind == "WRONG_UNIT": reading["unit"] = "volt"
    else: observed = (AT - timedelta(seconds=60)).isoformat()
    save(path, [sample(signals=[reading], observed=observed)])
    result = review(path, start=AT - timedelta(seconds=60))
    assert result["counts"] == {"WITHIN_LIMITS": 0, "BELOW_LOWER": 0, "ABOVE_UPPER": 0, "UNAVAILABLE": 1}
    assert result["results"][0]["outcome"] == "UNAVAILABLE"


def test_rule_and_config_versions_change_results_without_changing_source_evidence(tmp_path):
    path = tmp_path / "raw.sqlite3"
    save(path, [sample(signals=[{"signalKey": "numeric.alpha", "value": 9, "unit": "count", "quality": "GOOD"}])])
    first = review(path, config([rule(lower=0, upper=10)]))
    second = review(path, config([rule(lower=0, upper=8, version="2")], version="2"))
    a, b = first["results"][0], second["results"][0]
    assert a["outcome"] == "WITHIN_LIMITS" and b["outcome"] == "ABOVE_UPPER"
    assert a["ruleVersion"] == "1" and b["ruleVersion"] == "2"
    assert first["configVersion"] == "1" and second["configVersion"] == "2"
    assert first["configSha256"] != second["configSha256"] and a["ruleSha256"] != b["ruleSha256"]
    assert a["sourceEvidence"] == b["sourceEvidence"]
    assert a["projectionEvidenceSha256"] == b["projectionEvidenceSha256"]
    assert a["ruleSnapshot"]["upperBound"] == 10 and b["ruleSnapshot"]["upperBound"] == 8
    assert first["configSnapshot"]["rules"][0]["upperBound"] == 10


def test_same_rule_version_content_change_is_detectable_and_json_layout_does_not_change_digest(tmp_path):
    path = tmp_path / "raw.sqlite3"
    save(path, [sample()])
    data = config()
    first = review(path, data)
    changed = copy.deepcopy(data)
    changed["rules"][0]["lowerBound"] = -4
    second = review(path, changed)
    assert first["configVersion"] == second["configVersion"]
    assert first["results"][0]["ruleVersion"] == second["results"][0]["ruleVersion"]
    assert first["configSha256"] != second["configSha256"]
    assert first["results"][0]["ruleSha256"] != second["results"][0]["ruleSha256"]
    from aegis.limit_rules import load_rule_config, review_archive
    config_path = tmp_path / "reordered.json"
    reordered = dict(reversed(list(data.items())))
    reordered["rules"] = [dict(reversed(list(data["rules"][0].items())))]
    config_path.write_text(json.dumps(reordered, indent=5), encoding="utf-8")
    equivalent = review_archive(load_rule_config(config_path), path, DEVICE, RUN,
        AT.isoformat(), (AT + timedelta(seconds=3)).isoformat(), evaluated_at=REFERENCE)
    assert first["configSha256"] == equivalent["configSha256"]


def test_empty_selection_retains_full_config_and_is_not_vacuous_normal(tmp_path):
    path = tmp_path / "raw.sqlite3"
    save(path, [sample()])
    result = review(path, start=AT + timedelta(seconds=10), end=AT + timedelta(seconds=11))
    assert result["selectionStatus"] == "NO_SAMPLES" and result["results"] == []
    assert result["counts"] == {key: 0 for key in OUTCOMES}
    assert result["configSnapshot"]["rules"] and result["configSha256"] == canonical_digest(result["configSnapshot"])
    assert not any(value in {"NORMAL", "PASS"} for value in result.values() if isinstance(value, str))


def test_no_applicable_device_rules_is_a_typed_error_before_missing_archive_access(tmp_path):
    from aegis.limit_rules import LimitRuleError
    path = tmp_path / "PRIVATE-absent.sqlite3"
    with pytest.raises(LimitRuleError) as error:
        review(path, config([rule(device=PEER)]))
    assert error.value.code == "NO_APPLICABLE_RULES" and not path.exists()


def test_archive_reader_batch_limit_is_preserved_without_partial_rule_output(tmp_path):
    from aegis.limit_rules import LimitRuleError
    path = tmp_path / "raw.sqlite3"
    save(path, [sample(0), sample(1)])
    before = db_state(path)
    with pytest.raises((RawQueryError, LimitRuleError)) as error:
        review(path, max_batches=1)
    assert error.value.code == "RAW_QUERY_LIMIT"
    assert db_state(path) == before


def test_run_sequence_gap_is_copied_as_reader_metadata_without_time_gap_invention(tmp_path):
    path = tmp_path / "raw.sqlite3"
    save(path, [sample(3), sample(4)], gap=(0, 2))
    before = db_state(path)
    result = review(path, end=AT + timedelta(seconds=6), reference=AT + timedelta(seconds=7))
    assert result["archiveSelection"]["sequenceGaps"] == {"scope": "SELECTED_RUN_SEQUENCE_ONLY", "items": [
        {"firstSequence": 0, "lastSequence": 2, "reason": "REPLAY_OVERFLOW"}]}
    assert db_state(path) == before


def test_actual_cli_prints_only_versioned_json_and_does_not_modify_archive(tmp_path):
    archive = tmp_path / "raw.sqlite3"
    config_path = tmp_path / "rules.json"
    save(archive, [sample()])
    config_path.write_text(json.dumps(config()), encoding="utf-8")
    before = db_state(archive)
    result = run_cli(config_path, archive)
    assert result.returncode == 0 and result.stderr == ""
    body = json.loads(result.stdout)
    assert body["schemaVersion"] == "numeric-limit-review-1"
    assert body["evaluationMode"] == "OFFLINE_ARCHIVE_REVIEW" and body["referenceTimeBasis"] == "EXPLICIT_REPLAY_UTC"
    assert body["counts"]["WITHIN_LIMITS"] == 1 and body["results"][0]["outcome"] == "WITHIN_LIMITS"
    assert re.fullmatch(r"[0-9a-f]{64}", body["configSha256"])
    assert body["configSnapshot"]["configId"] == "synthetic-limits"
    assert db_state(archive) == before
    # SQLite may manage its ordinary WAL sidecars even for a read-only connection.
    assert {p.name for p in tmp_path.iterdir()} <= {"raw.sqlite3", "raw.sqlite3-wal", "raw.sqlite3-shm", "rules.json"}


def test_actual_cli_default_reference_is_explicitly_system_utc(tmp_path):
    archive = tmp_path / "raw.sqlite3"
    config_path = tmp_path / "rules.json"
    save(archive, [sample()])
    config_path.write_text(json.dumps(config()), encoding="utf-8")
    before = datetime.now(timezone.utc)
    result = run_cli(config_path, archive, reference=None)
    after = datetime.now(timezone.utc)
    assert result.returncode == 0 and result.stderr == ""
    body = json.loads(result.stdout)
    clock = datetime.fromisoformat(body["evaluatedAtUtc"].replace("Z", "+00:00"))
    assert before <= clock <= after
    assert body["referenceTimeBasis"] == "SYSTEM_UTC" and body["evaluationMode"] == "OFFLINE_ARCHIVE_REVIEW"


@pytest.mark.parametrize("bad", ["PRIVATE-invalid-reference", "2026-11-09T12:00:00", "0001-01-01T00:00:00+01:00"])
def test_actual_cli_invalid_reference_has_fixed_error_json_and_empty_stdout(tmp_path, bad):
    archive = tmp_path / "raw.sqlite3"
    config_path = tmp_path / "rules.json"
    save(archive, [sample()])
    config_path.write_text(json.dumps(config()), encoding="utf-8")
    assert_cli_error(run_cli(config_path, archive, reference=bad), "INVALID_EVALUATION_TIME", bad, archive, config_path)


@pytest.mark.parametrize("kind", ["missing", "malformed", "duplicate", "directory", "oversized"])
def test_actual_cli_invalid_config_is_a_fixed_error_without_file_contents_or_paths(tmp_path, kind):
    archive = tmp_path / "raw.sqlite3"
    config_path = tmp_path / "PRIVATE-config.json"
    if kind == "malformed": config_path.write_text("PRIVATE malformed content", encoding="utf-8")
    elif kind == "duplicate": config_path.write_text('{"configId":"one","configId":"PRIVATE-two"}', encoding="utf-8")
    elif kind == "directory": config_path.mkdir()
    elif kind == "oversized": config_path.write_text(" " * (64 * 1024 + 1), encoding="utf-8")
    result = run_cli(config_path, archive)
    assert_cli_error(result, "INVALID_RULE_CONFIG", "PRIVATE", archive, config_path)
    assert not archive.exists()


@pytest.mark.parametrize("kind,code", [("missing", "RAW_ARCHIVE_UNAVAILABLE"),
                                       ("invalid", "RAW_ARCHIVE_INVALID"),
                                       ("limit", "RAW_QUERY_LIMIT"), ("bad_window", "INVALID_QUERY")])
def test_actual_cli_preserves_reader_failures_without_partial_output(tmp_path, kind, code):
    archive = tmp_path / "PRIVATE-raw.sqlite3"
    config_path = tmp_path / "rules.json"
    config_path.write_text(json.dumps(config()), encoding="utf-8")
    extras = []
    if kind != "missing": save(archive, [sample(0), sample(1)])
    if kind == "invalid":
        with sqlite3.connect(archive) as connection:
            connection.execute("UPDATE raw_batches SET payload_json=?", ('{"PRIVATE":"invalid"}',))
    elif kind == "limit": extras = ["--max-batches", "1"]
    elif kind == "bad_window": extras = ["--to-utc", AT.isoformat()]
    before = db_state(archive) if archive.exists() else None
    result = run_cli(config_path, archive, extras=extras)
    assert_cli_error(result, code, archive, "PRIVATE")
    if before is not None: assert db_state(archive) == before
    else: assert not archive.exists()


def test_actual_cli_no_applicable_rules_is_not_empty_normal_success(tmp_path):
    archive = tmp_path / "PRIVATE-raw.sqlite3"
    config_path = tmp_path / "rules.json"
    config_path.write_text(json.dumps(config([rule(device=PEER)])), encoding="utf-8")
    assert_cli_error(run_cli(config_path, archive), "NO_APPLICABLE_RULES", archive)
    assert not archive.exists()


def test_maximum_matching_batch_rule_product_is_bounded_to_1024_explicit_results(tmp_path):
    path = tmp_path / "raw.sqlite3"
    save(path, [sample(i) for i in range(64)], receipt=AT + timedelta(seconds=70))
    data = config([rule(f"rule{i:02d}", max_age=100000) for i in range(16)])
    result = review(path, data, end=AT + timedelta(seconds=64), reference=AT + timedelta(seconds=80))
    assert len(result["results"]) == 1024
    assert result["counts"] == {"WITHIN_LIMITS": 1024, "BELOW_LOWER": 0, "ABOVE_UPPER": 0, "UNAVAILABLE": 0}
    assert len(result["appliedRuleIds"]) == 16 and result["archiveSelection"]["matchedBatches"] == 64
