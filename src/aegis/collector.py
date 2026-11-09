"""Bounded read-only acquisition with optional development advisory evaluation."""

import asyncio
import json
import os
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis.contracts import (VERSION, DeviceHealth, ModeId, Number, Observation,
                             ObservationEvaluation, ObservationQuality, SignalKey, UtcTimestamp)
from aegis.observability import make_event, write_event


SIGNAL_UNITS = {
    "setpointFlowLpm": "L/min", "actualFlowLpm": "L/min", "supplyPressureKpa": "kPa",
    "valveCommandPct": "%", "valveFeedbackPct": "%", "motorCurrentAmp": "A",
    "temperatureC": "degC", "cycleCount": "count",
}
MAX_BODY_BYTES = 65536
MAX_SOURCES = 32


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    deviceId: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
    baseUrl: str
    tokenEnvironment: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] = "AEGIS_EMULATOR_TOKEN"
    transport: Literal["snapshot", "replay"] = "snapshot"
    pollIntervalMs: Annotated[int, Field(strict=True, ge=50, le=60000)] | None = None
    equipmentId: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")] | None = None
    moduleId: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")] | None = None
    analysisProfile: Literal["flow", "raw"] = "flow"

    @model_validator(mode="after")
    def loopback_origin(self):
        try:
            url = urlsplit(self.baseUrl)
            valid = (url.scheme == "http" and url.hostname == "127.0.0.1" and url.port is not None
                     and 1 <= url.port <= 65535 and not url.username and not url.password
                     and url.path in {"", "/"} and not url.query and not url.fragment
                     and "?" not in self.baseUrl and "#" not in self.baseUrl)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("An emulator requires a literal IPv4 loopback HTTP origin with a port.")
        if (self.equipmentId is None) != (self.moduleId is None):
            raise ValueError("Specify both equipmentId and moduleId for a module source.")
        return self


def load_sources(path: Path) -> list[SourceConfig]:
    if path.stat().st_size > MAX_BODY_BYTES:
        raise ValueError("Emulator configuration is too large.")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or set(raw) != {"sources"} or not isinstance(raw["sources"], list):
        raise ValueError("Expected a sources configuration.")
    sources = [SourceConfig.model_validate(item) for item in raw["sources"]]
    _unique_sources(sources)
    return sources


def _unique_sources(sources):
    if not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("Configure between 1 and 32 emulator instances.")
    if len({s.deviceId for s in sources}) != len(sources) or len({urlsplit(s.baseUrl).port for s in sources}) != len(sources):
        raise ValueError("Emulator device IDs and origins must be unique.")


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    signalKey: SignalKey
    value: Number | None
    unit: str
    quality: ObservationQuality

    @model_validator(mode="after")
    def valid_reading(self):
        if self.unit != SIGNAL_UNITS[self.signalKey.value] or (self.quality == ObservationQuality.GOOD and self.value is None):
            raise ValueError("Invalid signal reading.")
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocolVersion: Literal["1.0"]
    source: Literal["SIMULATOR"]
    deviceId: str
    runId: UUID
    sequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    observedAtUtc: UtcTimestamp
    deviceStateVersion: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    modeId: ModeId
    signals: Annotated[list[Signal], Field(min_length=8, max_length=8)]

    @model_validator(mode="after")
    def complete_batch(self):
        if {row.signalKey.value for row in self.signals} != set(SIGNAL_UNITS):
            raise ValueError("A snapshot must contain exactly the eight contracted signals.")
        return self


class RawSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)
    signalKey: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    value: Number | None
    unit: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[^\x00-\x1f\x7f]+$")]
    quality: ObservationQuality

    @field_validator("value", mode="before")
    @classmethod
    def exact_integer_value(cls, value):
        # Reject lossy integer conversion before the raw value reaches storage.
        if type(value) is int:
            try:
                exact = float(value) == value
            except OverflowError:
                exact = False
            if not exact:
                raise ValueError("Raw integer values must be exactly representable as a float.")
        return value

    @model_validator(mode="after")
    def good_has_value(self):
        if self.quality == "GOOD" and self.value is None:
            raise ValueError("GOOD requires a numeric value.")
        return self


class RawSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocolVersion: Literal["1.1"]
    source: Literal["SIMULATOR"]
    deviceId: str
    runId: UUID
    sequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    observedAtUtc: UtcTimestamp
    deviceStateVersion: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    modeId: ModeId
    signals: Annotated[list[RawSignal], Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def unique_signals(self):
        if len({row.signalKey for row in self.signals}) != len(self.signals):
            raise ValueError("Duplicate raw signal identifiers.")
        for row in self.signals:
            if row.signalKey in SIGNAL_UNITS and row.unit != SIGNAL_UNITS[row.signalKey]:
                raise ValueError("Invalid flow-profile signal unit.")
        return self


class ReplayPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    streamVersion: Literal["1.0"]
    deviceId: str
    runId: UUID
    oldestSequence: Annotated[int, Field(strict=True, ge=0)]
    latestSequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    snapshots: Annotated[list[RawSnapshot], Field(max_length=256)]

    @model_validator(mode="after")
    def ordered_page(self):
        if not 0 <= self.latestSequence - self.oldestSequence < 4096:
            raise ValueError("Invalid replay retention bounds.")
        for index, snapshot in enumerate(self.snapshots):
            if snapshot.deviceId != self.deviceId or snapshot.runId != self.runId or not self.oldestSequence <= snapshot.sequence <= self.latestSequence:
                raise ValueError("Invalid replay identity or sequence.")
            if index and (snapshot.sequence != self.snapshots[index - 1].sequence + 1 or snapshot.observedAtUtc <= self.snapshots[index - 1].observedAtUtc):
                raise ValueError("Non-contiguous replay page.")
        return self


@dataclass(frozen=True)
class State:
    state: str = "CONNECTING"
    snapshot: Snapshot | RawSnapshot | None = None
    rows: tuple[Observation, ...] = ()
    received: datetime | None = None
    accepted_at: float | None = None
    accepted: int = 0
    accepted_values: int = 0
    archive_commit_ms: float | None = None
    available_sequence: int | None = None
    rejected: int = 0
    dropped: int = 0
    latency_ms: float | None = None
    reason: str | None = None


class RejectedBatch(ValueError):
    pass


class CollectorLivenessError(RuntimeError):
    """A fixed failure code, independent of source freshness or sample count."""


class Collector:
    def __init__(self, sources: list[SourceConfig], environment=None, *, client=None,
                 interval=1.0, timeout=.5, stale_after=3.0, log_path=None,
                 utc_now=None, monotonic=time.monotonic, evaluate=False, archive=None):
        _unique_sources(sources)
        if not .05 <= interval <= 60 or not .01 <= timeout <= 10 or not interval < stale_after <= 300:
            raise ValueError("Invalid bounded collector timing.")
        self.sources = tuple(sources)
        if any(source.transport == "replay" for source in sources) and archive is None:
            raise ValueError("Replay ingestion requires a durable raw archive.")
        self._archive = archive
        self._cursors = [None for _ in sources]
        self._behind = [False for _ in sources]
        environment = os.environ if environment is None else environment
        self._tokens = []
        for source in sources:
            token = environment.get(source.tokenEnvironment, "")
            if not isinstance(token, str) or len(token) < 32 or not all(33 <= ord(c) <= 126 for c in token):
                raise ValueError("Configure a private emulator token of at least 32 characters.")
            self._tokens.append(token)
        self.interval, self.timeout, self.stale_after = interval, timeout, stale_after
        self._now = utc_now or (lambda: datetime.now(timezone.utc))
        self._clock = monotonic
        self._states = [State() for _ in sources]
        self._retired_runs = [deque(maxlen=32) for _ in sources]
        self._client = client
        self._owns_client = client is None
        self._tasks = []
        self._poll_started = [None for _ in sources]
        self._log_path = log_path
        self._trace = str(uuid4())
        self.logging_degraded = False
        if evaluate:
            from aegis.evaluation import Evaluator
            self._evaluators = {source.deviceId: Evaluator(source.deviceId) for source in sources if source.analysisProfile == "flow"}
        else:
            self._evaluators = {}

    async def start(self):
        if self._tasks:
            raise RuntimeError("Collector already started.")
        if self._archive is not None:
            await self._archive.start()
        if self._client is None:
            self._client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=MAX_SOURCES, max_keepalive_connections=MAX_SOURCES))
        self._event("COLLECTOR_STARTED", "SUCCESS", None)
        self._tasks = [asyncio.create_task(self._loop(i)) for i in range(len(self.sources))]

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._archive is not None:
            await self._archive.close()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _loop(self, index):
        interval = self.sources[index].pollIntervalMs / 1000 if self.sources[index].pollIntervalMs else self.interval
        while True:
            started = self._clock()
            self._poll_started[index] = started
            try:
                await self.poll_once(index)
            finally:
                self._poll_started[index] = None
            await asyncio.sleep(.01 if self._behind[index] else max(.01, interval - (self._clock() - started)))

    def ensure_live(self):
        """Used by the module host; no new data is required for a healthy task."""
        if len(self._tasks) != len(self.sources):
            raise CollectorLivenessError("COLLECTOR_TASK_STOPPED")
        for task in self._tasks:
            if task.done():
                if not task.cancelled():
                    task.exception()  # Retrieve failures without logging source/private exception text.
                raise CollectorLivenessError("COLLECTOR_TASK_STOPPED")
        now = self._clock()
        if any(started is not None and now - started > max(10.0, self.timeout * 2)
               for started in self._poll_started):
            raise CollectorLivenessError("COLLECTOR_POLL_STALLED")

    def _event(self, name, outcome, device, reason=None, signals=None):
        if self._log_path is None:
            return
        fields = {"outcome": outcome}
        if device:
            fields["deviceId"] = device
        if reason:
            fields["reasonCode"] = reason
        if signals is not None:
            fields["safeContext"] = {"signalKeys": signals}
        self._write_event(make_event(name, "collector", self._trace, **fields))

    def _write_event(self, event):
        if self._log_path is None:
            return
        try:
            write_event(self._log_path, event)
            self.logging_degraded = False
        except OSError:
            self.logging_degraded = True

    async def _read(self, index, path, params=None, limit=MAX_BODY_BYTES):
        source = self.sources[index]
        assert self._client is not None
        async with asyncio.timeout(self.timeout), self._client.stream("GET", source.baseUrl.rstrip("/") + path,
                params=params, headers={"X-Aegis-Emulator-Token": self._tokens[index], "Accept-Encoding": "identity"},
                timeout=self.timeout, follow_redirects=False) as response:
            if response.status_code != 200:
                raise httpx.HTTPStatusError("Emulator request failed.", request=response.request, response=response)
            if response.headers.get("content-encoding", "identity") != "identity" or int(response.headers.get("content-length", "0")) > limit:
                raise RejectedBatch("BODY_LIMIT")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16384):
                body.extend(chunk)
                if len(body) > limit:
                    raise RejectedBatch("BODY_LIMIT")
        return body

    async def poll_once(self, index):
        source, previous = self.sources[index], self._states[index]
        self._behind[index] = False
        started = time.perf_counter()
        try:
            if source.transport == "replay":
                await self._replay(index)
            else:
                snapshot = Snapshot.model_validate_json(await self._read(index, "/v1/snapshot"))
                self._accept(index, snapshot)
        except (ValueError, RejectedBatch):
            self._states[index] = replace(previous, state="REJECTED", rejected=previous.rejected + 1, reason="INVALID_SNAPSHOT")
        except (httpx.TimeoutException, TimeoutError):
            self._states[index] = replace(previous, state="DISCONNECTED", reason="POLL_TIMEOUT")
        except httpx.HTTPError:
            self._states[index] = replace(previous, state="DISCONNECTED", reason="SOURCE_UNAVAILABLE")
        except (sqlite3.Error, OSError):
            self._states[index] = replace(previous, state="DISCONNECTED", reason="ARCHIVE_UNAVAILABLE")
        finally:
            state = self._states[index]
            self._states[index] = replace(state, latency_ms=round((time.perf_counter() - started) * 1000, 3))
        state = self._states[index]
        if self._effective(state) != "CONNECTED" and source.deviceId in self._evaluators:
            self._evaluators[source.deviceId].unavailable()
        if state.state != previous.state:
            if state.state in {"DISCONNECTED", "REJECTED"}:
                self._event("STORAGE_WRITE_FAILED" if state.reason == "ARCHIVE_UNAVAILABLE" else "POLL_TIMEOUT",
                            "FAILED", source.deviceId, state.reason)
            elif state.state == "CONNECTED" and previous.state != "CONNECTING":
                self._event("COLLECTOR_RESTARTED", "SUCCESS", source.deviceId)

    async def _replay(self, index):
        source = self.sources[index]
        cursor = await self._archive.cursor(source.deviceId)
        self._cursors[index] = cursor
        params = {"after": cursor[1] if cursor else -1, "limit": 256}
        if cursor:
            params["runId"] = cursor[0]
        page = ReplayPage.model_validate_json(await self._read(index, "/v1/batches", params, 1048576))
        received = self._now()
        if page.deviceId != source.deviceId:
            raise RejectedBatch("IDENTITY_MISMATCH")
        same_run = cursor is not None and cursor[0] == str(page.runId)
        if same_run and cursor[1] > page.latestSequence:
            raise RejectedBatch("REPLAY_REGRESSION")
        expected = cursor[1] + 1 if same_run else 0
        first = max(expected, page.oldestSequence)
        if page.snapshots:
            if page.snapshots[0].sequence != first:
                raise RejectedBatch("REPLAY_GAP")
        elif first <= page.latestSequence:
            raise RejectedBatch("EMPTY_REPLAY_PAGE")
        old = self._states[index].snapshot
        if old is None and same_run:
            old = RawSnapshot.model_validate(await self._archive.latest(source.deviceId))
        for snapshot in page.snapshots:
            self._check_snapshot(index, snapshot, old, history=True)
            old = snapshot
        if not page.snapshots:
            state = self._states[index]
            if state.snapshot is not None and state.snapshot.runId == page.runId:
                self._states[index] = replace(state, state="CONNECTED", reason=None)
            return
        gap = (expected, page.oldestSequence - 1) if expected < page.oldestSequence else None
        commit_started = time.perf_counter()
        await self._archive.append_validated(page.snapshots, received,
            equipment_id=source.equipmentId, module_id=source.moduleId, gap=gap)
        self._cursors[index] = await self._archive.cursor(source.deviceId)
        if self._cursors[index] != (str(page.runId), page.snapshots[-1].sequence):
            raise RejectedBatch("RETIRED_RUN")
        commit_ms = round((time.perf_counter() - commit_started) * 1000, 3)
        for snapshot in page.snapshots:
            self._accept(index, snapshot, history=True, received=received)
        if cursor is not None and not same_run:
            self._event("COLLECTOR_RESTARTED", "UNKNOWN", source.deviceId, "SOURCE_RUN_CHANGED")
        self._states[index] = replace(self._states[index], archive_commit_ms=commit_ms,
                                      available_sequence=page.latestSequence)
        self._behind[index] = page.snapshots[-1].sequence < page.latestSequence
        if gap:
            state = self._states[index]
            self._states[index] = replace(state, dropped=state.dropped + gap[1] - gap[0] + 1)
            self._event("OBSERVATION_GAP_DETECTED", "UNKNOWN", source.deviceId, "REPLAY_OVERFLOW")

    def _check_snapshot(self, index, snapshot, old, *, history=False):
        source = self.sources[index]
        now = self._now()
        age = (now - snapshot.observedAtUtc).total_seconds()
        if snapshot.deviceId != source.deviceId or age < -5 or (not history and age > max(6, self.stale_after * 2)):
            raise RejectedBatch("IDENTITY_OR_TIMESTAMP")
        dropped = 0
        if old is not None:
            if old.runId == snapshot.runId:
                if snapshot.sequence < old.sequence:
                    raise RejectedBatch("OUT_OF_ORDER")
                if snapshot.sequence == old.sequence:
                    if snapshot != old:
                        raise RejectedBatch("DUPLICATE_CHANGED")
                    return False, 0
                if snapshot.observedAtUtc <= old.observedAtUtc or snapshot.deviceStateVersion < old.deviceStateVersion:
                    raise RejectedBatch("OUT_OF_ORDER")
                dropped = snapshot.sequence - old.sequence - 1
            else:
                if snapshot.runId in self._retired_runs[index]:
                    raise RejectedBatch("RETIRED_RUN")
        return True, dropped

    def _accept(self, index, snapshot, *, history=False, received=None):
        source, previous = self.sources[index], self._states[index]
        old = previous.snapshot
        changed, dropped = self._check_snapshot(index, snapshot, old, history=history)
        if not changed:
            self._states[index] = replace(previous, state="CONNECTED", reason=None)
            return
        if history:
            dropped = 0
        if old is not None and old.runId != snapshot.runId:
            self._retired_runs[index].append(old.runId)
        now = received or self._now()
        rows = tuple(Observation(schemaVersion=VERSION,
            observationId=f"{snapshot.runId}:{snapshot.sequence}:{row.signalKey}", deviceId=source.deviceId,
            signalKey=row.signalKey, value=row.value, unit=row.unit, observedAtUtc=snapshot.observedAtUtc,
            receivedAtUtc=now, quality=row.quality, deviceStateVersion=snapshot.deviceStateVersion, modeId=snapshot.modeId)
            for row in snapshot.signals if row.signalKey in SIGNAL_UNITS)
        if source.deviceId in self._evaluators:
            evaluator = self._evaluators[source.deviceId]
            old_warnings = {warning.id: warning.state for warning in evaluator.warnings()} if self._log_path else {}
            old_issues = evaluator.diagnostics()["issues"] if self._log_path else []
            if old is not None and old.runId != snapshot.runId:
                evaluator.unavailable()
            if history and (self._now() - snapshot.observedAtUtc).total_seconds() > self.stale_after:
                evaluator.unavailable()
            else:
                evaluator.accept(list(rows), continuous=(old is None or old.runId != snapshot.runId
                    or snapshot.sequence == old.sequence + 1))
            if self._log_path:
                issues = evaluator.diagnostics()["issues"]
                if issues != old_issues:
                    self._event("SIGNAL_QUALITY_CHANGED", "FAILED" if issues else "SUCCESS", source.deviceId,
                        issues[0]["reasonCode"] if issues else "QUALITY_RECOVERED",
                        [issue["signalKey"] for issue in (issues or old_issues)])
                for warning in evaluator.warnings():
                    if old_warnings.get(warning.id) != warning.state:
                        self._write_event(evaluator.timeline(warning.id).events[-1].model_dump())
        self._states[index] = State(state="CONNECTED", snapshot=snapshot, rows=rows, received=now,
            accepted_at=self._clock(), accepted=previous.accepted + 1, rejected=previous.rejected,
            accepted_values=previous.accepted_values + len(snapshot.signals),
            dropped=previous.dropped + dropped)
        if dropped:
            self._event("OBSERVATION_GAP_DETECTED", "UNKNOWN", source.deviceId, "SEQUENCE_GAP")

    def _effective(self, state):
        if state.state == "CONNECTED" and state.accepted_at is not None and (
            self._clock() - state.accepted_at > self.stale_after or
            (self._now() - state.snapshot.observedAtUtc).total_seconds() > self.stale_after):
            return "STALE"
        return state.state

    def ingestion(self):
        return [{"deviceId": source.deviceId, "equipmentId": source.equipmentId, "moduleId": source.moduleId,
                 "transport": source.transport, "archiveEnabled": self._archive is not None and source.transport == "replay",
                 "signalCount": len(state.snapshot.signals) if state.snapshot else 0,
                 "acceptedSignalValues": state.accepted_values,
                 "archiveCommitMs": state.archive_commit_ms,
                 "latestAvailableSequence": state.available_sequence,
                 "pendingBatches": max(0, state.available_sequence - cursor[1]) if state.available_sequence is not None and cursor else None,
                 "observedLagMs": round(max(0, (self._now() - state.snapshot.observedAtUtc).total_seconds() * 1000), 3) if state.snapshot else None,
                 "lastObservedAtUtc": state.snapshot.observedAtUtc.isoformat() if state.snapshot else None,
                 "lastCommittedRunId": cursor[0] if cursor else None,
                 "lastCommittedSequence": cursor[1] if cursor else None}
                for source, state, cursor in zip(self.sources, self._states, self._cursors)]

    def connections(self):
        # Publish from one immutable state reference per device, including thread-pool API readers.
        rows = []
        for source, state in zip(self.sources, self._states):
            effective = self._effective(state)
            rows.append({"deviceId": source.deviceId, "state": effective,
                "lastReceivedAtUtc": state.received, "lastSequence": state.snapshot.sequence if state.snapshot else None,
                "acceptedBatches": state.accepted, "rejectedBatches": state.rejected, "droppedBatches": state.dropped,
                "pollLatencyMs": state.latency_ms, "reasonCode": "NO_NEW_DATA" if effective == "STALE" else state.reason})
        return rows

    def _index(self, device_id):
        for index, source in enumerate(self.sources):
            if source.deviceId == device_id:
                return index
        raise KeyError(device_id)

    def observations(self, device_id):
        state = self._states[self._index(device_id)]
        return self._rows_for_state(state)

    def _rows_for_state(self, state):
        if self._effective(state) == "CONNECTED":
            return list(state.rows)
        return [row.model_copy(update={"quality": ObservationQuality.STALE})
                if row.quality == ObservationQuality.GOOD else row for row in state.rows]

    def device_health(self, device_id):
        state = self._states[self._index(device_id)]
        current = self._effective(state) == "CONNECTED"
        if device_id in self._evaluators:
            result = self._evaluators[device_id].health()
            if not current:
                result = result.model_copy(update={"observationEvaluation": ObservationEvaluation.UNAVAILABLE,
                    "dataQuality": ObservationQuality.STALE if state.rows else ObservationQuality.MISSING,
                    "normalReferencePercentile": None})
            return result
        rows = self._rows_for_state(state)
        qualities = {row.quality.value for row in rows}
        quality = next((q for q in ("MISSING", "BAD", "STALE", "GOOD") if q in qualities), "MISSING")
        return DeviceHealth(schemaVersion=VERSION, deviceId=device_id, modelLifecycle="NO_MODEL",
            observationEvaluation="INSUFFICIENT_DATA" if current else "UNAVAILABLE", modelVersion=None,
            windowEndUtc=state.snapshot.observedAtUtc if state.snapshot else self._now(),
            evidenceSignalKeys=[], dataQuality=quality, normalReferencePercentile=None)

    def diagnostics(self, device_id):
        state = self._states[self._index(device_id)]
        current = self._effective(state) == "CONNECTED"
        if device_id in self._evaluators:
            result = self._evaluators[device_id].diagnostics()
            if not current:
                result.update(evaluation="UNAVAILABLE")
            return result
        return {"deviceId": device_id, "enabled": False, "modelVersion": None,
            "evaluation": "INSUFFICIENT_DATA" if current else "UNAVAILABLE", "sampleCount": 0,
            "flowErrorEwmaLpm": None, "issues": []}

    def warnings(self):
        return [warning for evaluator in self._evaluators.values() for warning in evaluator.warnings()]

    def signal_review(self, device_id):
        from .signal_review import FlowReview, unavailable_review
        state = self._states[self._index(device_id)]
        evaluator = self._evaluators.get(device_id)
        if evaluator is None:
            return FlowReview(deviceId=device_id, state="DISABLED").model_dump(mode="json")
        result = evaluator.signal_review()
        return result if self._effective(state) == "CONNECTED" else unavailable_review(result)

    def timeline(self, warning_id):
        for evaluator in self._evaluators.values():
            result = evaluator.timeline(warning_id)
            if result is not None:
                return result
        return None
