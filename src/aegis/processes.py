"""Supervise independent module collectors and retain only bounded latest summaries."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from aegis.collector import SIGNAL_UNITS, SourceConfig, _unique_sources
from aegis.contracts import (DeviceHealth, Observation, ObservationEvaluation, ObservationQuality,
                             UtcTimestamp,
                             VERSION, Warning, WarningTimeline)
from aegis.signal_review import FlowReview, unavailable_review


MAX_FRAME_BYTES = 1048576
SUMMARY_STALE_SECONDS = 3
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_UTC_TIMESTAMP = TypeAdapter(UtcTimestamp)


def _count(value, maximum=2**63 - 1):
    return type(value) is int and 0 <= value <= maximum


def _finite_nonnegative(value):
    return type(value) in (int, float) and 0 <= value <= sys.float_info.max


def _valid_identifier(value):
    return value is None or (isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None)


def _valid_reason(value):
    return value is None or (isinstance(value, str) and _CODE.fullmatch(value) is not None)


class WorkerFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: dict
    ingestion: dict
    observations: Annotated[list[Observation], Field(max_length=8)]
    health: DeviceHealth
    diagnostics: dict
    signalReview: FlowReview
    warnings: Annotated[list[Warning], Field(max_length=32)]
    timelines: dict[str, WarningTimeline]
    loggingDegraded: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def one_module(self):
        device = self.health.deviceId
        if (not isinstance(device, str) or _IDENTIFIER.fullmatch(device) is None
                or self.signalReview.deviceId != device
                or any(row.get("deviceId") != device for row in (self.connection, self.ingestion, self.diagnostics))
                or any(row.deviceId != device for row in [*self.observations, *self.warnings])
                or len({row.signalKey for row in self.observations}) != len(self.observations)
                or len({row.id for row in self.warnings}) != len(self.warnings)
                or len(self.timelines) > 32):
            raise ValueError("A worker summary must describe one module.")
        warning_ids = {row.id for row in self.warnings}
        for key, timeline in self.timelines.items():
            if (key != timeline.warningId or key not in warning_ids or len(timeline.events) > 32
                    or any(event.deviceId != device or event.warningId != key for event in timeline.events)):
                raise ValueError("Invalid module warning timeline.")
        if (set(self.connection) != {"deviceId", "state", "lastReceivedAtUtc", "lastSequence", "acceptedBatches",
                "rejectedBatches", "droppedBatches", "pollLatencyMs", "reasonCode"}
                or set(self.ingestion) != {"deviceId", "equipmentId", "moduleId", "transport", "archiveEnabled",
                "signalCount", "acceptedSignalValues", "archiveCommitMs", "observedLagMs", "lastObservedAtUtc",
                "lastCommittedRunId", "lastCommittedSequence", "latestAvailableSequence", "pendingBatches"}
                or set(self.diagnostics) != {"deviceId", "enabled", "modelVersion", "evaluation", "sampleCount",
                "flowErrorEwmaLpm", "issues"}):
            raise ValueError("Unexpected worker summary fields.")
        connection, ingestion, diagnostics = self.connection, self.ingestion, self.diagnostics
        if (not isinstance(connection["state"], str) or connection["state"] not in
                {"CONNECTING", "CONNECTED", "STALE", "DISCONNECTED", "REJECTED"}
                or any(not _count(connection[key]) for key in ("acceptedBatches", "rejectedBatches", "droppedBatches"))
                or (connection["lastSequence"] is not None and not _count(connection["lastSequence"]))
                or (connection["lastReceivedAtUtc"] is not None and
                    (not isinstance(connection["lastReceivedAtUtc"], str) or
                     _UTC_TIMESTAMP.validate_python(connection["lastReceivedAtUtc"]) is None))
                or (connection["pollLatencyMs"] is not None and not _finite_nonnegative(connection["pollLatencyMs"]))
                or not _valid_reason(connection["reasonCode"])):
            raise ValueError("Invalid worker connection metadata.")
        run_id, committed_sequence = ingestion["lastCommittedRunId"], ingestion["lastCommittedSequence"]
        if (not isinstance(ingestion["transport"], str) or ingestion["transport"] not in {"snapshot", "replay"}
                or type(ingestion["archiveEnabled"]) is not bool
                or not _count(ingestion["signalCount"], 512)
                or not _count(ingestion["acceptedSignalValues"])
                or any(value is not None and not _finite_nonnegative(value) for value in
                       (ingestion["archiveCommitMs"], ingestion["observedLagMs"]))
                or (ingestion["lastObservedAtUtc"] is None) != (ingestion["observedLagMs"] is None)
                or (ingestion["lastObservedAtUtc"] is not None and
                    (not isinstance(ingestion["lastObservedAtUtc"], str) or
                     _UTC_TIMESTAMP.validate_python(ingestion["lastObservedAtUtc"]) is None))
                or not all(_valid_identifier(ingestion[key]) for key in ("equipmentId", "moduleId"))
                or (ingestion["equipmentId"] is None) != (ingestion["moduleId"] is None)
                or (run_id is None) != (committed_sequence is None)
                or (run_id is not None and (not isinstance(run_id, str) or not _valid_uuid(run_id)))
                or (committed_sequence is not None and not _count(committed_sequence))
                or (ingestion["latestAvailableSequence"] is not None and
                    not _count(ingestion["latestAvailableSequence"]))
                or (ingestion["pendingBatches"] is not None and not _count(ingestion["pendingBatches"]))):
            raise ValueError("Invalid worker ingestion metadata.")
        model_version = diagnostics["modelVersion"]
        if (type(diagnostics["enabled"]) is not bool
                or (model_version is not None and (not isinstance(model_version, str) or
                    not _IDENTIFIER.fullmatch(model_version)))
                or (diagnostics["enabled"] and model_version is None)
                or (not diagnostics["enabled"] and model_version is not None)
                or not isinstance(diagnostics["evaluation"], str)
                or diagnostics["evaluation"] not in {item.value for item in ObservationEvaluation}
                or not _count(diagnostics["sampleCount"], 60)
                or (diagnostics["flowErrorEwmaLpm"] is not None and
                    not _finite_nonnegative(diagnostics["flowErrorEwmaLpm"]))
                or not isinstance(diagnostics["issues"], list) or len(diagnostics["issues"]) > len(SIGNAL_UNITS)
                or any(not isinstance(issue, dict) or set(issue) != {"signalKey", "reasonCode", "message"}
                       or not isinstance(issue["signalKey"], str)
                       or issue["signalKey"] not in SIGNAL_UNITS
                       or not isinstance(issue["reasonCode"], str) or not _CODE.fullmatch(issue["reasonCode"])
                       or not isinstance(issue["message"], str) or not 1 <= len(issue["message"]) <= 512
                       or any(ord(char) < 32 or ord(char) == 127 for char in issue["message"])
                       for issue in diagnostics["issues"])):
            raise ValueError("Invalid worker diagnostics metadata.")
        return self


def _valid_uuid(value):
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def decode_frame(line: bytes, device_id: str) -> WorkerFrame:
    if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
        raise ValueError("Worker summary exceeds its bounded line protocol.")
    frame = WorkerFrame.model_validate_json(line)
    if frame.health.deviceId != device_id:
        raise ValueError("Worker summary identity does not match its configured module.")
    return frame


@dataclass
class ModuleProcess:
    source: SourceConfig
    archive: Path
    process: subprocess.Popen | None = None
    summary: tuple[WorkerFrame, float] | None = None
    restart_count: int = 0
    state: str = "STARTING"


class ProcessCollector:
    def __init__(self, sources, archive_directory, *, evaluate=False, log_path=None):
        _unique_sources(sources)
        self.sources = tuple(sources)
        self.archive_directory = Path(archive_directory)
        self.archive_paths = {source.deviceId: self.archive_directory /
            f"module-{source.deviceId}-{hashlib.sha256(source.deviceId.encode()).hexdigest()[:12]}.sqlite3"
            for source in self.sources}
        self._workers = [ModuleProcess(source, self.archive_paths[source.deviceId]) for source in self.sources]
        self._evaluate, self._log_path = evaluate, log_path
        self._executor = None
        self._tasks = []
        self._stopping = False
        self._stop_event = None

    @property
    def logging_degraded(self):
        return any(worker.summary is not None and worker.summary[0].loggingDegraded for worker in self._workers)

    async def start(self):
        if self._executor is not None:
            raise RuntimeError("Module collector already started.")
        for source in self.sources:
            token = os.environ.get(source.tokenEnvironment, "")
            if len(token) < 32 or not all(33 <= ord(character) <= 126 for character in token):
                raise ValueError("Configure a private emulator token of at least 32 characters.")
        self.archive_directory.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._executor = ThreadPoolExecutor(max_workers=len(self.sources), thread_name_prefix="aegis-module")
        self._tasks = [asyncio.create_task(self._monitor(worker)) for worker in self._workers]

    def _launch(self, worker, stderr):
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        # The base interpreter avoids Windows venv redirector children; explicitly retain its packages.
        paths = [str(Path(__file__).resolve().parents[1]), *[path for path in sys.path if path]]
        environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
        process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-m", "aegis.module_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        worker.process = process
        config = {"source": worker.source.model_dump(mode="json"), "archivePath": str(worker.archive.resolve()),
                  "evaluate": self._evaluate,
                  "logPath": str(worker.archive.with_suffix(".events.jsonl").resolve()) if self._log_path is not None else None}
        process.stdin.write(json.dumps(config, separators=(",", ":")).encode() + b"\n")
        process.stdin.flush()
        return process

    def _read(self, worker, process):
        while True:
            line = process.stdout.readline(MAX_FRAME_BYTES + 1)
            if not line:
                return
            if self._stopping:
                continue
            frame = decode_frame(line, worker.source.deviceId)
            if worker.process is not process:
                return
            # Publish receipt and frame together; API readers capture one immutable reference.
            worker.summary = (frame, time.monotonic())
            worker.state = "RUNNING"

    @staticmethod
    def _finish(process):
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    async def _monitor(self, worker):
        while not self._stopping:
            worker.state = "STARTING"
            process = None
            try:
                with worker.archive.with_suffix(".stderr.log").open("ab") as stderr:
                    try:
                        process = self._launch(worker, stderr)
                        await asyncio.get_running_loop().run_in_executor(self._executor, self._read, worker, process)
                    except (OSError, ValueError) as error:
                        stderr.write(f"Module worker lifecycle failed: {type(error).__name__}\n".encode())
                        stderr.flush()
                    finally:
                        worker.state = "STOPPED" if self._stopping else "RESTARTING"
                        process = process or worker.process
                        if process is not None:
                            await asyncio.to_thread(self._finish, process)
                            if process.stdout is not None:
                                process.stdout.close()
            except OSError:
                worker.state = "STOPPED" if self._stopping else "RESTARTING"
            if not self._stopping:
                worker.restart_count += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), 2)
                except TimeoutError:
                    pass

    async def stop(self):
        if self._executor is None:
            return
        self._stopping = True
        self._stop_event.set()
        processes = [worker.process for worker in self._workers if worker.process is not None]
        await asyncio.gather(*(asyncio.to_thread(self._finish, process) for process in processes))
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.to_thread(self._executor.shutdown, wait=True)
        self._executor = None
        self._tasks.clear()

    def _available(self, worker, summary):
        return (summary is not None and worker.state == "RUNNING" and worker.process is not None
                and worker.process.poll() is None and time.monotonic() - summary[1] <= SUMMARY_STALE_SECONDS)

    def _current(self, worker, summary):
        if not self._available(worker, summary) or summary[0].connection["state"] != "CONNECTED":
            return False
        lag = self._observed_lag_ms(summary)
        return lag is not None and lag <= SUMMARY_STALE_SECONDS * 1000

    @staticmethod
    def _observed_lag_ms(summary):
        frame = summary[0]
        observed = [row.observedAtUtc for row in frame.observations]
        if frame.ingestion["lastObservedAtUtc"] is not None:
            observed.append(_UTC_TIMESTAMP.validate_python(frame.ingestion["lastObservedAtUtc"]))
        return max(0, (datetime.now(timezone.utc) - min(observed)).total_seconds() * 1000) if observed else None

    def _worker(self, device_id):
        for worker in self._workers:
            if worker.source.deviceId == device_id:
                return worker
        raise KeyError(device_id)

    def connections(self):
        rows = []
        for worker in self._workers:
            summary = worker.summary
            row = dict(summary[0].connection) if summary is not None else {
                "deviceId": worker.source.deviceId, "state": "CONNECTING", "lastReceivedAtUtc": None,
                "lastSequence": None, "acceptedBatches": 0, "rejectedBatches": 0,
                "droppedBatches": 0, "pollLatencyMs": None, "reasonCode": None}
            if not self._available(worker, summary):
                row.update(state="DISCONNECTED", reasonCode="WORKER_UNAVAILABLE")
            elif row["state"] == "CONNECTED" and not self._current(worker, summary):
                row.update(state="STALE", reasonCode="NO_NEW_DATA")
            rows.append(row)
        return rows

    def ingestion(self):
        rows = []
        for worker in self._workers:
            source = worker.source
            summary = worker.summary
            row = dict(summary[0].ingestion) if summary is not None else {
                "deviceId": source.deviceId, "equipmentId": source.equipmentId, "moduleId": source.moduleId,
                "transport": source.transport, "archiveEnabled": source.transport == "replay", "signalCount": 0,
                "acceptedSignalValues": 0, "archiveCommitMs": None, "observedLagMs": None,
                "lastObservedAtUtc": None,
                "lastCommittedRunId": None, "lastCommittedSequence": None,
                "latestAvailableSequence": None, "pendingBatches": None}
            if summary is not None and row["observedLagMs"] is not None:
                row["observedLagMs"] = round(self._observed_lag_ms(summary), 3)
            row.update(workerPid=worker.process.pid if worker.process is not None and worker.process.poll() is None else None,
                restartCount=worker.restart_count, workerState=worker.state if self._available(worker, summary) else "UNAVAILABLE")
            rows.append(row)
        return rows

    def observations(self, device_id):
        worker = self._worker(device_id)
        summary = worker.summary
        rows = summary[0].observations if summary is not None else []
        current = self._current(worker, summary)
        return [row.model_copy(update={"quality": ObservationQuality.STALE}) if not current and row.quality == "GOOD"
                else row.model_copy() for row in rows]

    def device_health(self, device_id):
        worker = self._worker(device_id)
        summary = worker.summary
        result = summary[0].health.model_copy(deep=True) if summary is not None else DeviceHealth(
            schemaVersion=VERSION, deviceId=device_id, modelLifecycle="NO_MODEL",
            observationEvaluation="UNAVAILABLE", modelVersion=None, windowEndUtc=datetime.now(timezone.utc),
            evidenceSignalKeys=[], dataQuality="MISSING", normalReferencePercentile=None)
        if not self._current(worker, summary):
            result = result.model_copy(update={"observationEvaluation": ObservationEvaluation.UNAVAILABLE, "evidenceSignalKeys": [],
                "dataQuality": ObservationQuality.STALE if summary is not None and summary[0].observations else ObservationQuality.MISSING,
                "normalReferencePercentile": None})
        return result

    def diagnostics(self, device_id):
        worker = self._worker(device_id)
        summary = worker.summary
        result = dict(summary[0].diagnostics) if summary is not None else {
            "deviceId": device_id, "enabled": False,
            "modelVersion": None, "evaluation": "UNAVAILABLE", "sampleCount": 0,
            "flowErrorEwmaLpm": None, "issues": []}
        if not self._current(worker, summary):
            result.update(evaluation="UNAVAILABLE", flowErrorEwmaLpm=None)
        return result

    def warnings(self):
        return [warning.model_copy(deep=True) for worker in self._workers if (summary := worker.summary) is not None
                for warning in summary[0].warnings]

    def signal_review(self, device_id):
        worker = self._worker(device_id)
        summary = worker.summary
        if summary is None:
            return FlowReview(deviceId=device_id, state="UNAVAILABLE").model_dump(mode="json")
        result = summary[0].signalReview.model_dump(mode="json")
        if result["state"] == "DISABLED" or self._current(worker, summary):
            return result
        return unavailable_review(result)

    def timeline(self, warning_id):
        for worker in self._workers:
            summary = worker.summary
            if summary is not None and warning_id in summary[0].timelines:
                return summary[0].timelines[warning_id].model_copy(deep=True)
        return None
