"""D0a wire contracts. All field names intentionally match the JSON contract."""

from datetime import datetime, timezone
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .contracts_generated import *  # noqa: F403 - generated public contract vocabulary


def iso_timestamp(value):
    if not isinstance(value, (str, datetime)) or (
        isinstance(value, str) and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", value
        )
    ):
        raise ValueError("timestamp must be an ISO 8601 string or an aware datetime")
    return value


UtcTimestamp = Annotated[AwareDatetime, BeforeValidator(iso_timestamp), AfterValidator(lambda value: value.astimezone(timezone.utc))]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Observation(ContractModel):
    schemaVersion: Literal[VERSION]
    observationId: str
    deviceId: str
    signalKey: SignalKey
    value: Number | None
    unit: str
    observedAtUtc: UtcTimestamp
    receivedAtUtc: UtcTimestamp
    quality: ObservationQuality
    deviceStateVersion: Count
    modeId: ModeId

    @model_validator(mode="after")
    def good_has_value(self):
        if self.quality == ObservationQuality.GOOD and self.value is None:
            raise ValueError("GOOD observations require a value")
        return self


class DeviceHealth(ContractModel):
    schemaVersion: Literal[VERSION]
    deviceId: str
    modelLifecycle: ModelLifecycle
    observationEvaluation: ObservationEvaluation
    modelVersion: str | None
    windowEndUtc: UtcTimestamp
    evidenceSignalKeys: list[SignalKey]
    dataQuality: ObservationQuality
    normalReferencePercentile: Annotated[Number, Field(ge=0, le=100)] | None = None

    @model_validator(mode="after")
    def insufficient_has_no_score(self):
        if self.observationEvaluation == ObservationEvaluation.INSUFFICIENT_DATA and self.normalReferencePercentile is not None:
            raise ValueError("INSUFFICIENT_DATA requires a null score")
        return self


class Warning(ContractModel):
    id: str
    deviceId: str
    state: WarningLifecycle
    createdAtUtc: UtcTimestamp
    recommendationId: str
    acknowledgedAtUtc: UtcTimestamp | None = None


class ApiError(ContractModel):
    code: str
    message: str
    requestId: str
    details: dict[str, Any] | None = None


class StructuredEvent(ContractModel):
    schemaVersion: Literal[VERSION]
    eventId: str
    occurredAtUtc: UtcTimestamp
    component: Component
    eventName: EventName
    severity: Severity
    traceId: str
    outcome: Outcome
    deviceId: str | None = None
    warningId: str | None = None
    incidentId: str | None = None
    requestId: str | None = None
    runId: str | None = None
    modelVersion: str | None = None
    sourceVersion: str | None = None
    durationMs: Annotated[Number, Field(ge=0)] | None = None
    reasonCode: str | None = None
    safeContext: dict[str, Any] | None = None
    sourceSequence: Count | None = None
    configRevision: str | None = None
    aggregationVersion: str | None = None
    dataWindowStartUtc: UtcTimestamp | None = None
    dataWindowEndUtc: UtcTimestamp | None = None
    observationGapCount: Count | None = None
    queryResolution: Resolution | None = None
    droppedCount: Count | None = None
    storageBytes: Count | None = None


class TimeWindow(ContractModel):
    fromUtc: UtcTimestamp
    toUtc: UtcTimestamp

    @model_validator(mode="after")
    def increasing(self):
        if self.fromUtc >= self.toUtc:
            raise ValueError("fromUtc must precede toUtc")
        return self


class TrendGap(TimeWindow):
    reason: str


class TrendPoint(ContractModel):
    t: UtcTimestamp
    v: Number | None
    quality: ObservationQuality

    @model_validator(mode="after")
    def good_has_value(self):
        if self.quality == ObservationQuality.GOOD and self.v is None:
            raise ValueError("GOOD points require a value")
        return self


class TrendSeries(ContractModel):
    signalKey: SignalKey
    unit: str
    points: Annotated[list[TrendPoint], Field(max_length=MAX_POINTS_PER_SERIES)]


class WarningMarker(ContractModel):
    warningId: str
    atUtc: UtcTimestamp
    type: str


class TrendResponse(ContractModel):
    schemaVersion: Literal[VERSION]
    deviceId: str
    source: Source
    resolution: Literal["raw", "1m", "1h"]
    window: TimeWindow
    generatedAtUtc: UtcTimestamp
    isComplete: Annotated[bool, Field(strict=True)]
    gaps: list[TrendGap]
    series: list[TrendSeries]
    warningMarkers: list[WarningMarker]


class WarningTimeline(ContractModel):
    schemaVersion: Literal[VERSION]
    warningId: str
    events: list[StructuredEvent]
    generatedAtUtc: UtcTimestamp
