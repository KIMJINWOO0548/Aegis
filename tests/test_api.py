import importlib.util
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


TOKEN = "test-only-local-token-" + "x" * 32
DEVICE = "/v1/devices/EQ-SUPPLY-01"
WINDOW = {
    "signals": "actualFlowLpm,setpointFlowLpm",
    "fromUtc": "2026-10-16T00:00:00Z",
    "toUtc": "2026-10-16T00:05:00Z",
}


@pytest.fixture
def app_factory():
    assert importlib.util.find_spec("aegis.api") is not None, "D0a local API is not implemented"
    from aegis.api import create_app

    return create_app


@pytest.fixture
def client(app_factory, tmp_path):
    return TestClient(
        app_factory(TOKEN, log_path=tmp_path / "api.jsonl"),
        base_url="http://127.0.0.1:8765",
        headers={"X-Aegis-Token": TOKEN},
    )


def assert_error(response, status, code=None):
    assert response.status_code == status
    body = response.json()
    assert {"code", "message", "requestId"} <= body.keys()
    assert body["requestId"] == response.headers["X-Request-Id"]
    UUID(body["requestId"])
    UUID(response.headers["X-Trace-Id"])
    if code:
        assert body["code"] == code


@pytest.mark.parametrize("token", ["", " " * 32, "short", "x" * 31])
def test_startup_requires_explicit_strong_token(app_factory, token):
    with pytest.raises(ValueError):
        app_factory(token)


def test_health_identifies_mock_scope_and_no_active_model(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["schemaVersion"] == "3.4.0"
    assert response.json()["stage"] == "D0a"
    assert response.json()["mock"] is True
    health = client.get(f"{DEVICE}/health").json()
    assert health["modelLifecycle"] == "NO_MODEL"
    assert health["observationEvaluation"] == "INSUFFICIENT_DATA"
    assert health["modelVersion"] is None
    assert health["normalReferencePercentile"] is None


@pytest.mark.parametrize("header", [None, "wrong-token", "x" * 32])
def test_requests_require_matching_token(app_factory, header):
    headers = {} if header is None else {"X-Aegis-Token": header}
    with TestClient(app_factory(TOKEN), base_url="http://127.0.0.1", headers=headers) as client:
        assert_error(client.get("/v1/health"), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "127.0.0.1:99999"])
def test_nonlocal_host_is_rejected(client, host):
    assert_error(client.get("/v1/health", headers={"Host": host}), 400, "INVALID_HOST")


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:8765", "null", ""])
def test_all_browser_origins_are_rejected(client, origin):
    assert_error(client.get("/v1/health", headers={"Origin": origin}), 403, "BROWSER_ORIGIN_FORBIDDEN")


def test_response_ids_are_safe_and_request_ids_are_unique(client):
    trace = str(uuid4())
    first = client.get("/v1/health", headers={"X-Trace-Id": trace, "X-Request-Id": "user-secret"})
    second = client.get("/v1/health", headers={"X-Trace-Id": "password=unsafe-secret"})
    assert first.headers["X-Trace-Id"] == trace
    UUID(second.headers["X-Trace-Id"])
    assert first.headers["X-Request-Id"] != second.headers["X-Request-Id"]
    UUID(first.headers["X-Request-Id"])


def test_read_routes_follow_contract_models(client):
    from aegis.contracts import DeviceHealth, Observation, Warning, WarningTimeline

    assert client.get("/v1/devices").json() == [{"deviceId": "EQ-SUPPLY-01", "readOnly": True, "source": "SIMULATOR"}]
    observations = client.get(f"{DEVICE}/observations").json()
    assert observations
    for row in observations:
        Observation.model_validate(row)
        assert row["deviceId"] == "EQ-SUPPLY-01"
    DeviceHealth.model_validate(client.get(f"{DEVICE}/health").json())
    warnings = client.get("/v1/warnings").json()
    assert [row["id"] for row in warnings] == ["warn-demo-02"]
    Warning.model_validate(warnings[0])
    timeline = client.get("/v1/warnings/warn-demo-02/timeline").json()
    WarningTimeline.model_validate(timeline)
    assert timeline["warningId"] == "warn-demo-02"
    assert timeline["events"][0]["eventId"] == "evt-demo-042"
    assert timeline["events"][0]["warningId"] == "warn-demo-02"
    assert not any(event["eventName"] == "WARNING_ACKNOWLEDGED" for event in timeline["events"])


@pytest.mark.parametrize("path", [
    "/v1/devices/unknown/health",
    "/v1/devices/unknown/observations",
    "/v1/devices/unknown/trends",
    "/v1/warnings/unknown/timeline",
    "/unknown",
])
def test_unknown_identifiers_never_return_mock_success(client, path):
    assert_error(client.get(path, params=WINDOW), 404, "NOT_FOUND")


@pytest.mark.parametrize("path", ["/v1/warnings/warn-demo-02/ack", "/v1/lab/scenarios/DEG-02/start"])
def test_unimplemented_mutation_gates_do_not_claim_success(client, path):
    assert_error(client.post(path, headers={"Idempotency-Key": "request-1"}), 501, "GATE_NOT_IMPLEMENTED")
    assert client.get("/v1/warnings").json()[0]["state"] == "OPEN"


@pytest.mark.parametrize("overrides", [
    {"signals": ""},
    {"signals": "unknown"},
    {"signals": "flowErrorEwmaLpm"},
    {"signals": "actualFlowLpm,setpointFlowLpm,motorCurrentAmp"},
    {"signals": "actualFlowLpm,actualFlowLpm"},
    {"fromUtc": "2026-10-16T00:00:00"},
    {"fromUtc": "garbage-password-secret"},
    {"fromUtc": "2026-10-16"},
    {"toUtc": "2026-10-16T00:00:00Z"},
    {"toUtc": "2026-10-15T23:59:00Z"},
    {"toUtc": "2026-10-16T01:00:01Z"},
    {"maxPoints": "0"},
    {"maxPoints": "1501"},
    {"maxPoints": "1.5"},
    {"resolution": "invalid"},
])
def test_trend_rejects_invalid_or_unbounded_queries(client, overrides):
    response = client.get(f"{DEVICE}/trends", params=WINDOW | overrides)
    assert_error(response, 422, "INVALID_QUERY")
    assert "garbage-password-secret" not in response.text


@pytest.mark.parametrize("missing", ["signals", "fromUtc", "toUtc"])
def test_trend_requires_all_bounds(client, missing):
    params = {key: value for key, value in WINDOW.items() if key != missing}
    assert_error(client.get(f"{DEVICE}/trends", params=params), 422, "INVALID_QUERY")


@pytest.mark.parametrize("resolution", ["1m", "1h"])
def test_trend_does_not_pretend_aggregation_is_implemented(client, resolution):
    assert_error(client.get(f"{DEVICE}/trends", params=WINDOW | {"resolution": resolution}), 501, "GATE_NOT_IMPLEMENTED")


def test_trend_preserves_requested_window_and_exposes_fixture_gaps(client):
    from aegis.contracts import TrendResponse

    response = client.get(f"{DEVICE}/trends", params=WINDOW)
    assert response.status_code == 200
    body = response.json()
    model = TrendResponse.model_validate(body)
    assert model.window.fromUtc == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert model.window.toUtc == datetime(2026, 10, 16, 0, 5, tzinfo=timezone.utc)
    assert body["resolution"] == "raw"
    assert body["source"] == "SIMULATOR"
    assert body["isComplete"] is False
    assert any(gap["reason"] == "SIGNAL_MISSING" for gap in body["gaps"])
    assert len(model.series) == 2
    assert model.warningMarkers[0].warningId == "warn-demo-02"
    for series in model.series:
        assert series.points
        assert all(model.window.fromUtc <= p.t <= model.window.toUtc for p in series.points)
        assert not any(datetime(2026, 10, 16, 0, 2, 20, tzinfo=timezone.utc) <= p.t < datetime(2026, 10, 16, 0, 2, 25, tzinfo=timezone.utc) for p in series.points)


def test_trend_small_limit_keeps_bounds_and_marks_omitted_data(client):
    response = client.get(f"{DEVICE}/trends", params=WINDOW | {"maxPoints": 1})
    body = response.json()
    assert response.status_code == 200
    assert all(len(series["points"]) == 1 for series in body["series"])
    assert body["isComplete"] is False
    assert any(gap["reason"] == "POINT_LIMIT" for gap in body["gaps"])
    assert body["window"]["toUtc"].startswith("2026-10-16T00:05:00")


def test_trend_outside_fixture_reports_empty_series_with_gap(client):
    response = client.get(f"{DEVICE}/trends", params=WINDOW | {"fromUtc": "2026-10-17T00:00:00Z", "toUtc": "2026-10-17T00:05:00Z"})
    body = response.json()
    assert response.status_code == 200
    assert all(series["points"] == [] for series in body["series"])
    assert body["isComplete"] is False
    assert body["warningMarkers"] == []
    assert body["gaps"][0]["reason"] == "MOCK_DATA_UNAVAILABLE"


def test_offset_timestamps_normalize_to_utc(client):
    response = client.get(f"{DEVICE}/trends", params=WINDOW | {"fromUtc": "2026-10-16T09:00:00+09:00", "toUtc": "2026-10-16T09:05:00+09:00"})
    assert response.status_code == 200
    assert response.json()["window"]["fromUtc"].startswith("2026-10-16T00:00:00")


def test_errors_and_logs_do_not_include_untrusted_inputs(app_factory, tmp_path):
    path = tmp_path / "api.jsonl"
    app = app_factory(TOKEN, log_path=path)

    @app.get("/test-failure")
    def failure():
        raise RuntimeError("password=internal-secret")

    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Aegis-Token": TOKEN}) as client:
        response = client.get("/test-failure", headers={"X-Trace-Id": "password=header-secret", "X-Request-Id": "password=request-secret"})
        assert_error(response, 500, "INTERNAL_ERROR")
        assert "internal-secret" not in response.text
        invalid = client.get(f"{DEVICE}/trends", params=WINDOW | {"maxPoints": "password=query-secret"})
        assert_error(invalid, 422, "INVALID_QUERY")
        assert "query-secret" not in invalid.text
    text = path.read_text(encoding="utf-8")
    assert TOKEN not in text
    assert "secret" not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert len(events) == 2
    assert all(event["eventName"] == "API_REQUEST_FAILED" for event in events)
    assert all(event["outcome"] == "FAILED" for event in events)
    assert events[0]["requestId"] == response.headers["X-Request-Id"]
    assert events[0]["traceId"] == response.headers["X-Trace-Id"]


def test_unwritable_diagnostics_are_visible_without_exposing_contents(app_factory, tmp_path):
    # An existing directory cannot be opened as the diagnostic file.
    app = app_factory(TOKEN, log_path=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        denied = client.get("/v1/health")
        assert_error(denied, 401, "UNAUTHORIZED")
        assert denied.headers["X-Aegis-Logging-Status"] == "DEGRADED"
        health = client.get("/v1/health", headers={"X-Aegis-Token": TOKEN})
        assert health.status_code == 200
        assert health.json()["status"] == "DEGRADED"
        assert health.headers["X-Aegis-Logging-Status"] == "DEGRADED"
        assert str(tmp_path) not in health.text
