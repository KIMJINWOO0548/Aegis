"""Check D0a wire contracts and reference SQL syntax without creating a database."""

from pathlib import Path
import sqlite3
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aegis import contracts
from scripts.generate_contracts import at, check, enums

MODEL_NAMES = (
    "Observation", "DeviceHealth", "Warning", "ApiError", "StructuredEvent",
    "TimeWindow", "TrendGap", "TrendPoint", "TrendSeries", "WarningMarker",
    "TrendResponse", "WarningTimeline",
)
REQUIRED_PATHS = {
    "Observation": "observationSchema.required",
    "DeviceHealth": "modelOutput.required",
    "ApiError": "apiErrorRequiredFields",
    "StructuredEvent": "observability.eventRequiredFields",
    "TrendResponse": "trendContract.trendResponseRequiredFields",
}


def normalize(schema, definitions):
    """Compare schema meaning across Pydantic JSON Schema and OpenAPI 3.0."""
    schema = dict(schema)
    if "$ref" in schema:
        reference = schema.pop("$ref").rsplit("/", 1)[-1]
        schema = dict(definitions[reference], **schema)
    if "anyOf" in schema:
        variants = schema.pop("anyOf")
        assert len(variants) == 2 and {"type": "null"} in variants, "unexpected union"
        schema.update(next(item for item in variants if item != {"type": "null"}))
        schema["nullable"] = True
        return normalize(schema, definitions)
    if "const" in schema:
        schema["enum"] = [schema.pop("const")]
    result = {}
    for key in ("type", "format", "enum", "nullable", "required", "properties", "items",
                "additionalProperties", "minimum", "maximum", "minItems", "maxItems"):
        if key not in schema:
            continue
        value = schema[key]
        if key == "properties":
            value = {name: normalize(field, definitions) for name, field in value.items()}
        elif isinstance(value, dict):
            value = normalize(value, definitions)
        elif key in ("required", "enum"):
            value = sorted(value)
        result[key] = value
    return result


def validate_contract(contract, api):
    schemas = api["components"]["schemas"]
    assert api["info"]["version"] == contract["contractVersion"], "OpenAPI version drift"
    for name, values in enums(contract).items():
        assert set(values) == {member.value for member in getattr(contracts, name)}, f"{name} Python enum drift"
        assert set(schemas[name]["enum"]) == set(values), f"{name} OpenAPI enum drift"
    assert contract["trendContract"]["signalKeys"] == contract["observationSchema"]["signalKeys"], "signal enum drift"
    for name in MODEL_NAMES:
        model = getattr(contracts, name)
        expected = model.model_json_schema()
        assert normalize(expected, expected.get("$defs", {})) == normalize(schemas[name], schemas), f"{name} OpenAPI/model schema drift"
        if name in REQUIRED_PATHS:
            required = {field for field, definition in model.model_fields.items() if definition.is_required()}
            assert required == set(at(contract, REQUIRED_PATHS[name])), f"{name} YAML required-field drift"
    optional = {field for field, definition in contracts.StructuredEvent.model_fields.items() if not definition.is_required()}
    assert optional == set(contract["observability"]["eventOptionalFields"]), "event optional-field drift"
    routes = {f"{method.upper()} {path}" for path, item in api["paths"].items()
              for method in item if method in {"get", "post", "put", "delete", "patch", "head", "options"}}
    assert routes == set(contract["apiRoutesThinDemo"]), "thin-demo route drift"
    trend = api["paths"]["/v1/devices/{id}/trends"]["get"]
    parameters = {item["name"]: item["schema"] for item in trend["parameters"]}
    assert parameters["maxPoints"]["maximum"] == contract["trendContract"]["maxPointsPerSeries"], "trend limit drift"
    assert set(parameters["resolution"]["enum"]) == set(contract["trendContract"]["resolutions"]), "query resolution drift"
    assert trend["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TrendResponse")
    timeline = api["paths"]["/v1/warnings/{id}/timeline"]["get"]
    assert timeline["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/WarningTimeline")
    for path, item in api["paths"].items():
        for method in ("get", "post"):
            if method not in item:
                continue
            for code, response in item[method]["responses"].items():
                if code.isdigit() and int(code) >= 400:
                    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/ApiError"), f"{path} {code} error shape drift"
    contracts.Observation.model_validate(contract["observationExample"])
    contracts.StructuredEvent.model_validate(contract["observability"]["sampleEvent"])
    contracts.TrendResponse.model_validate(contract["trendContract"]["sampleResponse"])
    assert contract["otPilot"]["modbusFunctionCodesAllowed"] == [1, 2, 3, 4]
    assert set(contract["otPilot"]["modbusFunctionCodesAllowed"]).isdisjoint(contract["otPilot"]["modbusFunctionCodesForbidden"])
    assert contract["otPilot"]["opcuaServicesForbidden"] == ["Write", "Call"]
    assert contract["simulatorOnly"] is True
    assert contract["advisoryManagement"]["distinctFrom"] == "PLC_PROCESS_OR_SAFETY_ALARM"


def main():
    contract = yaml.safe_load((ROOT / "AEGIS_CONTRACT_v3_4.yaml").read_text(encoding="utf-8"))
    api = yaml.safe_load((ROOT / "OPENAPI_THIN_DEMO_v3_4.yaml").read_text(encoding="utf-8"))
    assert not check(ROOT, contract), "generated Python/C# contract drift; run scripts/generate_contracts.py"
    validate_contract(contract, api)
    with sqlite3.connect(":memory:") as connection:
        connection.executescript((ROOT / "SQL_SCHEMA_REFERENCE_v3_3.sql").read_text(encoding="utf-8"))
    print("PASS: YAML/generated enums, required fields, nested OpenAPI schemas, thin-demo routes and samples")
    print("PASS: reference SQL syntax only; no product database or hardware connection")


if __name__ == "__main__":
    main()
