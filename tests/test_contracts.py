import copy
import importlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contract():
    return yaml.safe_load((ROOT / "AEGIS_CONTRACT_v3_4.yaml").read_text(encoding="utf-8"))


def models():
    return importlib.import_module("aegis.contracts")


def test_contract_examples_round_trip(contract):
    for name, payload in (
        ("Observation", contract["observationExample"]),
        ("StructuredEvent", contract["observability"]["sampleEvent"]),
        ("TrendResponse", contract["trendContract"]["sampleResponse"]),
    ):
        model = getattr(models(), name).model_validate(payload)
        assert type(model).model_validate_json(model.model_dump_json(exclude_none=True)) == model


@pytest.mark.parametrize("quality", ["MISSING", "BAD", "STALE"])
def test_non_good_observation_preserves_null(contract, quality):
    payload = dict(contract["observationExample"], value=None, quality=quality)
    assert json.loads(models().Observation.model_validate(payload).model_dump_json())["value"] is None


@pytest.mark.parametrize("change", [
    {"quality": "GOOD", "value": None},
    {"quality": "UNKNOWN"},
    {"signalKey": "not-a-signal"},
    {"schemaVersion": "3.3.0"},
    {"value": float("inf")},
    {"value": float("nan")},
    {"value": True},
    {"observedAtUtc": "2026-10-16T00:00:00"},
    {"observedAtUtc": 1792108800},
    {"observedAtUtc": "1792108800"},
    {"observedAtUtc": "2026-10-16 00:00:00Z"},
    {"unexpected": "secret"},
])
def test_observation_rejects_invalid_boundary_values(contract, change):
    with pytest.raises(ValidationError):
        models().Observation.model_validate(dict(contract["observationExample"], **change))


@pytest.mark.parametrize("field", ["value", "deviceId", "quality", "observedAtUtc"])
def test_observation_requires_fields_even_when_nullable(contract, field):
    payload = dict(contract["observationExample"])
    payload.pop(field)
    with pytest.raises(ValidationError):
        models().Observation.model_validate(payload)


def test_offset_time_is_normalized_to_utc(contract):
    payload = dict(contract["observationExample"], observedAtUtc="2026-10-16T09:00:00+09:00")
    observed = models().Observation.model_validate(payload)
    assert observed.observedAtUtc.utcoffset() == timedelta(0)
    assert json.loads(observed.model_dump_json())["observedAtUtc"] == "2026-10-16T00:00:00Z"


@pytest.mark.parametrize("change", [
    {"v": None, "quality": "GOOD"}, {"v": float("nan")},
    {"t": "2026-10-16T00:00:00"},
])
def test_trend_points_obey_observation_rules(change):
    payload = dict(t="2026-10-16T00:00:00Z", v=1.0, quality="GOOD")
    payload.update(change)
    with pytest.raises(ValidationError):
        models().TrendPoint.model_validate(payload)


def test_time_window_rejects_reversed_bounds():
    with pytest.raises(ValidationError):
        models().TimeWindow(fromUtc="2026-10-16T01:00:00Z", toUtc="2026-10-16T00:00:00Z")


@pytest.mark.parametrize("complete", ["false", 0])
def test_trend_requires_json_boolean_completeness(contract, complete):
    payload = dict(contract["trendContract"]["sampleResponse"], isComplete=complete)
    with pytest.raises(ValidationError):
        models().TrendResponse.model_validate(payload)


def test_model_lifecycle_does_not_accept_normal():
    with pytest.raises(ValidationError):
        models().DeviceHealth(schemaVersion="3.4.0", deviceId="EQ-SUPPLY-01", modelLifecycle="NORMAL",
                              observationEvaluation="NORMAL", modelVersion=None,
                              windowEndUtc="2026-10-16T00:00:00Z", evidenceSignalKeys=[], dataQuality="GOOD")


def test_extended_event_names_are_valid(contract):
    payload = dict(contract["observability"]["sampleEvent"], eventName="SOURCE_QUEUE_OVERFLOW")
    assert models().StructuredEvent.model_validate(payload).eventName == "SOURCE_QUEUE_OVERFLOW"


def test_event_rejects_non_finite_duration(contract):
    payload = dict(contract["observability"]["sampleEvent"], durationMs=float("inf"))
    with pytest.raises(ValidationError):
        models().StructuredEvent.model_validate(payload)


def test_timeline_requires_structured_events(contract):
    payload = dict(schemaVersion="3.4.0", warningId="warn-demo-02", generatedAtUtc="2026-10-16T00:01:30Z", events=[{}])
    with pytest.raises(ValidationError):
        models().WarningTimeline.model_validate(payload)
    payload["events"] = [contract["observability"]["sampleEvent"]]
    assert models().WarningTimeline.model_validate(payload).events[0].warningId == "warn-demo-02"


def test_yaml_and_openapi_match_runtime_contracts(contract):
    api = yaml.safe_load((ROOT / "OPENAPI_THIN_DEMO_v3_4.yaml").read_text(encoding="utf-8"))
    importlib.import_module("scripts.validate_v34").validate_contract(contract, api)


@pytest.mark.parametrize("mutation", ["enum", "required", "route", "nested", "event"])
def test_validator_detects_contract_openapi_drift(contract, mutation):
    api = yaml.safe_load((ROOT / "OPENAPI_THIN_DEMO_v3_4.yaml").read_text(encoding="utf-8"))
    if mutation == "enum":
        contract["observationQuality"].append("INVALID")
    elif mutation == "required":
        contract["observationSchema"]["required"].append("newRequiredField")
    elif mutation == "route":
        api["paths"].pop("/v1/health")
    elif mutation == "nested":
        api["components"]["schemas"]["TrendPoint"]["required"].remove("quality")
    else:
        contract["advisoryManagement"]["newEventNames"].append("NEW_EVENT")
    validator = importlib.import_module("scripts.validate_v34")
    with pytest.raises(AssertionError):
        validator.validate_contract(contract, api)


def test_generated_check_detects_manual_edit_and_yaml_drift(tmp_path, contract):
    generator = importlib.import_module("scripts.generate_contracts")
    outputs = generator.render(contract)
    for path, content in outputs.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    assert generator.check(tmp_path, contract) == []
    generated = tmp_path / "src/aegis/contracts_generated.py"
    generated.write_text(generated.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8")
    assert "src/aegis/contracts_generated.py" in generator.check(tmp_path, contract)
    changed = copy.deepcopy(contract)
    changed["observationSchema"]["required"].append("newField")
    assert "src/Aegis.Contracts/Contract.g.cs" in generator.check(tmp_path, changed)
