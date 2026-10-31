"""Durable raw replay batches; disk work stays on one dedicated worker."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


COLUMNS = {
    "raw_batches": ("device_id", "run_id", "sequence", "observed_at_utc", "received_at_utc", "equipment_id", "module_id", "payload_json"),
    "ingest_cursors": ("device_id", "run_id", "sequence"),
    "ingest_gaps": ("device_id", "run_id", "first_sequence", "last_sequence", "reason"),
}
SCHEMA = (
    """CREATE TABLE raw_batches (
        device_id TEXT NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0),
        observed_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL,
        equipment_id TEXT, module_id TEXT, payload_json TEXT NOT NULL,
        PRIMARY KEY(device_id,run_id,sequence))""",
    """CREATE TABLE ingest_cursors (
        device_id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK(sequence>=0))""",
    """CREATE TABLE ingest_gaps (
        device_id TEXT NOT NULL, run_id TEXT NOT NULL,
        first_sequence INTEGER NOT NULL CHECK(first_sequence>=0),
        last_sequence INTEGER NOT NULL CHECK(last_sequence>=first_sequence),
        reason TEXT NOT NULL DEFAULT 'REPLAY_OVERFLOW' CHECK(reason='REPLAY_OVERFLOW'),
        UNIQUE(device_id,run_id,first_sequence,last_sequence))""",
)


class RawArchive:
    def __init__(self, path):
        self.path = Path(path)
        self._executor = self._connection = None
        self._ready = False
        self._lifecycle = asyncio.Lock()

    async def start(self):
        async with self._lifecycle:
            if self._ready:
                return
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aegis-raw")
            try:
                await asyncio.get_running_loop().run_in_executor(self._executor, self._open)
            except BaseException:
                await self._shutdown()
                raise
            self._ready = True

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=.2, isolation_level=None)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = list(connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
            tables = {name for kind, name in objects if kind == "table"}
            if version not in (0, 1) or (version == 0 and objects) or (version == 1 and tables != set(COLUMNS)):
                raise ValueError("Unknown or unrelated archive database schema version.")
            if version == 1:
                for name, columns in COLUMNS.items():
                    fields = list(connection.execute(f"PRAGMA table_info({name})"))
                    keys = tuple(row[1] for row in sorted(fields, key=lambda row: row[5]) if row[5])
                    expected_keys = columns[:3] if name == "raw_batches" else (("device_id",) if name == "ingest_cursors" else ())
                    if tuple(row[1] for row in fields) != columns or keys != expected_keys:
                        raise ValueError("Unknown archive database schema.")
                unique_gap = any(tuple(row[0] for row in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index[1],))) == COLUMNS["ingest_gaps"][:4]
                    for index in connection.execute("PRAGMA index_list(ingest_gaps)") if index[2] and not index[4])
                if not unique_gap:
                    raise ValueError("Unknown archive database gap schema.")
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValueError("Archive database requires WAL journal mode.")
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise ValueError("Archive database requires FULL synchronization.")
            if version == 0:
                connection.execute("BEGIN IMMEDIATE")
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            self._connection = connection
        except BaseException:
            connection.close()
            raise

    async def _run(self, method, *args):
        if not self._ready:
            raise RuntimeError("Raw archive is not started or is closing.")
        return await asyncio.get_running_loop().run_in_executor(self._executor, method, *args)

    async def cursor(self, device_id) -> tuple[str, int] | None:
        return await self._run(lambda: self._connection.execute(
            "SELECT run_id,sequence FROM ingest_cursors WHERE device_id=?", (device_id,)).fetchone())

    async def latest(self, device_id) -> dict | None:
        def read():
            row = self._connection.execute("""SELECT payload_json FROM raw_batches b JOIN ingest_cursors c
                ON b.device_id=c.device_id AND b.run_id=c.run_id AND b.sequence=c.sequence WHERE b.device_id=?""",
                (device_id,)).fetchone()
            return json.loads(row[0]) if row is not None else None
        return await self._run(read)

    async def append(self, snapshots: list[dict], received_at: datetime, *, equipment_id=None, module_id=None, gap: tuple[int, int] | None = None):
        # Copy before submitting: a caller must not mutate a queued transaction's evidence.
        records = [(item["deviceId"], str(item["runId"]), item["sequence"], item["observedAtUtc"],
                    json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
                   for item in snapshots]
        await self._append_records(records, received_at, equipment_id, module_id, gap)

    async def append_validated(self, snapshots, received_at: datetime, *, equipment_id=None, module_id=None, gap=None):
        """Serialize the collector's validated models directly, before queueing disk work."""
        records = [(item.deviceId, str(item.runId), item.sequence, item.observedAtUtc.isoformat().replace("+00:00", "Z"),
                    item.model_dump_json()) for item in snapshots]
        await self._append_records(records, received_at, equipment_id, module_id, gap)

    async def _append_records(self, records, received_at, equipment_id, module_id, gap):
        if not self._ready:
            raise RuntimeError("Raw archive is not started or is closing.")
        if not records:
            if gap is not None:
                raise ValueError("A gap requires a source batch identity.")
            return
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("Receipt timestamp must include a timezone.")
        if any(row[:2] != records[0][:2] for row in records):
            raise ValueError("One append must contain one device and source run.")
        if gap is not None and (len(gap) != 2 or any(type(value) is not int for value in gap)
                                or not 0 <= gap[0] <= gap[1] < min(row[2] for row in records)):
            raise ValueError("Invalid replay overflow gap range.")
        await self._run(self._append, records, received_at.astimezone(timezone.utc).isoformat(), equipment_id, module_id, gap)

    def _append(self, records, receipt, equipment, module, gap):
        connection = self._connection
        device, run = records[0][:2]
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("SELECT run_id,sequence FROM ingest_cursors WHERE device_id=?", (device,)).fetchone()
            known_run = connection.execute("SELECT 1 FROM raw_batches WHERE device_id=? AND run_id=? LIMIT 1", (device, run)).fetchone()
            for _, _, sequence, observed, payload in records:
                old = connection.execute("SELECT payload_json FROM raw_batches WHERE device_id=? AND run_id=? AND sequence=?",
                                         (device, run, sequence)).fetchone()
                if old is not None:
                    if old[0] != payload and json.loads(old[0]) != json.loads(payload):
                        raise ValueError("Conflicting changed payload for an existing raw batch.")
                    continue
                connection.execute("INSERT INTO raw_batches VALUES (?,?,?,?,?,?,?,?)",
                                   (device, run, sequence, observed, receipt, equipment, module, payload))
            if gap is not None:
                connection.execute("""INSERT INTO ingest_gaps(device_id,run_id,first_sequence,last_sequence)
                    VALUES (?,?,?,?) ON CONFLICT(device_id,run_id,first_sequence,last_sequence) DO NOTHING""",
                    (device, run, *gap))
            if cursor is None or cursor[0] == run or not known_run:
                sequence = max(row[2] for row in records)
                if cursor is not None and cursor[0] == run:
                    sequence = max(sequence, cursor[1])
                connection.execute("""INSERT INTO ingest_cursors VALUES (?,?,?)
                    ON CONFLICT(device_id) DO UPDATE SET run_id=excluded.run_id,sequence=excluded.sequence""",
                    (device, run, sequence))
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def _close_connection(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def _shutdown(self):
        executor = self._executor
        async def finish():
            try:
                await asyncio.get_running_loop().run_in_executor(executor, self._close_connection)
            finally:
                await asyncio.to_thread(executor.shutdown, wait=True)
        task = asyncio.create_task(finish())
        cancelled = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
        finally:
            self._executor = None
        if cancelled:
            raise asyncio.CancelledError

    async def close(self):
        async with self._lifecycle:
            if self._executor is None:
                return
            self._ready = False
            await self._shutdown()
