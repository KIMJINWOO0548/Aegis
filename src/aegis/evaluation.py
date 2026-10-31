"""Opt-in development advisory: fixed flow rules, not a trained or calibrated model."""

from collections import deque
from datetime import datetime, timezone
from functools import wraps
from math import isfinite
from threading import RLock
from uuid import uuid4

from .contracts import DeviceHealth, Observation, StructuredEvent, VERSION, Warning, WarningLifecycle, WarningTimeline
from .signal_review import FlowReviewMonitor


MODEL_VERSION = "ewma-flow-dev-1"
# Development hypotheses; hardware calibration must replace these bounds before pilot use.
RANGES = {
    "setpointFlowLpm": (0, 100), "actualFlowLpm": (0, 100),
    "supplyPressureKpa": (0, 2000), "valveCommandPct": (0, 100),
    "valveFeedbackPct": (0, 100), "motorCurrentAmp": (0, 100),
    "temperatureC": (-40, 200), "cycleCount": (0, float("inf")),
}
FLOW_SIGNALS = ["actualFlowLpm", "setpointFlowLpm"]


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class Evaluator:
    def __init__(self, device_id):
        self.device_id = device_id
        self._lock = RLock()
        self._at = datetime.now(timezone.utc)
        self._last_seen = self._mode = None
        self._state_version = None
        self._samples = deque(maxlen=60)
        self._score = self._last_eval = None
        self._high = self._low = 0
        self._evaluation, self._quality = "INSUFFICIENT_DATA", "MISSING"
        self._issues, self._evidence = [], []
        self._active = None
        self._warnings, self._events = deque(maxlen=32), {}
        self._signal_review = FlowReviewMonitor(device_id)

    def _reset(self):
        self._samples.clear()
        self._score = self._last_eval = None
        self._high = self._low = 0
        self._evaluation = "INSUFFICIENT_DATA"

    @staticmethod
    def _issue(key, reason, message):
        return {"signalKey": str(key), "reasonCode": reason, "message": message}

    @locked
    def accept(self, rows: list[Observation], *, continuous=True):
        at = max((row.observedAtUtc for row in rows), default=self._at)
        if rows and self._last_seen is not None and at <= self._last_seen:
            return
        previous_at = self._last_seen
        if rows:
            self._at = self._last_seen = at
        self._issues = []
        by_key = {str(row.signalKey): row for row in rows}
        for key, (lower, upper) in RANGES.items():
            row = by_key.get(key)
            reason = None
            if row is None:
                reason, message = "MISSING", "신호가 누락되었습니다. 수집 연결과 신호 구성을 확인하세요."
            elif row.deviceId != self.device_id or row.observedAtUtc != at or row.modeId != rows[0].modeId or row.deviceStateVersion != rows[0].deviceStateVersion:
                reason, message = "BATCH_MISMATCH", "신호의 장치·시각·운전 상태가 다릅니다. 수집 구성을 확인하세요."
            elif sum(item.signalKey == row.signalKey for item in rows) != 1:
                reason, message = "DUPLICATE_SIGNAL", "동일 신호가 중복되었습니다. 수집 구성을 확인하세요."
            elif row.quality != "GOOD":
                reason, message = str(row.quality), "신호 품질이 유효하지 않습니다. 센서와 수집 연결을 확인하세요."
            elif row.value is None or not isfinite(row.value) or not lower <= row.value <= upper or (key == "cycleCount" and not row.value.is_integer()):
                reason, message = "OUT_OF_RANGE", "개발용 허용 범위를 벗어났습니다. 센서 값과 단위·보정을 확인하세요."
            if reason:
                self._issues.append(self._issue(key, reason, message))
        if self._issues:
            self._signal_review.reset("QUALITY", at)
            self._reset()
            self._evidence = []
            reasons = {issue["reasonCode"] for issue in self._issues}
            self._quality = "BAD" if reasons - {"MISSING", "STALE"} else ("MISSING" if "MISSING" in reasons else "STALE")
            return
        if not continuous:
            self._signal_review.reset("GAP", at)
        if previous_at is not None and (at - previous_at).total_seconds() > 1.5:
            self._signal_review.reset("GAP", at)
            self._reset()
            self._issues.append(self._issue("actualFlowLpm", "GAP", "수집 간격이 1.5초를 넘었습니다. 연결 상태를 확인하세요."))
        if self._mode is not None and (rows[0].modeId != self._mode or rows[0].deviceStateVersion != self._state_version):
            self._signal_review.reset("STATE_CHANGE", at)
            self._reset()
        self._mode = rows[0].modeId
        self._state_version = rows[0].deviceStateVersion
        self._quality, self._evidence = "GOOD", list(FLOW_SIGNALS)
        self._signal_review.accept(rows)
        if self._score is None:
            self._evaluation = "INSUFFICIENT_DATA"
        # Validate every raw frame; only the advisory window uses a 1 Hz projection.
        if self._samples and (at - self._samples[-1][0]).total_seconds() < 1:
            return
        error = abs(by_key["actualFlowLpm"].value - by_key["setpointFlowLpm"].value)
        self._samples.append((at, error))
        if len(self._samples) < 60:
            return
        if (at - self._samples[0][0]).total_seconds() < 59:
            self._reset()
            self._samples.append((at, error))
            return
        if self._last_eval is not None and (at - self._last_eval).total_seconds() < 30:
            return
        mean = sum(value for _, value in self._samples) / 60
        self._score = mean if self._score is None else .2 * mean + .8 * self._score
        self._last_eval = at
        self._high = self._high + 1 if self._score > 1.5 else 0
        self._low = self._low + 1 if self._score <= 1.0 else 0
        if self._active is None and self._high >= 3:
            if len(self._warnings) == 32:
                self._events.pop(self._warnings[0].id)
            self._active = Warning(id=str(uuid4()), deviceId=self.device_id, state="OPEN",
                                   createdAtUtc=at, recommendationId="inspect-flow-tracking")
            self._warnings.append(self._active)
            self._events[self._active.id] = deque(maxlen=32)
            self._event("EWMA_WARNING_RAISED", "WARN", "FLOW_ERROR_PERSISTENT")
        elif self._active is not None and self._low >= 3:
            self._active.state = WarningLifecycle.RESOLVED
            self._event("ADVISORY_RECOVERY_CONFIRMED", "INFO", "FLOW_ERROR_RECOVERED")
            self._active = None
        self._evaluation = "ANOMALOUS" if self._active is not None else "NORMAL"

    def _event(self, name, severity, reason):
        warning_id = self._active.id
        self._events[warning_id].append(StructuredEvent(schemaVersion=VERSION,
            eventId=str(uuid4()), occurredAtUtc=self._at, component="ml_worker", eventName=name,
            severity=severity, traceId=warning_id, outcome="SUCCESS", deviceId=self.device_id,
            warningId=warning_id, modelVersion=MODEL_VERSION, reasonCode=reason,
            dataWindowStartUtc=self._samples[0][0], dataWindowEndUtc=self._at,
            safeContext={"flowErrorEwmaLpm": self._score, "sampleCount": len(self._samples)}))

    @locked
    def unavailable(self):
        self._signal_review.reset("SOURCE_UNAVAILABLE", self._last_seen)
        self._reset()
        self._evaluation = "UNAVAILABLE"
        self._quality = "STALE" if self._last_seen is not None else "MISSING"
        self._evidence = []
        self._issues = [self._issue(key, self._quality, "새 데이터를 받을 수 없습니다. 에뮬레이터와 수집 연결을 확인하세요.")
                        for key in FLOW_SIGNALS]

    @locked
    def diagnostics(self):
        return {"deviceId": self.device_id, "enabled": True, "modelVersion": MODEL_VERSION,
                "evaluation": self._evaluation, "sampleCount": len(self._samples),
                "flowErrorEwmaLpm": self._score, "issues": [dict(issue) for issue in self._issues]}

    @locked
    def signal_review(self):
        return self._signal_review.snapshot()

    @locked
    def health(self):
        return DeviceHealth(schemaVersion=VERSION, deviceId=self.device_id, modelLifecycle="ADVISORY",
            observationEvaluation=self._evaluation, modelVersion=MODEL_VERSION, windowEndUtc=self._at,
            evidenceSignalKeys=list(self._evidence), dataQuality=self._quality, normalReferencePercentile=None)

    @locked
    def warnings(self):
        return [warning.model_copy(deep=True) for warning in self._warnings]

    @locked
    def timeline(self, warning_id):
        events = self._events.get(warning_id)
        if events is None:
            return None
        return WarningTimeline(schemaVersion=VERSION, warningId=warning_id,
            events=[event.model_copy(deep=True) for event in events], generatedAtUtc=self._at)
