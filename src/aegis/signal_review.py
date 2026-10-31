"""Descriptive flow evidence; an excursion's cause is deliberately unresolved."""

from collections import deque
from statistics import median
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import ContractModel, Count, Number, UtcTimestamp


REASONS = Literal["QUALITY", "GAP", "STATE_CHANGE", "SETPOINT_CHANGE", "SOURCE_UNAVAILABLE"]
RELATED = {"supplyPressureKpa": 10.0, "motorCurrentAmp": .2, "valveFeedbackPct": 3.0}
RelatedKey = Literal["supplyPressureKpa", "motorCurrentAmp", "valveFeedbackPct"]
Error = Annotated[Number, Field(ge=-100, le=100)]
Nonnegative = Annotated[Number, Field(ge=0)]


class FlowExcursion(ContractModel):
    eventId: Annotated[Count, Field(ge=1)]
    disposition: Literal["UNRESOLVED"] = "UNRESOLVED"
    startObservationId: Annotated[str, Field(min_length=1, max_length=256)]
    lastObservationId: Annotated[str, Field(min_length=1, max_length=256)]
    startAtUtc: UtcTimestamp
    lastAtUtc: UtcTimestamp
    closedAtUtc: UtcTimestamp | None = None
    endReason: Literal["RETURNED_TO_CONTEXT", "QUALITY", "GAP", "STATE_CHANGE", "SETPOINT_CHANGE", "SOURCE_UNAVAILABLE"] | None = None
    sampleCount: Annotated[Count, Field(ge=1)]
    rawErrorMinLpm: Error
    rawErrorMaxLpm: Error
    peakDeviationLpm: Nonnegative
    baselineErrorLpm: Error
    thresholdLpm: Annotated[Number, Field(ge=.5)]
    maxSampleIntervalMs: Nonnegative
    relatedSignalKeys: Annotated[list[RelatedKey], Field(max_length=3)]

    @model_validator(mode="after")
    def consistent_evidence(self):
        if (self.lastAtUtc < self.startAtUtc or self.rawErrorMinLpm > self.rawErrorMaxLpm
                or (self.closedAtUtc is None) != (self.endReason is None)
                or (self.closedAtUtc is not None and self.closedAtUtc < self.lastAtUtc)
                or len(set(self.relatedSignalKeys)) != len(self.relatedSignalKeys)):
            raise ValueError("Inconsistent flow evidence.")
        return self


class FlowReview(ContractModel):
    deviceId: Annotated[str, Field(min_length=1, max_length=64)]
    algorithmVersion: Literal["flow-review-dev-1"] = "flow-review-dev-1"
    state: Literal["DISABLED", "WARMING", "TRACKING", "REVIEW_REQUIRED", "UNAVAILABLE"]
    observedAtUtc: UtcTimestamp | None = None
    rawErrorLpm: Error | None = None
    trendErrorLpm: Error | None = None
    residualMadLpm: Nonnegative | None = None
    contextSampleCount: Annotated[Count, Field(le=256)] = 0
    contextSpanMs: Annotated[Number, Field(ge=0, le=1000)] = 0.0
    contextReason: Literal["STARTUP", "QUALITY", "GAP", "STATE_CHANGE", "SETPOINT_CHANGE", "SOURCE_UNAVAILABLE"] | None = "STARTUP"
    eventCount: Count = 0
    omittedEventCount: Count = 0
    events: Annotated[list[FlowExcursion], Field(max_length=32)] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded_history(self):
        ids = [event.eventId for event in self.events]
        if (self.eventCount != len(self.events) + self.omittedEventCount
                or ids != sorted(set(ids)) or any(key > self.eventCount for key in ids)
                or sum(event.closedAtUtc is None for event in self.events) > 1):
            raise ValueError("Inconsistent flow review history.")
        if self.state in {"DISABLED", "UNAVAILABLE"} and any(value is not None for value in
                (self.rawErrorLpm, self.trendErrorLpm, self.residualMadLpm)):
            raise ValueError("Unavailable analysis cannot contain current signal values.")
        return self


def unavailable_review(result):
    return dict(result, state="UNAVAILABLE", rawErrorLpm=None, trendErrorLpm=None,
                residualMadLpm=None, contextSampleCount=0, contextSpanMs=0.0,
                contextReason="SOURCE_UNAVAILABLE")


class FlowReviewMonitor:
    def __init__(self, device_id):
        self.device_id = device_id
        self._history = deque(maxlen=256)
        self._trend = deque(maxlen=5)
        self._events = deque(maxlen=32)
        self._active = self._reference = None
        self._count = 0
        self._last_at = self._setpoint = None
        self._result = FlowReview(deviceId=device_id, state="WARMING")

    def _close(self, at, reason):
        if self._active is not None:
            self._active.closedAtUtc = at
            self._active.endReason = reason
            self._active = self._reference = None

    def reset(self, reason: REASONS, at=None):
        self._close(at or self._last_at, reason)
        self._history.clear()
        self._trend.clear()
        self._last_at = self._setpoint = None
        self._result = FlowReview(deviceId=self.device_id, state="UNAVAILABLE", contextReason=reason,
                                  observedAtUtc=at)

    def accept(self, rows):
        """Caller validates the entire device/mode/time-consistent batch first."""
        by_key = {str(row.signalKey): row for row in rows}
        actual, setpoint = by_key["actualFlowLpm"], by_key["setpointFlowLpm"].value
        at, error = actual.observedAtUtc, actual.value - setpoint
        if self._setpoint is not None and abs(setpoint - self._setpoint) >= .5:
            self.reset("SETPOINT_CHANGE", at)
        if self._setpoint is None:
            self._setpoint = setpoint
        while self._history and (at - self._history[0][0]).total_seconds() > 1:
            self._history.popleft()
        span = ((self._history[-1][0] - self._history[0][0]).total_seconds()
                if len(self._history) > 1 else 0)
        ready = len(self._history) >= 5 and span >= .2 - 1e-9
        center = median(item[1] for item in self._history) if self._history else error
        mad = median(abs(item[1] - center) for item in self._history) if self._history else 0.0
        sensors = {key: by_key[key].value for key in RELATED}
        if self._active is None and ready and abs(error - center) > max(.5, 6 * mad):
            self._count += 1
            self._active = FlowExcursion(eventId=self._count,
                startObservationId=actual.observationId, lastObservationId=actual.observationId,
                startAtUtc=at, lastAtUtc=at, sampleCount=1, rawErrorMinLpm=error,
                rawErrorMaxLpm=error, peakDeviationLpm=abs(error - center), baselineErrorLpm=center,
                thresholdLpm=max(.5, 6 * mad), maxSampleIntervalMs=0.0, relatedSignalKeys=[])
            self._events.append(self._active)
            self._reference = {}
            for key, floor in RELATED.items():
                base = median(item[2][key] for item in self._history)
                deviation = median(abs(item[2][key] - base) for item in self._history)
                self._reference[key] = (base, max(floor, 6 * deviation))
        event = self._active
        if event is not None:
            if abs(error - event.baselineErrorLpm) <= event.thresholdLpm:
                self._close(at, "RETURNED_TO_CONTEXT")
            else:
                if at > event.lastAtUtc:
                    event.sampleCount += 1
                    event.maxSampleIntervalMs = max(event.maxSampleIntervalMs,
                        (at - event.lastAtUtc).total_seconds() * 1000)
                event.lastAtUtc, event.lastObservationId = at, actual.observationId
                event.rawErrorMinLpm = min(event.rawErrorMinLpm, error)
                event.rawErrorMaxLpm = max(event.rawErrorMaxLpm, error)
                event.peakDeviationLpm = max(event.peakDeviationLpm, abs(error - event.baselineErrorLpm))
                for key, (base, threshold) in self._reference.items():
                    if abs(sensors[key] - base) > threshold and key not in event.relatedSignalKeys:
                        event.relatedSignalKeys.append(key)
        self._trend.append(error)
        self._result = FlowReview(deviceId=self.device_id,
            state="REVIEW_REQUIRED" if self._active is not None else
                (("REVIEW_REQUIRED" if self._events else "TRACKING") if ready else "WARMING"),
            observedAtUtc=at, rawErrorLpm=error, trendErrorLpm=median(self._trend),
            residualMadLpm=mad if ready else None, contextSampleCount=len(self._history),
            contextSpanMs=round(span * 1000, 6), contextReason=None if ready else self._result.contextReason)
        # An unresolved excursion must not become the baseline for the next event.
        if self._active is None:
            self._history.append((at, error, sensors))
        self._last_at = at

    def snapshot(self):
        return self._result.model_copy(update={"eventCount": self._count,
            "omittedEventCount": self._count - len(self._events),
            "events": list(self._events)}, deep=True).model_dump(mode="json")
