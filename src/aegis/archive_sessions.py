"""Discover the launcher's previous synthetic sessions without accepting file paths."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
from tempfile import TemporaryDirectory
import time

from aegis.raw_history import RawQueryError, _schema, _timestamp
from aegis.stored_records import MAX_SOURCES, QueryError, _Budget


SESSION_ID = re.compile(r"run-[a-z0-9_]{8}")
DEVICE_ID_PATTERN = r"EQ-SUPPLY-[0-9]{2}"
DEVICE_ID = re.compile(DEVICE_ID_PATTERN)
MODULE_FILE = re.compile(rf"module-({DEVICE_ID_PATTERN})-([a-f0-9]{{12}})\.sqlite3")
MAX_SESSIONS = 32
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


class SessionNotFound(Exception):
    """The session is unknown, active, or outside the configured catalog."""


def _plain(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or not directory and info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise QueryError("SOURCE_UNAVAILABLE")
    return info


def _version(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _format_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ArchivedSession:
    session_id: str
    last_modified_ns: int
    paths: tuple[tuple[str, Path], ...]

    def summary(self) -> dict:
        modified = datetime.fromtimestamp(self.last_modified_ns / 1e9, timezone.utc)
        return {"sessionId": self.session_id, "lastModifiedAtUtc": _format_time(modified),
                "storeIds": [device for device, _path in self.paths]}


class ArchiveSessions:
    """The portable launcher holds the state-root lock for the active session."""

    def __init__(self, root: Path, active: Path):
        root = Path(root).absolute()
        active = Path(active).absolute()
        if active.parent != root or not SESSION_ID.fullmatch(active.name):
            raise ValueError("The active archive must belong to the portable sessions root.")
        _plain(root, directory=True)
        _plain(active, directory=True)
        self.root = root
        self.active_id = active.name

    def _session(self, folder: Path) -> ArchivedSession:
        info = _plain(folder, directory=True)
        sources: list[tuple[str, Path]] = []
        modified = info.st_mtime_ns
        for path in folder.iterdir():
            match = MODULE_FILE.fullmatch(path.name)
            if not match:
                continue
            device, marker = match.groups()
            if marker != hashlib.sha256(device.encode("ascii")).hexdigest()[:12]:
                continue
            modified = max(modified, _plain(path).st_mtime_ns)
            sources.append((device, path))
        if not 1 <= len(sources) <= MAX_SOURCES:
            raise SessionNotFound()
        return ArchivedSession(folder.name, modified, tuple(sorted(sources)))

    def catalog(self) -> dict:
        _plain(self.root, directory=True)
        sessions = []
        for folder in self.root.iterdir():
            if not SESSION_ID.fullmatch(folder.name) or folder.name == self.active_id:
                continue
            try:
                sessions.append(self._session(folder))
            except (QueryError, SessionNotFound, OSError):
                continue
        sessions.sort(key=lambda session: (session.last_modified_ns, session.session_id), reverse=True)
        return {"schemaVersion": "archive-sessions-1", "hasMore": len(sessions) > MAX_SESSIONS,
                "sessions": [session.summary() for session in sessions[:MAX_SESSIONS]]}

    @contextmanager
    def snapshots(self, session_id: str, device_id: str | None, *, require_device: bool = False):
        if not SESSION_ID.fullmatch(session_id) or session_id == self.active_id:
            raise SessionNotFound()
        _plain(self.root, directory=True)
        folder = self.root / session_id
        try:
            session = self._session(folder)
        except FileNotFoundError:
            raise SessionNotFound() from None
        if device_id is not None:
            selected = tuple(source for source in session.paths if source[0] == device_id)
            if not selected and require_device:
                raise SessionNotFound()
            if selected:
                session = ArchivedSession(session.session_id, session.last_modified_ns, selected)

        before = self._files(session)
        # A closed, checkpointed file can be read in SQLite immutable mode.
        # Re-check the source family before returning any result to the caller.
        if all(path.suffix == ".sqlite3" or version[2] == 0 for path, version in before.items()):
            yield [{"storeId": device, "kind": "RAW_V1", "path": path, "cleanPortable": True}
                   for device, path in session.paths]
            if before != self._files(session):
                raise QueryError("SOURCE_UNAVAILABLE")
            return
        # SQLite may create or update SHM when opening a WAL database in read-only
        # mode. Give journal-backed archives a private snapshot.
        with TemporaryDirectory(prefix="aegis-history-") as directory:
            snapshot_root = Path(directory)
            deadline = time.monotonic() + 2
            if sum(version[2] for version in before.values()) > MAX_SNAPSHOT_BYTES:
                raise QueryError("QUERY_LIMIT")
            for path, expected in before.items():
                with path.open("rb") as source:
                    if _version(os.fstat(source.fileno())) != expected:
                        raise QueryError("SOURCE_UNAVAILABLE")
                    with (snapshot_root / path.name).open("xb") as target:
                        remaining = expected[2]
                        while remaining:
                            if time.monotonic() > deadline:
                                raise QueryError("QUERY_LIMIT")
                            block = source.read(min(remaining, 1024 * 1024))
                            if not block:
                                raise QueryError("SOURCE_UNAVAILABLE")
                            target.write(block)
                            remaining -= len(block)
            if before != self._files(session):
                raise QueryError("SOURCE_UNAVAILABLE")
            yield [{"storeId": device, "kind": "RAW_V1", "path": snapshot_root / path.name}
                   for device, path in session.paths]

    @staticmethod
    def _files(session: ArchivedSession) -> dict[Path, tuple[int, int, int, int]]:
        files = {}
        for _device, path in session.paths:
            _plain(path.parent, directory=True)
            files[path] = _version(_plain(path))
            for suffix in ("-wal", "-journal"):
                companion = path.with_name(path.name + suffix)
                try:
                    files[companion] = _version(_plain(companion))
                except FileNotFoundError:
                    pass
        return files


def recent_receipt_window(sources: list[dict], device_id: str | None) -> tuple[str, str]:
    """Return a small factual window anchored to the latest matching stored receipt."""
    latest = None
    budget = _Budget()
    for source in sources:
        if device_id is not None and source["storeId"] != device_id:
            continue
        budget.check()
        path = source["path"]
        try:
            params = "?mode=ro&immutable=1" if source.get("cleanPortable") else "?mode=ro"
            connection = sqlite3.connect(path.as_uri() + params, uri=True, timeout=0.2)
            try:
                connection.set_progress_handler(budget.progress, 1000)
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                _schema(connection)
                receipt = connection.execute("SELECT max(received_at_utc) FROM raw_batches").fetchone()[0]
                if receipt is not None:
                    value = _timestamp(receipt)
                    latest = value if latest is None else max(latest, value)
            finally:
                connection.close()
        except (ValueError, OverflowError, sqlite3.Error, RawQueryError):
            raise QueryError("QUERY_LIMIT" if budget.exhausted else "SOURCE_INVALID") from None
    if latest is None:
        latest = datetime.now(timezone.utc)
    try:
        return _format_time(latest - timedelta(seconds=1)), _format_time(latest + timedelta(microseconds=1))
    except OverflowError:
        raise QueryError("SOURCE_INVALID") from None
