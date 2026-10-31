"""Bounded console heartbeat probe; Windows service installation belongs to D1."""

import argparse
import time
from uuid import UUID, uuid4

from .contracts import StructuredEvent
from .observability import make_event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--trace-id", default=str(uuid4()))
    args = parser.parse_args()
    if not 1 <= args.count <= 1000 or not 0 <= args.interval_ms <= 60000:
        parser.error("count must be 1..1000 and interval-ms must be 0..60000")
    try:
        trace_id = str(UUID(args.trace_id))
    except ValueError:
        parser.error("trace-id must be a UUID")

    def emit(name, sequence, **fields):
        event = make_event(name, "collector", trace_id, sourceSequence=sequence, **fields)
        print(StructuredEvent.model_validate(event).model_dump_json(exclude_none=True), flush=True)

    emit("SERVICE_STARTED", 1, reasonCode="D0A_HEARTBEAT_PROBE")
    started = time.perf_counter()
    completed = 0
    try:
        for _ in range(args.count):
            time.sleep(args.interval_ms / 1000)
            completed += 1
    except KeyboardInterrupt:
        emit("SERVICE_STOPPED", 2, outcome="SKIPPED", reasonCode="D0A_HEARTBEAT_CANCELLED",
             durationMs=(time.perf_counter() - started) * 1000,
             safeContext={"heartbeatCount": completed})
        return 130
    emit("SERVICE_STOPPED", 2, reasonCode="D0A_HEARTBEAT_COMPLETE",
         durationMs=(time.perf_counter() - started) * 1000,
         safeContext={"heartbeatCount": completed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
