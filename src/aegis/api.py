"""Local read-only API: explicit fixed fixtures or independent emulator sources."""

import hmac
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aegis.contracts import (
    INITIAL_DEVICE,
    MAX_POINTS_PER_SERIES,
    THIN_DEMO_MAX_SIGNALS,
    THIN_DEMO_QUERY_MAX_SECONDS,
    VERSION,
    ApiError,
    DeviceHealth,
    Observation,
    TrendResponse,
    Warning,
    WarningTimeline,
)
from aegis.observability import make_event, write_event
from aegis.collector import Collector, load_sources


DEVICE_ID = INITIAL_DEVICE
WARNING_ID = "warn-demo-02"
FIXTURE_START = datetime(2026, 10, 16, tzinfo=timezone.utc)
FIXTURE_END = FIXTURE_START + timedelta(minutes=5)
WARNING_AT = FIXTURE_START + timedelta(seconds=90)
MISSING_FROM = FIXTURE_START + timedelta(seconds=140)
MISSING_TO = FIXTURE_START + timedelta(seconds=145)
GENERATED_AT = FIXTURE_END + timedelta(seconds=2)
SIGNALS = {
    "setpointFlowLpm": (10.0, "L/min"),
    "actualFlowLpm": (9.8, "L/min"),
    "supplyPressureKpa": (250.0, "kPa"),
    "valveCommandPct": (50.0, "%"),
    "valveFeedbackPct": (49.0, "%"),
    "motorCurrentAmp": (2.1, "A"),
    "temperatureC": (27.0, "degC"),
    "cycleCount": (42.0, "count"),
}
UUID_HEADER = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?\Z")


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request.state.reason_code = code
    body = ApiError(code=code, message=message, requestId=request.state.request_id)
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def _known_warning(warning_id: str) -> None:
    if warning_id != WARNING_ID:
        raise HTTPException(404)


def _utc(value: str) -> datetime:
    try:
        if len(value) < 20 or value[10] not in "Tt":
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise HTTPException(422) from None


def _source_context(collector: Collector | None) -> dict:
    """Describe current configuration, never provenance inferred for archived rows."""
    if collector is None:
        sources = [{"deviceId": DEVICE_ID, "declaredOrigin": "SYNTHETIC",
                    "producerKind": "FIXTURE", "acquisitionMethod": "FIXED_FIXTURE",
                    "sourceAccessMode": "NOT_APPLICABLE", "originEvidence": "BUILT_IN_FIXTURE",
                    "sourceTimestampPolicy": "FIXTURE_DEFINED", "sourceSequencePolicy": "NOT_EXPOSED"}]
    else:
        methods = {"snapshot": "SNAPSHOT_POLL", "replay": "SOURCE_BUFFER_REPLAY"}
        # Whitelist metadata: source configuration also contains private URLs and token names.
        sources = [{"deviceId": source.deviceId, "declaredOrigin": "SYNTHETIC",
                    "producerKind": "SIMULATOR", "acquisitionMethod": methods[source.transport],
                    "sourceAccessMode": "READ_ONLY", "originEvidence": "CONTRACT_DECLARED",
                    "sourceTimestampPolicy": "PRODUCER_REQUIRED", "sourceSequencePolicy": "PRODUCER_REQUIRED"}
                   for source in collector.sources]
    return {"schemaVersion": "source-context-1", "scope": "CURRENT_CONFIGURATION",
            "archivedRowProvenance": "NOT_INFERRED", "realEquipmentSupported": False,
            "hardRealtimeGuaranteed": False, "sources": sources}


def create_app(token: str, log_path: Path | None = None, *, collector: Collector | None = None) -> FastAPI:
    if not isinstance(token, str) or len(token.strip()) < 32 or "\r" in token or "\n" in token:
        raise ValueError("AEGIS_API_TOKEN must contain at least 32 characters.")
    expected_token = token.encode("utf-8")
    @asynccontextmanager
    async def lifespan(_app):
        if collector is not None:
            await collector.start()
        try:
            yield
        finally:
            if collector is not None:
                await collector.stop()

    app = FastAPI(title="Aegis Insight Local API", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.logging_degraded = False

    def _known_device(device_id):
        if device_id not in ({source.deviceId for source in collector.sources} if collector else {DEVICE_ID}):
            raise HTTPException(404)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        request.state.request_id = str(uuid4())
        supplied_trace = request.headers.getlist("x-trace-id")
        request.state.trace_id = (
            supplied_trace[0]
            if len(supplied_trace) == 1 and UUID_HEADER.fullmatch(supplied_trace[0])
            else str(uuid4())
        )
        hosts = request.headers.getlist("host")
        host_match = LOCAL_HOST.fullmatch(hosts[0].lower()) if len(hosts) == 1 else None
        tokens = request.headers.getlist("x-aegis-token")
        if not host_match or (host_match.group(1) and not 1 <= int(host_match.group(1)) <= 65535):
            response = _error(request, 400, "INVALID_HOST", "A loopback Host is required.")
        elif "origin" in request.headers:
            response = _error(request, 403, "BROWSER_ORIGIN_FORBIDDEN", "Browser clients are not supported in D0a.")
        elif len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode("utf-8"), expected_token):
            response = _error(request, 401, "UNAUTHORIZED", "A valid local API token is required.")
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = _error(request, 500, "INTERNAL_ERROR", "The local API could not complete this request.")
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Trace-Id"] = request.state.trace_id
        if log_path is not None and response.status_code >= 400:
            event = make_event(
                "API_REQUEST_FAILED", "local_api", request.state.trace_id,
                requestId=request.state.request_id,
                outcome="FAILED", severity="WARN",
                reasonCode=getattr(request.state, "reason_code", "REQUEST_FAILED"),
            )
            try:
                write_event(log_path, event)
                app.state.logging_degraded = False
            except OSError:
                app.state.logging_degraded = True
        if app.state.logging_degraded or (collector is not None and collector.logging_degraded):
            response.headers["X-Aegis-Logging-Status"] = "DEGRADED"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _exc):
        return _error(request, 422, "INVALID_QUERY", "Request parameters are invalid.")

    @app.exception_handler(StarletteHTTPException)
    async def request_error(request: Request, exc):
        messages = {
            404: ("NOT_FOUND", "The requested resource was not found."),
            405: ("METHOD_NOT_ALLOWED", "This request method is not supported."),
            422: ("INVALID_QUERY", "Request parameters are invalid."),
            501: ("GATE_NOT_IMPLEMENTED", "This capability is not implemented in D0a."),
        }
        code, message = messages.get(exc.status_code, ("REQUEST_FAILED", "The request could not be completed."))
        return _error(request, exc.status_code, code, message)

    @app.get("/v1/health")
    async def health(includeSourceContext: bool = Query(False, description="Include current source declarations; not archived-row provenance.")):
        degraded = app.state.logging_degraded or (collector is not None and (
            collector.logging_degraded or any(row["state"] != "CONNECTED" for row in collector.connections())))
        result = {"status": "DEGRADED" if degraded else "OK", "schemaVersion": VERSION,
                  "stage": "EMULATOR_LINK" if collector else "D0a", "mock": True,
                  "mode": "EMULATORS" if collector else "FIXTURE"}
        if includeSourceContext:
            result["sourceContext"] = _source_context(collector)
        return result

    @app.get("/v1/devices")
    async def devices():
        if collector is not None:
            return [{"deviceId": source.deviceId, "readOnly": True, "source": "SIMULATOR"} for source in collector.sources]
        return [{"deviceId": DEVICE_ID, "readOnly": True, "source": "SIMULATOR"}]

    @app.get("/v1/connections")
    async def connections():
        return collector.connections() if collector else []

    @app.get("/v1/devices/{id}/observations", response_model=list[Observation])
    async def observations(id: str):
        _known_device(id)
        if collector is not None:
            return collector.observations(id)
        return [
            Observation(
                schemaVersion=VERSION, observationId=f"mock-obs-{index:02d}", deviceId=DEVICE_ID,
                signalKey=signal, value=value, unit=unit, observedAtUtc=FIXTURE_START,
                receivedAtUtc=FIXTURE_START + timedelta(milliseconds=87), quality="GOOD",
                deviceStateVersion=41, modeId="PRODUCTION",
            )
            for index, (signal, (value, unit)) in enumerate(SIGNALS.items())
        ]

    @app.get("/v1/devices/{id}/health", response_model=DeviceHealth)
    async def device_health(id: str):
        _known_device(id)
        if collector is not None:
            return collector.device_health(id)
        return DeviceHealth(
            schemaVersion=VERSION, deviceId=DEVICE_ID, modelLifecycle="NO_MODEL",
            observationEvaluation="INSUFFICIENT_DATA", modelVersion=None,
            windowEndUtc=FIXTURE_END, evidenceSignalKeys=[], dataQuality="GOOD",
            normalReferencePercentile=None,
        )

    @app.get("/v1/warnings", response_model=list[Warning])
    async def warnings():
        if collector is not None:
            return collector.warnings()
        return [Warning(id=WARNING_ID, deviceId=DEVICE_ID, state="OPEN", createdAtUtc=WARNING_AT, recommendationId="rec-demo-02")]

    @app.post("/v1/warnings/{id}/ack")
    def acknowledge(id: str):
        if collector is not None:
            raise HTTPException(404)
        _known_warning(id)
        raise HTTPException(501)

    @app.post("/v1/lab/scenarios/DEG-02/start")
    def start_scenario():
        raise HTTPException(501)

    @app.get("/v1/devices/{id}/trends", response_model=TrendResponse)
    def trends(
        id: str, signals: str, fromUtc: str, toUtc: str,
        resolution: str = "auto", maxPoints: int = Query(default=MAX_POINTS_PER_SERIES, ge=1, le=MAX_POINTS_PER_SERIES),
    ):
        _known_device(id)
        if collector is not None:
            raise HTTPException(501)
        selected = signals.split(",")
        start, end = _utc(fromUtc), _utc(toUtc)
        if (not 0 < (end - start).total_seconds() <= THIN_DEMO_QUERY_MAX_SECONDS
                or not 1 <= len(selected) <= THIN_DEMO_MAX_SIGNALS or len(set(selected)) != len(selected)
                or any(signal not in SIGNALS for signal in selected)
                or resolution not in {"auto", "raw", "1m", "1h"}):
            raise HTTPException(422)
        if resolution in {"1m", "1h"}:
            raise HTTPException(501)

        gaps = []

        def gap(left, right, reason):
            left, right = max(start, left), min(end, right)
            if left < right:
                gaps.append({"fromUtc": left, "toUtc": right, "reason": reason})

        gap(start, FIXTURE_START, "MOCK_DATA_UNAVAILABLE")
        gap(FIXTURE_END, end, "MOCK_DATA_UNAVAILABLE")
        gap(MISSING_FROM, MISSING_TO, "SIGNAL_MISSING")
        # shortcut: fixed D0a samples only; add simulator input at D0b and storage at D1.
        times = [FIXTURE_START + timedelta(seconds=second) for second in range(301)]
        times = [at for at in times if start <= at <= end and not MISSING_FROM <= at < MISSING_TO]
        if len(times) > maxPoints:
            gap(times[maxPoints - 1], min(end, FIXTURE_END), "POINT_LIMIT")
            times = times[:maxPoints]
        return TrendResponse(
            schemaVersion=VERSION, deviceId=DEVICE_ID, source="SIMULATOR", resolution="raw",
            window={"fromUtc": start, "toUtc": end}, generatedAtUtc=GENERATED_AT,
            isComplete=not gaps, gaps=gaps,
            series=[{
                "signalKey": signal, "unit": SIGNALS[signal][1],
                "points": [{"t": at, "v": SIGNALS[signal][0], "quality": "GOOD"} for at in times],
            } for signal in selected],
            warningMarkers=[{"warningId": WARNING_ID, "atUtc": WARNING_AT, "type": "EARLY_WARNING"}]
            if start <= WARNING_AT <= end else [],
        )

    @app.get("/v1/warnings/{id}/timeline", response_model=WarningTimeline)
    async def timeline(id: str):
        if collector is not None:
            result = collector.timeline(id)
            if result is None:
                raise HTTPException(404)
            return result
        _known_warning(id)
        return WarningTimeline(
            schemaVersion=VERSION, warningId=WARNING_ID, generatedAtUtc=GENERATED_AT,
            events=[{
                "schemaVersion": VERSION, "eventId": "evt-demo-042", "occurredAtUtc": WARNING_AT,
                "component": "ml_worker", "eventName": "EWMA_WARNING_RAISED", "severity": "WARN",
                "traceId": "trace-demo-02", "outcome": "SUCCESS", "deviceId": DEVICE_ID,
                "warningId": WARNING_ID, "modelVersion": "ewma-demo-1", "reasonCode": "FLOW_ERROR_TREND",
                "safeContext": {"signalKeys": ["actualFlowLpm", "setpointFlowLpm"], "quality": "GOOD"},
            }],
        )

    @app.get("/v1/devices/{id}/diagnostics")
    async def diagnostics(id: str):
        _known_device(id)
        if collector is None:
            return {"deviceId": id, "enabled": False, "modelVersion": None, "evaluation": "UNAVAILABLE",
                    "sampleCount": 0, "flowErrorEwmaLpm": None, "issues": []}
        return collector.diagnostics(id)

    @app.get("/v1/ingestion")
    async def ingestion():
        return collector.ingestion() if collector is not None else []

    @app.get("/v1/devices/{id}/signal-review")
    async def signal_review(id: str):
        _known_device(id)
        if collector is None:
            from .signal_review import FlowReview
            return FlowReview(deviceId=id, state="DISABLED").model_dump(mode="json")
        return collector.signal_review(id)

    return app


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Read-only local fixture or emulator API.")
    parser.add_argument("--emulators", type=Path, help="Independent emulator sources configuration.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--evaluate", action="store_true", help="Enable development EWMA and data-quality advisory rules.")
    parser.add_argument("--archive", type=Path, help="Replay raw archive; defaults to runtime/raw.sqlite3 for replay sources.")
    parser.add_argument("--isolate", action="store_true", help="Run each module's acquisition, archive and analysis in its own process.")
    parser.add_argument("--archive-directory", type=Path, help="Isolated per-module archives; default runtime/modules.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose a local port between 1 and 65535.")
    if args.evaluate and not args.emulators:
        parser.error("--evaluate requires configured emulator sources.")
    if args.archive and not args.emulators:
        parser.error("--archive requires configured emulator sources.")
    if args.isolate and (not args.emulators or args.archive):
        parser.error("--isolate requires emulator sources and uses --archive-directory instead of --archive.")
    if args.archive_directory and not args.isolate:
        parser.error("--archive-directory requires --isolate.")
    try:
        log_path = Path("runtime/local_api.jsonl")
        sources = load_sources(args.emulators) if args.emulators else []
        archive = None
        if args.isolate:
            from aegis.processes import ProcessCollector
            collector = ProcessCollector(sources, args.archive_directory or Path("runtime/modules"), evaluate=args.evaluate, log_path=log_path)
        elif any(source.transport == "replay" for source in sources):
            from aegis.archive import RawArchive
            archive = RawArchive(args.archive or Path("runtime/raw.sqlite3"))
        elif args.archive:
            parser.error("--archive requires at least one replay source.")
        if not args.isolate:
            collector = Collector(sources, log_path=log_path, evaluate=args.evaluate, archive=archive) if sources else None
        app = create_app(os.environ.get("AEGIS_API_TOKEN", ""), log_path, collector=collector)
    except (ValueError, OSError):
        raise SystemExit("Check local API/emulator token environment variables and emulator configuration.") from None
    # Windows IOCP accept can remain failed after WinError 64; bounded local TCP needs no subprocess loop support.
    loop = "asyncio:SelectorEventLoop" if os.name == "nt" else "auto"
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False, loop=loop)


if __name__ == "__main__":
    main()
