"""Storage cause codes and event transitions remain distinct from transport failures."""

import asyncio
import errno
import json
import sqlite3

import httpx
import pytest

from aegis.collector import Collector, SourceConfig
from aegis.module_worker import build_frame
from aegis.processes import decode_frame


TOKEN = "synthetic-storage-diagnostics-" + "x" * 32


class ArchivePort:
    error = None
    async def cursor(self, device):
        if self.error is not None:
            raise self.error
        return None


def sqlite_error(code=None):
    error = sqlite3.OperationalError("DIAG_PRIVATE full wording is not a typed cause")
    if code is not None:
        error.sqlite_errorcode = code
    return error


def configured():
    return SourceConfig(deviceId="STORAGE-DIAG", baseUrl="http://127.0.0.1:19101", transport="replay")


def timeout(request):
    raise httpx.ReadTimeout("DIAG_PRIVATE synthetic network timeout", request=request)


@pytest.mark.parametrize("factory,expected", [
    (lambda: sqlite_error(sqlite3.SQLITE_FULL), "ARCHIVE_FULL"),
    (lambda: sqlite_error(sqlite3.SQLITE_FULL | (5 << 8)), "ARCHIVE_FULL"),
    (lambda: OSError(errno.ENOSPC, "DIAG_PRIVATE local capacity"), "ARCHIVE_FULL"),
    (lambda: sqlite_error(sqlite3.SQLITE_BUSY), "ARCHIVE_UNAVAILABLE"),
    (lambda: sqlite_error(), "ARCHIVE_UNAVAILABLE"),
    (lambda: sqlite_error("13"), "ARCHIVE_UNAVAILABLE"),
    (lambda: OSError(errno.EIO, "DIAG_PRIVATE full text"), "ARCHIVE_UNAVAILABLE"),
    (lambda: PermissionError(errno.EACCES, "DIAG_PRIVATE full text"), "ARCHIVE_UNAVAILABLE"),
    (lambda: OSError("DIAG_PRIVATE full text"), "ARCHIVE_UNAVAILABLE"),
])
def test_typed_storage_causes_preserve_worker_frame_contract_without_private_error_text(
        tmp_path, factory, expected):
    async def check():
        archive = ArchivePort()
        archive.error = factory()
        log = tmp_path / "events.jsonl"
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN},
                                  archive=archive, client=client, log_path=log)
            await collector.poll_once(0)
            row = collector.connections()[0]
            assert row["state"] == "DISCONNECTED"  # Conservative acquisition unavailable status.
            assert row["reasonCode"] == expected
            assert row["acceptedBatches"] == 0 and collector.observations(configured().deviceId) == []
            packet = json.dumps(build_frame(collector)).encode() + b"\n"
            assert decode_frame(packet, configured().deviceId).connection["reasonCode"] == expected
            assert b"DIAG_PRIVATE" not in packet and "DIAG_PRIVATE" not in log.read_text()
            event = json.loads(log.read_text())
            assert (event["eventName"], event["reasonCode"]) == ("STORAGE_WRITE_FAILED", expected)
    asyncio.run(check())


def test_same_disconnected_status_records_changed_cause_once_without_repeated_failure_noise(tmp_path):
    async def check():
        archive = ArchivePort()
        log = tmp_path / "events.jsonl"
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN},
                                  archive=archive, client=client, log_path=log)
            await collector.poll_once(0)
            assert collector.connections()[0]["reasonCode"] == "POLL_TIMEOUT"
            archive.error = PermissionError(errno.EACCES, "synthetic archive permission failure")
            await collector.poll_once(0)
            assert collector.connections()[0]["reasonCode"] == "ARCHIVE_UNAVAILABLE"
            await collector.poll_once(0)
            archive.error = None
            await collector.poll_once(0)
            events = [json.loads(line) for line in log.read_text().splitlines()]
            assert [(e["eventName"], e["reasonCode"]) for e in events] == [
                ("POLL_TIMEOUT", "POLL_TIMEOUT"),
                ("STORAGE_WRITE_FAILED", "ARCHIVE_UNAVAILABLE"),
                ("POLL_TIMEOUT", "POLL_TIMEOUT"),
            ]
    asyncio.run(check())


def test_log_volume_full_does_not_hide_raw_storage_failure_or_kill_poll(tmp_path, monkeypatch):
    async def check():
        archive = ArchivePort()
        log = tmp_path / "events.jsonl"
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            collector = Collector([configured()], {"AEGIS_EMULATOR_TOKEN": TOKEN},
                                  archive=archive, client=client, log_path=log)
            await collector.poll_once(0)
            assert not collector.logging_degraded
            archive.error = OSError(errno.ENOSPC, "synthetic raw archive capacity")
            def unavailable_log(*args):
                raise OSError(errno.ENOSPC, "synthetic diagnostic volume capacity")
            monkeypatch.setattr("aegis.collector.write_event", unavailable_log)
            await collector.poll_once(0)
            assert collector.connections()[0]["reasonCode"] == "ARCHIVE_FULL"
            assert collector.logging_degraded
            assert collector.ingestion()[0]["lastCommittedSequence"] is None
            assert collector.device_health(configured().deviceId).observationEvaluation == "UNAVAILABLE"
            packet = json.dumps(build_frame(collector)).encode() + b"\n"
            assert decode_frame(packet, configured().deviceId).loggingDegraded
            await collector.poll_once(0)
            assert collector.logging_degraded and collector.connections()[0]["reasonCode"] == "ARCHIVE_FULL"
    asyncio.run(check())
