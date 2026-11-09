"""Stateless configured numeric limits for an explicitly offline archive review."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from aegis.collector import RawSignal, SIGNAL_UNITS
from aegis.contracts import ModeId, Number, UtcTimestamp
from aegis.raw_history import RawQueryError, read_raw_archive


MAX_CONFIG_BYTES = 65536
MAX_RULES = 16
MAX_AGE_MS = 3600000
OUTCOMES = ("WITHIN_LIMITS", "BELOW_LOWER", "ABOVE_UPPER", "UNAVAILABLE")
IDENTIFIER = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
SIGNAL_KEY = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
UNIT = Annotated[str, Field(strict=True, min_length=1, max_length=32, pattern=r"^[^\x00-\x1f\x7f]+$")]
TIMESTAMP = TypeAdapter(UtcTimestamp)


class LimitRuleError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _config_error():
    return LimitRuleError("INVALID_RULE_CONFIG", "The numeric limit configuration is invalid or unavailable.")


def _exact_number(value):
    if type(value) not in (int, float):
        raise ValueError("A bound must be a JSON number.")
    if type(value) is int:
        try:
            exact = float(value) == value
        except OverflowError:
            exact = False
        if not exact:
            raise ValueError("Integer bounds must be exactly representable as floats.")
    return value


BOUND = Annotated[Number, BeforeValidator(_exact_number)]


class RuleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    deviceId: IDENTIFIER
    signalKey: SIGNAL_KEY
    equipmentId: IDENTIFIER | None = None
    moduleId: IDENTIFIER | None = None

    @model_validator(mode="after")
    def paired_hierarchy(self):
        if (self.equipmentId is None) != (self.moduleId is None):
            raise ValueError("Specify both equipment and module, or neither.")
        return self


class LimitRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ruleId: IDENTIFIER
    ruleVersion: IDENTIFIER
    target: RuleTarget
    unit: UNIT
    lowerBound: BOUND | None = None
    upperBound: BOUND | None = None
    maxAgeMs: Annotated[int, Field(strict=True, ge=1, le=MAX_AGE_MS)]

    @field_validator("unit")
    @classmethod
    def nonblank_unit(cls, value):
        if not value.strip():
            raise ValueError("A unit cannot be blank.")
        return value

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.lowerBound is None and self.upperBound is None:
            raise ValueError("At least one bound is required.")
        if self.lowerBound is not None and self.upperBound is not None and self.lowerBound > self.upperBound:
            raise ValueError("Lower bound cannot exceed upper bound.")
        return self


class LimitRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schemaVersion: Literal["numeric-limit-rules-1"]
    configId: IDENTIFIER
    configVersion: IDENTIFIER
    rules: Annotated[tuple[LimitRule, ...], Field(min_length=1, max_length=MAX_RULES)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({rule.ruleId for rule in self.rules}) != len(self.rules):
            raise ValueError("Rule IDs must be unique within a configuration.")
        return self


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Non-finite JSON number.")


def load_rule_config(path):
    try:
        path = Path(path)
        if not path.is_file():
            raise _config_error()
        with path.open("rb") as source:
            encoded = source.read(MAX_CONFIG_BYTES + 1)
        if len(encoded) > MAX_CONFIG_BYTES:
            raise _config_error()
        value = json.loads(encoded.decode("utf-8-sig"), object_pairs_hook=_object, parse_constant=_constant)
        return LimitRuleConfig.model_validate(value)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        raise _config_error() from None


def _timestamp_string(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("An original timestamp string is required.")
    return value


ORIGINAL_TIME = Annotated[UtcTimestamp, BeforeValidator(_timestamp_string)]


class _Projection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    signalKey: SIGNAL_KEY
    present: Annotated[bool, Field(strict=True)]
    reading: dict[str, Any] | None

    @model_validator(mode="after")
    def valid_reading(self):
        if not self.present:
            if self.reading is not None:
                raise ValueError("An absent tag has no reading.")
            return self
        value = self.reading
        if (value is None or set(value) != {"value", "unit", "quality"}
                or not isinstance(value["unit"], str) or not isinstance(value["quality"], str)
                or (value["value"] is not None and type(value["value"]) not in (int, float))):
            raise ValueError("Invalid projected reading.")
        RawSignal.model_validate(dict(value, signalKey=self.signalKey))
        if self.signalKey in SIGNAL_UNITS and value["unit"] != SIGNAL_UNITS[self.signalKey]:
            raise ValueError("Invalid raw flow-profile unit.")
        return self


class _EvidenceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocolVersion: Literal["1.1"]
    source: Literal["SIMULATOR"]
    deviceId: IDENTIFIER
    runId: UUID
    sequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    observedAtUtc: ORIGINAL_TIME
    receivedAtUtc: ORIGINAL_TIME
    deviceStateVersion: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    modeId: ModeId
    equipmentId: Annotated[str, Field(strict=True, max_length=128)] | None
    moduleId: Annotated[str, Field(strict=True, max_length=128)] | None
    signals: Annotated[tuple[_Projection, ...], Field(min_length=1, max_length=16)]

    @field_validator("runId", "modeId", mode="before")
    @classmethod
    def original_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("Original JSON identity strings are required.")
        return value

    @model_validator(mode="after")
    def unique_projection(self):
        if len({item.signalKey for item in self.signals}) != len(self.signals):
            raise ValueError("Duplicate projected tag.")
        return self


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reference(value):
    try:
        if isinstance(value, str) and len(value) > 64:
            raise ValueError
        return TIMESTAMP.validate_python(value)
    except (ValueError, TypeError, OverflowError):
        raise LimitRuleError("INVALID_EVALUATION_TIME", "A valid UTC-aware evaluation reference is required.") from None


def evaluate_rule(rule, batch, *, evaluated_at):
    if not isinstance(rule, LimitRule):
        raise _config_error()
    reference = _reference(evaluated_at)
    snapshot = rule.model_dump(mode="json")
    result = {"schemaVersion": "numeric-limit-evaluation-1", "evaluationMode": "OFFLINE_ARCHIVE_REVIEW",
        "evaluatedAtUtc": reference.isoformat(), "ruleId": rule.ruleId, "ruleVersion": rule.ruleVersion,
        "ruleSha256": _digest(snapshot), "ruleSnapshot": snapshot,
        "targetScope": "DEVICE_TAG" if rule.target.equipmentId is None else "EQUIPMENT_MODULE_TAG",
        "outcome": "UNAVAILABLE", "reasonCode": "INVALID_EVIDENCE",
        "sourceEvidence": None, "projectionEvidenceSha256": None}
    try:
        if not isinstance(batch, dict):
            raise ValueError
        parsed = _EvidenceBatch.model_validate(batch)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return result
    item = next((value for value in parsed.signals if value.signalKey == rule.target.signalKey), None)
    original = next((value for value in batch["signals"] if value["signalKey"] == rule.target.signalKey), None)
    reading = dict(original["reading"]) if original is not None and original["reading"] is not None else None
    evidence = {key: value for key, value in batch.items() if key != "signals"}
    evidence.update(signalKey=rule.target.signalKey, present=item.present if item is not None else None, reading=reading)
    result["projectionEvidenceSha256"] = _digest(evidence)
    age = reference - parsed.observedAtUtc
    evidence["sourceAgeMs"] = age.total_seconds() * 1000
    result["sourceEvidence"] = evidence
    reason = None
    if (parsed.deviceId != rule.target.deviceId or (rule.target.equipmentId is not None
            and (parsed.equipmentId != rule.target.equipmentId or parsed.moduleId != rule.target.moduleId))):
        reason = "TARGET_MISMATCH"
    elif item is None:
        reason = "TAG_NOT_PROJECTED"
    elif not item.present:
        reason = "SIGNAL_ABSENT"
    elif reading["quality"] != "GOOD":
        reason = "QUALITY_" + reading["quality"]
    elif reading["unit"] != rule.unit:
        reason = "UNIT_MISMATCH"
    elif parsed.observedAtUtc > reference:
        reason = "SOURCE_TIME_IN_FUTURE"
    elif parsed.receivedAtUtc > reference:
        reason = "RECEIPT_TIME_IN_FUTURE"
    elif parsed.observedAtUtc > parsed.receivedAtUtc:
        reason = "SOURCE_AFTER_RECEIPT"
    elif age > timedelta(milliseconds=rule.maxAgeMs):
        reason = "STALE_SOURCE"
    if reason is not None:
        result["reasonCode"] = reason
        return result
    value = reading["value"]
    outcome = ("BELOW_LOWER" if rule.lowerBound is not None and value < rule.lowerBound else
               "ABOVE_UPPER" if rule.upperBound is not None and value > rule.upperBound else "WITHIN_LIMITS")
    result.update(outcome=outcome, reasonCode=outcome)
    return result


def review_archive(config, archive, device_id, run_id, from_utc, to_utc, *, max_batches=64, evaluated_at=None):
    if not isinstance(config, LimitRuleConfig):
        raise _config_error()
    rules = [rule for rule in config.rules if rule.target.deviceId == device_id]
    if not rules:
        raise LimitRuleError("NO_APPLICABLE_RULES", "No configured numeric rules apply to the selected device.")
    reference = _reference(datetime.now(timezone.utc) if evaluated_at is None else evaluated_at)
    tags = list(dict.fromkeys(rule.target.signalKey for rule in rules))
    archive_result = read_raw_archive(archive, device_id, run_id, from_utc, to_utc,
                                     signals=",".join(tags), max_batches=max_batches)
    results = [evaluate_rule(rule, batch, evaluated_at=reference)
               for batch in archive_result["batches"] for rule in rules]
    snapshot = config.model_dump(mode="json")
    return {"schemaVersion": "numeric-limit-review-1", "evaluationMode": "OFFLINE_ARCHIVE_REVIEW",
        "referenceTimeBasis": "SYSTEM_UTC" if evaluated_at is None else "EXPLICIT_REPLAY_UTC",
        "evaluatedAtUtc": reference.isoformat(), "configId": config.configId, "configVersion": config.configVersion,
        "configSha256": _digest(snapshot), "configSnapshot": snapshot,
        "appliedRuleIds": [rule.ruleId for rule in rules],
        "archiveSelection": {key: value for key, value in archive_result.items() if key not in {"batches", "tags"}},
        "selectionStatus": "HAS_SAMPLES" if results else "NO_SAMPLES",
        "counts": {outcome: sum(row["outcome"] == outcome for row in results) for outcome in OUTCOMES},
        "results": results}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise LimitRuleError("INVALID_QUERY", "Command arguments are invalid.")


def main(argv=None):
    parser = _Parser(description="Read-only offline archive review with configured numeric limits.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--from-utc", required=True)
    parser.add_argument("--to-utc", required=True)
    parser.add_argument("--max-batches", type=int, default=64)
    parser.add_argument("--evaluated-at-utc")
    try:
        args = parser.parse_args(argv)
        report = review_archive(load_rule_config(args.config), args.archive, args.device, args.run_id,
            args.from_utc, args.to_utc, max_batches=args.max_batches, evaluated_at=args.evaluated_at_utc)
    except (LimitRuleError, RawQueryError) as error:
        print(json.dumps({"status": "UNAVAILABLE", "code": error.code, "message": error.message}), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
