"""One module owns acquisition, raw storage and its advisory evaluation."""

import asyncio
import json
import sys
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aegis.archive import RawArchive
from aegis.collector import Collector, SourceConfig
from aegis.processes import MAX_FRAME_BYTES


class WorkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceConfig
    archivePath: str = Field(min_length=1, max_length=4096)
    evaluate: bool = Field(strict=True)
    logPath: str | None


def build_frame(collector):
    device = collector.sources[0].deviceId
    warnings = collector.warnings()
    connection = collector.connections()[0]
    if connection["lastReceivedAtUtc"] is not None:
        connection["lastReceivedAtUtc"] = connection["lastReceivedAtUtc"].isoformat()
    return {"connection": connection, "ingestion": collector.ingestion()[0],
        "observations": [row.model_dump(mode="json") for row in collector.observations(device)],
        "health": collector.device_health(device).model_dump(mode="json"),
        "diagnostics": collector.diagnostics(device),
        "signalReview": collector.signal_review(device),
        "warnings": [warning.model_dump(mode="json") for warning in warnings],
        "timelines": {warning.id: collector.timeline(warning.id).model_dump(mode="json") for warning in warnings},
        "loggingDegraded": collector.logging_degraded}


def _write(line):
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()


async def run(config):
    source = config.source
    collector = Collector([source], evaluate=config.evaluate,
        archive=RawArchive(config.archivePath) if source.transport == "replay" else None,
        log_path=Path(config.logPath) if config.logPath is not None else None)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()

    def watch_parent():
        try:
            while sys.stdin.buffer.read(1):
                pass
        finally:
            try:
                loop.call_soon_threadsafe(stopped.set)
            except RuntimeError:
                pass

    threading.Thread(target=watch_parent, name="aegis-parent", daemon=True).start()
    try:
        await collector.start()
        while not stopped.is_set():
            # Only the latest contracted flow rows cross the pipe; raw tags stay in this process.
            line = json.dumps(build_frame(collector), separators=(",", ":"), allow_nan=False).encode() + b"\n"
            if len(line) > MAX_FRAME_BYTES:
                raise ValueError("Module summary exceeded its bounded protocol.")
            await asyncio.to_thread(_write, line)
            try:
                await asyncio.wait_for(stopped.wait(), .2)
            except TimeoutError:
                pass
    finally:
        await collector.stop()


def main():
    try:
        line = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
        if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
            raise ValueError("Invalid module configuration line.")
        config = WorkerConfig.model_validate_json(line)
        asyncio.run(run(config))
    except (OSError, ValueError) as error:
        print(f"Module worker configuration or I/O failed ({type(error).__name__}, "
              f"winerror={getattr(error, 'winerror', None)}); inspect its local configuration and storage.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
