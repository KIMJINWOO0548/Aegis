"""Configurable limits retain their declaration and raw projection evidence."""

import copy
import hashlib
import importlib
import json
import math
from datetime import datetime, timedelta, timezone

import pytest


AT = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
DEVICE = "LIMIT-SYNTHETIC-01"
RUN = "11111111-1111-4111-8111-111111111111"


def module():
    return importlib.import_module("aegis.limit_rules")


def rule(**changes):
    value = {"ruleId": "numeric.bounds", "ruleVersion": "1.0", "target": {
        "deviceId": DEVICE, "signalKey": "numeric.alpha"}, "unit": "count",
        "lowerBound": 0, "upperBound": 10, "maxAgeMs": 1000}
    value.update(changes)
    return value


def config(rules=None):
    return {"schemaVersion": "numeric-limit-rules-1", "configId": "synthetic-limits",
            "configVersion": "1.0", "rules": rules if rules is not None else [rule()]}


def batch(value=5, *, quality="GOOD", present=True, unit="count", observed=None, received=None):
    return {"protocolVersion": "1.1", "source": "SIMULATOR", "deviceId": DEVICE, "runId": RUN,
        "sequence": 7, "observedAtUtc": observed or AT.isoformat(),
        "receivedAtUtc": received or (AT + timedelta(milliseconds=100)).isoformat(),
        "deviceStateVersion": 3, "modeId": "PRODUCTION", "equipmentId": "STORED-EQUIPMENT",
        "moduleId": "STORED-MODULE", "signals": [{"signalKey": "numeric.alpha", "present": present,
            "reading": {"value": value, "unit": unit, "quality": quality} if present else None}]}


def evaluate(declaration=None, evidence=None, *, at=None):
    m = module()
    return m.evaluate_rule(m.LimitRule.model_validate(declaration or rule()),
        batch() if evidence is None else evidence, evaluated_at=at or AT + timedelta(milliseconds=500))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize("value,outcome", [
    (-1, "BELOW_LOWER"), (0, "WITHIN_LIMITS"), (10, "WITHIN_LIMITS"), (11, "ABOVE_UPPER"),
    (math.nextafter(0.0, -math.inf), "BELOW_LOWER"),
    (math.nextafter(10.0, math.inf), "ABOVE_UPPER"),
])
def test_bounds_are_inclusive_without_implicit_rounding(value, outcome):
    result = evaluate(evidence=batch(value))
    assert result["outcome"] == result["reasonCode"] == outcome
    assert result["sourceEvidence"]["reading"]["value"] == value


@pytest.mark.parametrize("declaration,value,outcome", [
    (rule(lowerBound=None, upperBound=10), -100, "WITHIN_LIMITS"),
    (rule(lowerBound=0, upperBound=None), 100, "WITHIN_LIMITS"),
    (rule(lowerBound=3, upperBound=3), 3, "WITHIN_LIMITS"),
    (rule(lowerBound=3, upperBound=3), 2, "BELOW_LOWER"),
    (rule(lowerBound=-10, upperBound=0), -5, "WITHIN_LIMITS"),
])
def test_one_sided_equal_and_negative_generic_limits_do_not_reuse_flow_ranges(declaration, value, outcome):
    assert evaluate(declaration, batch(value))["outcome"] == outcome


@pytest.mark.parametrize("change", [
    {"lowerBound": None, "upperBound": None}, {"lowerBound": 11, "upperBound": 10},
    {"upperBound": True}, {"lowerBound": False}, {"upperBound": "10"},
    {"lowerBound": float("nan")}, {"upperBound": float("inf")}, {"lowerBound": -float("inf")},
    {"upperBound": 9007199254740993}, {"maxAgeMs": True}, {"maxAgeMs": "1000"},
    {"maxAgeMs": 0}, {"maxAgeMs": 3600001}, {"unit": ""}, {"unit": "   "}, {"unit": "bad\nunit"},
    {"ruleId": ""}, {"ruleVersion": ""}, {"unexpected": "ignored?"},
    {"target": {"deviceId": DEVICE, "signalKey": "numeric.alpha", "moduleId": "PARTIAL"}},
])
def test_rule_declarations_reject_ambiguous_or_lossy_configuration(change):
    m = module()
    with pytest.raises(ValueError):
        m.LimitRule.model_validate(rule(**change))


@pytest.mark.parametrize("rules", [[], [rule()] * 2,
    [rule(ruleId=f"rule{i}") for i in range(17)]])
def test_rule_sets_reject_empty_duplicate_or_excessive_rules(rules):
    m = module()
    with pytest.raises(ValueError):
        m.LimitRuleConfig.model_validate(config(rules))


def test_rules_and_nested_targets_are_frozen_without_mutable_rule_lists():
    m = module()
    original = m.LimitRuleConfig.model_validate(config())
    assert isinstance(original.rules, tuple)
    with pytest.raises((ValueError, AttributeError, TypeError)):
        original.rules[0].upperBound = 99
    with pytest.raises((ValueError, AttributeError, TypeError)):
        original.rules[0].target.deviceId = "OTHER"
    with pytest.raises((ValueError, AttributeError, TypeError)):
        original.rules.append(original.rules[0])
    assert original.rules[0].upperBound == 10 and original.rules[0].target.deviceId == DEVICE


@pytest.mark.parametrize("quality,value,reason", [
    ("MISSING", None, "QUALITY_MISSING"), ("BAD", None, "QUALITY_BAD"),
    ("BAD", 5, "QUALITY_BAD"), ("STALE", 5, "QUALITY_STALE"),
])
def test_unusable_quality_is_unavailable_even_for_an_in_range_value(quality, value, reason):
    result = evaluate(evidence=batch(value, quality=quality))
    assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == reason
    assert result["sourceEvidence"]["present"] is True
    assert result["sourceEvidence"]["reading"] == {"value": value, "unit": "count", "quality": quality}


def test_absent_and_not_projected_are_distinct_and_neither_is_within_limits():
    missing = evaluate(evidence=batch(present=False))
    assert missing["outcome"] == "UNAVAILABLE" and missing["reasonCode"] == "SIGNAL_ABSENT"
    assert missing["sourceEvidence"]["present"] is False and missing["sourceEvidence"]["reading"] is None
    not_projected = batch()
    not_projected["signals"][0]["signalKey"] = "numeric.other"
    result = evaluate(evidence=not_projected)
    assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == "TAG_NOT_PROJECTED"
    assert result["sourceEvidence"]["present"] is None


@pytest.mark.parametrize("unit", ["COUNT", " count", "volt"])
def test_unit_comparison_is_exact_without_conversion_or_trimming(unit):
    result = evaluate(evidence=batch(unit=unit))
    assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == "UNIT_MISMATCH"
    assert result["sourceEvidence"]["reading"]["unit"] == unit


def test_device_and_stored_hierarchy_targets_never_use_current_configuration_substitutes():
    wrong_device = batch(); wrong_device["deviceId"] = "OTHER-DEVICE"
    assert evaluate(evidence=wrong_device)["reasonCode"] == "TARGET_MISMATCH"
    scoped = rule(target={"deviceId": DEVICE, "signalKey": "numeric.alpha",
                          "equipmentId": "STORED-EQUIPMENT", "moduleId": "STORED-MODULE"})
    assert evaluate(scoped)["outcome"] == "WITHIN_LIMITS"
    assert evaluate(scoped)["targetScope"] == "EQUIPMENT_MODULE_TAG"
    for field in ("equipmentId", "moduleId"):
        evidence = batch(); evidence[field] = "CURRENT-CONFIGURATION-OTHER"
        result = evaluate(scoped, evidence)
        assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == "TARGET_MISMATCH"
    assert evaluate()["targetScope"] == "DEVICE_TAG"


def test_freshness_uses_source_time_with_exact_inclusive_max_age_not_fresh_receipt():
    at = AT + timedelta(seconds=1)
    assert evaluate(at=at)["outcome"] == "WITHIN_LIMITS"
    stale = evaluate(at=at + timedelta(microseconds=1))
    assert stale["outcome"] == "UNAVAILABLE" and stale["reasonCode"] == "STALE_SOURCE"
    fresh_receipt = batch(received=(AT + timedelta(seconds=5)).isoformat())
    assert evaluate(evidence=fresh_receipt, at=AT + timedelta(seconds=5))["reasonCode"] == "STALE_SOURCE"


@pytest.mark.parametrize("observed,received,at,reason", [
    (AT + timedelta(seconds=1), AT + timedelta(seconds=2), AT, "SOURCE_TIME_IN_FUTURE"),
    (AT, AT + timedelta(seconds=1), AT, "RECEIPT_TIME_IN_FUTURE"),
    (AT + timedelta(milliseconds=100), AT, AT + timedelta(milliseconds=500), "SOURCE_AFTER_RECEIPT"),
])
def test_clock_order_errors_are_unavailable_without_clamping_or_source_time_substitution(observed, received, at, reason):
    evidence = batch(observed=observed.isoformat(), received=received.isoformat())
    result = evaluate(evidence=evidence, at=at)
    assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == reason
    assert result["sourceEvidence"]["observedAtUtc"] == observed.isoformat()
    assert result["sourceEvidence"]["receivedAtUtc"] == received.isoformat()


@pytest.mark.parametrize("fault", ["missing_time", "missing_sequence", "unsupported_source", "duplicate_tag",
                                    "bad_number", "bool_number", "good_null", "bad_quality", "utc_overflow"])
def test_invalid_evidence_is_unavailable_and_does_not_fabricate_original_metadata(fault):
    evidence = batch()
    if fault == "missing_time": evidence.pop("observedAtUtc")
    elif fault == "missing_sequence": evidence.pop("sequence")
    elif fault == "unsupported_source": evidence["source"] = "REAL"
    elif fault == "duplicate_tag": evidence["signals"] *= 2
    elif fault == "bad_number": evidence["signals"][0]["reading"]["value"] = float("nan")
    elif fault == "bool_number": evidence["signals"][0]["reading"]["value"] = True
    elif fault == "good_null": evidence["signals"][0]["reading"]["value"] = None
    elif fault == "bad_quality": evidence["signals"][0]["reading"]["quality"] = "UNRECOGNIZED"
    else: evidence["observedAtUtc"] = "0001-01-01T00:00:00+01:00"
    result = evaluate(evidence=evidence)
    assert result["outcome"] == "UNAVAILABLE" and result["reasonCode"] == "INVALID_EVIDENCE"
    assert result["sourceEvidence"] is None and result["projectionEvidenceSha256"] is None


@pytest.mark.parametrize("reference", ["2026-11-09T12:00:00", "not-a-date", "9999-12-31T23:00:00-02:00"])
def test_invalid_evaluation_clock_is_a_typed_error_without_a_fabricated_reference(reference):
    m = module()
    with pytest.raises(m.LimitRuleError) as error:
        evaluate(at=reference)
    assert error.value.code == "INVALID_EVALUATION_TIME"


def test_version_or_same_version_content_change_is_auditable_and_never_mutates_prior_results():
    evidence = batch(9)
    before = copy.deepcopy(evidence)
    first = evaluate(evidence=evidence)
    frozen_first = copy.deepcopy(first)
    second = evaluate(rule(ruleVersion="2.0", upperBound=8), evidence)
    reused = evaluate(rule(upperBound=8), evidence)
    assert first["outcome"] == "WITHIN_LIMITS" and second["outcome"] == reused["outcome"] == "ABOVE_UPPER"
    assert first["ruleId"] == second["ruleId"] == reused["ruleId"]
    assert first["ruleVersion"] == reused["ruleVersion"] == "1.0" and second["ruleVersion"] == "2.0"
    assert len({first["ruleSha256"], second["ruleSha256"], reused["ruleSha256"]}) == 3
    assert first["sourceEvidence"] == second["sourceEvidence"] == reused["sourceEvidence"]
    assert first["projectionEvidenceSha256"] == second["projectionEvidenceSha256"] == reused["projectionEvidenceSha256"]
    assert first == frozen_first and evidence == before
    evidence["signals"][0]["reading"]["value"] = 1
    assert first["sourceEvidence"]["reading"]["value"] == 9


def test_evidence_is_original_projection_and_hash_excludes_derived_age_and_rule_version():
    evidence = batch(9007199254740994)
    declaration = rule(lowerBound=None, upperBound=9007199254740994)
    result = evaluate(declaration, evidence)
    assert result["schemaVersion"] == "numeric-limit-evaluation-1"
    assert result["evaluationMode"] == "OFFLINE_ARCHIVE_REVIEW"
    assert result["ruleSnapshot"]["target"]["signalKey"] == "numeric.alpha"
    assert result["ruleSha256"] == digest(result["ruleSnapshot"])
    source = result["sourceEvidence"]
    assert type(source["reading"]["value"]) is int and source["reading"]["value"] == 9007199254740994
    for key in ("deviceId", "equipmentId", "moduleId", "protocolVersion", "source", "runId", "sequence",
                "observedAtUtc", "receivedAtUtc", "deviceStateVersion", "modeId"):
        assert source[key] == evidence[key]
    assert result["projectionEvidenceSha256"] == digest({k: v for k, v in source.items() if k != "sourceAgeMs"})
    later = evaluate(declaration, evidence, at=AT + timedelta(seconds=3))
    assert later["outcome"] == "UNAVAILABLE" and later["reasonCode"] == "STALE_SOURCE"
    assert later["sourceEvidence"]["sourceAgeMs"] != source["sourceAgeMs"]
    assert later["projectionEvidenceSha256"] == result["projectionEvidenceSha256"]


@pytest.mark.parametrize("content", [
    b'{"schemaVersion":"numeric-limit-rules-1","configVersion":"1","configVersion":"2"}',
    b'{"value":NaN}', b'\xff', b' ' * 65537,
], ids=["duplicate-key", "nonfinite-json", "invalid-utf8", "over-64kib"])
def test_loader_rejects_duplicate_nonfinite_invalid_utf8_and_oversized_inputs_without_echo(tmp_path, content):
    m = module()
    path = tmp_path / "PRIVATE-config.json"
    path.write_bytes(content)
    with pytest.raises(m.LimitRuleError) as error:
        m.load_rule_config(path)
    assert error.value.code == "INVALID_RULE_CONFIG" and str(path) not in str(error.value)


def test_loader_canonicalizes_configuration_not_input_whitespace_and_keeps_immutable_models(tmp_path):
    m = module()
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps(config()))
    second.write_bytes(b'\xef\xbb\xbf' + json.dumps(config(), sort_keys=True, indent=2).encode())
    a, b = m.load_rule_config(first), m.load_rule_config(second)
    assert a == b and a.model_dump(mode="json") == b.model_dump(mode="json")
