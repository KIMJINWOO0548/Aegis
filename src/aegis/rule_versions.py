"""Operator-owned immutable rule references, without implicit registration or writes."""

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis.limit_rules import (
    IDENTIFIER, LimitRule, LimitRuleConfig, LimitRuleError,
    _constant, _digest, _object, load_rule_config,
)


MAX_LOCK_ENTRIES = 256
MAX_LOCK_BYTES = 256 * 1024
DIGEST = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class RuleVersionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _invalid():
    return RuleVersionError("INVALID_RULE_LOCK", "The rule version reference is invalid or unavailable.")


def _limit():
    return RuleVersionError("RULE_LOCK_LIMIT", "The rule version reference exceeds a supported resource limit.")


def _conflict():
    return RuleVersionError("RULE_VERSION_CONFLICT", "Rule contents conflict with the registered ID and version.")


class RegisteredRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ruleId: IDENTIFIER
    ruleVersion: IDENTIFIER
    ruleSha256: DIGEST
    ruleSnapshot: LimitRule

    @model_validator(mode="after")
    def bind_identity_and_snapshot(self):
        snapshot = self.ruleSnapshot
        if (snapshot.ruleId != self.ruleId or snapshot.ruleVersion != self.ruleVersion
                or _digest(snapshot.model_dump(mode="json")) != self.ruleSha256):
            raise ValueError("Rule identity or digest does not match its effective snapshot.")
        return self


class RuleVersionLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schemaVersion: Literal["numeric-rule-lock-1"]
    ruleSchemaVersion: Literal["numeric-limit-rules-1"]
    evaluationSchemaVersion: Literal["numeric-limit-evaluation-1"]
    configId: IDENTIFIER
    rules: Annotated[tuple[RegisteredRule, ...], Field(min_length=1, max_length=MAX_LOCK_ENTRIES)]

    @model_validator(mode="after")
    def unique_sorted_keys(self):
        keys = [(entry.ruleId, entry.ruleVersion) for entry in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate rule version key.")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda entry: (entry.ruleId, entry.ruleVersion))))
        return self


def _encoded(lock):
    # A complete UTF8/LF artifact is formed and bounded before any stdout write.
    if len(lock.rules) > MAX_LOCK_ENTRIES:
        raise _limit()
    text = json.dumps(lock.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
    encoded = text.encode("utf-8") + b"\n"
    if len(encoded) > MAX_LOCK_BYTES:
        raise _limit()
    return encoded


def _require_lock(lock):
    if not isinstance(lock, RuleVersionLock):
        raise _invalid()
    _encoded(lock)
    return lock


def load_rule_lock(path):
    try:
        path = Path(path)
        if not path.is_file():
            raise _invalid()
        with path.open("rb") as source:
            encoded = source.read(MAX_LOCK_BYTES + 1)
        if len(encoded) > MAX_LOCK_BYTES:
            raise _limit()
        value = json.loads(encoded.decode("utf-8-sig"), object_pairs_hook=_object, parse_constant=_constant)
        if isinstance(value, dict) and isinstance(value.get("rules"), list) and len(value["rules"]) > MAX_LOCK_ENTRIES:
            raise _limit()
        return _require_lock(RuleVersionLock.model_validate(value))
    except RuleVersionError:
        raise
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        raise _invalid() from None


def _config(value):
    # A JSON boundary avoids __main__/named-module class duplication in both CLIs.
    try:
        if isinstance(value, LimitRuleConfig):
            return value
        if not isinstance(value, dict):
            raise ValueError
        return LimitRuleConfig.model_validate(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise RuleVersionError("INVALID_RULE_CONFIG", "The numeric limit configuration is invalid or unavailable.") from None


def _scope(config, lock):
    if config.configId != lock.configId:
        raise RuleVersionError("RULE_LOCK_SCOPE_MISMATCH", "The rule version reference belongs to a different configuration.")


def _entry(rule):
    snapshot = rule.model_dump(mode="json")
    return RegisteredRule(ruleId=rule.ruleId, ruleVersion=rule.ruleVersion,
                          ruleSha256=_digest(snapshot), ruleSnapshot=snapshot)


def build_rule_lock(config, previous=None):
    config = _config(config)
    entries = {}
    if previous is not None:
        previous = _require_lock(previous)
        _scope(config, previous)
        entries = {(entry.ruleId, entry.ruleVersion): entry for entry in previous.rules}
    for rule in config.rules:
        entry = _entry(rule)
        key = (entry.ruleId, entry.ruleVersion)
        if key in entries and entries[key].ruleSha256 != entry.ruleSha256:
            raise _conflict()
        entries.setdefault(key, entry)
    if len(entries) > MAX_LOCK_ENTRIES:
        raise _limit()
    lock = RuleVersionLock(schemaVersion="numeric-rule-lock-1", ruleSchemaVersion="numeric-limit-rules-1",
        evaluationSchemaVersion="numeric-limit-evaluation-1", configId=config.configId, rules=tuple(entries.values()))
    return _require_lock(lock)


def verify_rule_lock(config, lock):
    config, lock = _config(config), _require_lock(lock)
    _scope(config, lock)
    registered = {(entry.ruleId, entry.ruleVersion): entry for entry in lock.rules}
    checked = []
    for rule in config.rules:
        entry = registered.get((rule.ruleId, rule.ruleVersion))
        if entry is None:
            raise RuleVersionError("RULE_VERSION_UNREGISTERED", "A configured rule version is not registered in this reference.")
        if entry.ruleSha256 != _digest(rule.model_dump(mode="json")):
            raise _conflict()
        checked.append({"ruleId": entry.ruleId, "ruleVersion": entry.ruleVersion, "ruleSha256": entry.ruleSha256})
    return {"schemaVersion": "numeric-rule-lock-check-1", "status": "VERIFIED",
        "lockSha256": _digest(lock.model_dump(mode="json")), "configId": lock.configId,
        "lockRuleCount": len(lock.rules), "checkedRuleCount": len(checked), "checkedRules": checked}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise RuleVersionError("INVALID_QUERY", "Command arguments are invalid.")


def main(argv=None):
    parser = _Parser(description="Build a bounded operator-owned numeric rule version reference on stdout.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--previous-lock", type=Path)
    try:
        args = parser.parse_args(argv)
        previous = load_rule_lock(args.previous_lock) if args.previous_lock is not None else None
        lock = build_rule_lock(load_rule_config(args.config), previous=previous)
        encoded = _encoded(lock)
    except (RuleVersionError, LimitRuleError) as error:
        print(json.dumps({"status": "UNAVAILABLE", "code": error.code, "message": error.message}), file=sys.stderr)
        raise SystemExit(2) from None
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main()
