"""Version references reject content reuse and preserve explicit historical slots."""

import copy
import hashlib
import importlib
import json

import pytest

from aegis.limit_rules import LimitRuleConfig


def module():
    return importlib.import_module("aegis.rule_versions")


def rule(version="1", **changes):
    value = {"ruleId": "numeric.bounds", "ruleVersion": version,
        "target": {"deviceId": "LOCK-SYNTHETIC-01", "signalKey": "numeric.alpha"},
        "unit": "count", "lowerBound": 0, "upperBound": 10, "maxAgeMs": 1000}
    value.update(changes)
    return value


def config(rules=None, *, version="1", config_id="synthetic-lock"):
    return LimitRuleConfig.model_validate({"schemaVersion": "numeric-limit-rules-1", "configId": config_id,
        "configVersion": version, "rules": rules if rules is not None else [rule()]})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def body(lock):
    return lock.model_dump(mode="json")


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")


def assert_error(code, operation):
    m = module()
    with pytest.raises(m.RuleVersionError) as error:
        operation()
    assert error.value.code == code and str(error.value)
    assert "PRIVATE" not in str(error.value)


def test_new_reference_binds_schema_config_identity_full_effective_rule_and_digest(tmp_path):
    m = module(); cfg = config()
    lock = m.build_rule_lock(cfg)
    value = body(lock)
    assert value["schemaVersion"] == "numeric-rule-lock-1"
    assert value["ruleSchemaVersion"] == "numeric-limit-rules-1"
    assert value["evaluationSchemaVersion"] == "numeric-limit-evaluation-1"
    assert value["configId"] == cfg.configId and len(value["rules"]) == 1
    entry = value["rules"][0]
    assert entry == {"ruleId": "numeric.bounds", "ruleVersion": "1",
        "ruleSha256": digest(cfg.rules[0].model_dump(mode="json")), "ruleSnapshot": cfg.rules[0].model_dump(mode="json")}
    path = tmp_path / "lock.json"; write(path, value)
    loaded = m.load_rule_lock(path)
    verification = m.verify_rule_lock(cfg, loaded)
    assert verification == {"schemaVersion": "numeric-rule-lock-check-1", "status": "VERIFIED",
        "lockSha256": digest(value), "configId": cfg.configId, "lockRuleCount": 1, "checkedRuleCount": 1,
        "checkedRules": [{key: entry[key] for key in ("ruleId", "ruleVersion", "ruleSha256")}]}


@pytest.mark.parametrize("change", [
    {"upperBound": 8}, {"lowerBound": -1}, {"unit": "volt"}, {"maxAgeMs": 2000},
    {"target": {"deviceId": "OTHER-DEVICE", "signalKey": "numeric.alpha"}},
    {"target": {"deviceId": "LOCK-SYNTHETIC-01", "signalKey": "numeric.other"}},
    {"target": {"deviceId": "LOCK-SYNTHETIC-01", "signalKey": "numeric.alpha",
                "equipmentId": "SAVED-EQUIPMENT", "moduleId": "SAVED-MODULE"}},
], ids=["upper", "lower", "unit", "age", "device", "tag", "hierarchy"])
def test_same_key_change_is_rejected_even_when_config_version_is_bumped(change):
    m = module(); lock = m.build_rule_lock(config()); changed = config([rule(**change)], version="2")
    before = copy.deepcopy(body(lock))
    assert_error("RULE_VERSION_CONFLICT", lambda: m.verify_rule_lock(changed, lock))
    assert_error("RULE_VERSION_CONFLICT", lambda: m.build_rule_lock(changed, previous=lock))
    assert body(lock) == before


def test_new_version_is_rejected_until_explicitly_appended_and_prior_snapshot_is_retained(tmp_path):
    m = module(); original = m.build_rule_lock(config())
    path = tmp_path / "old-lock.json"; write(path, body(original)); old_bytes = path.read_bytes()
    next_config = config([rule("2", upperBound=8)], version="2")
    assert_error("RULE_VERSION_UNREGISTERED", lambda: m.verify_rule_lock(next_config, original))
    extended = m.build_rule_lock(next_config, previous=original)
    assert len(extended.rules) == 2
    assert body(extended)["rules"][0] == body(original)["rules"][0]
    assert m.verify_rule_lock(next_config, extended)["status"] == "VERIFIED"
    assert m.verify_rule_lock(config(), extended)["status"] == "VERIFIED"
    assert path.read_bytes() == old_bytes
    assert body(m.build_rule_lock(next_config, previous=extended)) == body(extended)


def test_removed_rules_remain_registered_and_snapshot_models_are_frozen():
    m = module()
    old_config = config([rule(), rule(ruleId="historical.other")])
    previous = m.build_rule_lock(old_config)
    before = copy.deepcopy(body(previous))
    extended = m.build_rule_lock(config([rule("2", upperBound=8)], version="2"), previous=previous)
    assert len(extended.rules) == 3 and body(previous) == before
    assert {(r.ruleId, r.ruleVersion) for r in extended.rules} == {
        ("numeric.bounds", "1"), ("numeric.bounds", "2"), ("historical.other", "1")}
    assert isinstance(extended.rules, tuple)
    with pytest.raises((ValueError, TypeError, AttributeError)):
        extended.rules[0].ruleSnapshot.upperBound = 77
    with pytest.raises((ValueError, TypeError, AttributeError)):
        extended.rules[0].ruleSnapshot.target.deviceId = "OTHER"
    assert body(previous) == before


def test_config_namespace_mismatch_is_not_silently_initialized_as_another_registry():
    m = module(); lock = m.build_rule_lock(config()); other = config(config_id="different-config")
    assert_error("RULE_LOCK_SCOPE_MISMATCH", lambda: m.verify_rule_lock(other, lock))
    assert_error("RULE_LOCK_SCOPE_MISMATCH", lambda: m.build_rule_lock(other, previous=lock))


def test_every_current_rule_including_other_devices_must_match_the_reference():
    m = module(); peer = rule(ruleId="peer", target={"deviceId": "PEER", "signalKey": "numeric.alpha"})
    lock = m.build_rule_lock(config([rule(), peer]))
    changed_peer = dict(peer, upperBound=7)
    assert_error("RULE_VERSION_CONFLICT", lambda: m.verify_rule_lock(config([rule(), changed_peer]), lock))
    assert_error("RULE_VERSION_UNREGISTERED", lambda: m.verify_rule_lock(config([rule(), rule(ruleId="new")]), lock))


def test_format_entry_order_and_equivalent_numeric_spelling_normalize_to_same_reference(tmp_path):
    m = module(); cfg = config([rule("2"), rule("1", ruleId="another")])
    original = m.build_rule_lock(cfg); reordered = body(original)
    reordered["rules"].reverse()
    path = tmp_path / "reordered.json"
    path.write_bytes(b'\xef\xbb\xbf' + json.dumps(dict(reversed(list(reordered.items()))), indent=3).encode())
    loaded = m.load_rule_lock(path)
    assert body(loaded) == body(original)
    assert m.verify_rule_lock(cfg, loaded)["lockSha256"] == m.verify_rule_lock(cfg, original)["lockSha256"]
    equivalent = config([rule("2", upperBound=10.0), rule("1", ruleId="another", upperBound=10.0)])
    assert body(m.build_rule_lock(equivalent)) == body(original)


@pytest.mark.parametrize("fault", ["digest", "inner_id", "inner_version", "duplicate", "schema", "rule_schema",
                                  "evaluation_schema", "unknown", "bad_bound"])
def test_invalid_references_fail_closed_with_fixed_errors(tmp_path, fault):
    m = module(); value = body(m.build_rule_lock(config())); entry = value["rules"][0]
    if fault == "digest": entry["ruleSha256"] = "0" * 64
    elif fault == "inner_id":
        entry["ruleSnapshot"]["ruleId"] = "different"; entry["ruleSha256"] = digest(entry["ruleSnapshot"])
    elif fault == "inner_version":
        entry["ruleSnapshot"]["ruleVersion"] = "9"; entry["ruleSha256"] = digest(entry["ruleSnapshot"])
    elif fault == "duplicate": value["rules"].append(copy.deepcopy(entry))
    elif fault == "schema": value["schemaVersion"] = "unknown"
    elif fault == "rule_schema": value["ruleSchemaVersion"] = "unknown"
    elif fault == "evaluation_schema": value["evaluationSchemaVersion"] = "unknown"
    elif fault == "unknown": value["unexpected"] = "PRIVATE-input"
    else: entry["ruleSnapshot"]["upperBound"] = True
    path = tmp_path / "PRIVATE-lock.json"; write(path, value)
    assert_error("INVALID_RULE_LOCK", lambda: m.load_rule_lock(path))


@pytest.mark.parametrize("content,code", [
    (b'{"configId":"one","configId":"PRIVATE-two"}', "INVALID_RULE_LOCK"),
    (b'{"rules":NaN}', "INVALID_RULE_LOCK"), (b'\xff', "INVALID_RULE_LOCK"),
    (b' ' * (256 * 1024 + 1), "RULE_LOCK_LIMIT"),
], ids=["duplicate-json-key", "nonfinite-json", "invalid-utf8", "over-256kib"])
def test_loader_resource_and_json_errors_do_not_echo_data(tmp_path, content, code):
    m = module(); path = tmp_path / "PRIVATE-lock.json"; path.write_bytes(content)
    assert_error(code, lambda: m.load_rule_lock(path))


def test_loader_accepts_exact_file_byte_limit_and_never_creates_missing_files(tmp_path):
    m = module(); value = body(m.build_rule_lock(config()))
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "exact.json"; path.write_bytes(encoded + b' ' * (256 * 1024 - len(encoded)))
    assert m.verify_rule_lock(config(), m.load_rule_lock(path))["status"] == "VERIFIED"
    missing = tmp_path / "PRIVATE-missing" / "lock.json"
    assert_error("INVALID_RULE_LOCK", lambda: m.load_rule_lock(missing))
    assert not missing.parent.exists()


def history(m, count):
    value = body(m.build_rule_lock(config()))
    template = value["rules"][0]
    value["rules"] = []
    for i in range(count):
        entry = copy.deepcopy(template); entry["ruleVersion"] = f"v{i:03d}"
        entry["ruleSnapshot"]["ruleVersion"] = entry["ruleVersion"]
        entry["ruleSha256"] = digest(entry["ruleSnapshot"]); value["rules"].append(entry)
    return value


def test_256_historical_entries_work_and_257th_is_rejected_without_eviction(tmp_path):
    m = module(); value = history(m, 256); path = tmp_path / "history.json"; write(path, value)
    loaded = m.load_rule_lock(path); before = copy.deepcopy(body(loaded)); old_bytes = path.read_bytes()
    assert len(loaded.rules) == 256
    assert m.verify_rule_lock(config([rule("v255")]), loaded)["status"] == "VERIFIED"
    assert_error("RULE_LOCK_LIMIT", lambda: m.build_rule_lock(config([rule("v256")]), previous=loaded))
    assert body(loaded) == before and path.read_bytes() == old_bytes


def test_builder_checks_its_utf8_output_and_final_newline_before_returning_a_lock(monkeypatch):
    m = module(); lock = m.build_rule_lock(config())
    length = len(json.dumps(body(lock), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) + 1
    monkeypatch.setattr(m, "MAX_LOCK_BYTES", length)
    assert body(m.build_rule_lock(config())) == body(lock)
    monkeypatch.setattr(m, "MAX_LOCK_BYTES", length - 1)
    assert_error("RULE_LOCK_LIMIT", lambda: m.build_rule_lock(config()))


def test_loaded_reference_is_one_immutable_snapshot_not_a_later_path_reread(tmp_path):
    m = module(); path = tmp_path / "reference.json"
    first = m.build_rule_lock(config()); write(path, body(first)); captured = m.load_rule_lock(path)
    alternate = m.build_rule_lock(config([rule(upperBound=8)])); write(path, body(alternate))
    assert m.verify_rule_lock(config(), captured)["lockSha256"] == digest(body(first))
    assert_error("RULE_VERSION_CONFLICT", lambda: m.verify_rule_lock(config(), m.load_rule_lock(path)))
    # A fresh unsigned reference can define a different trust baseline; no authentication claim.
    assert m.verify_rule_lock(config([rule(upperBound=8)]), m.load_rule_lock(path))["status"] == "VERIFIED"
