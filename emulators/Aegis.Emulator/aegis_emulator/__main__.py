"""Run one independently configured emulator process."""

import argparse
import os

from .core import SCENARIOS, SnapshotGenerator, create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local synthetic equipment emulator.")
    parser.add_argument("--device-id", required=True, help="Unique equipment ID, e.g. EQ-SUPPLY-01.")
    parser.add_argument("--port", type=int, default=8771, help="Loopback TCP port (default: 8771).")
    parser.add_argument("--seed", type=int, default=1, help="Deterministic signal seed (default: 1).")
    parser.add_argument("--interval-ms", type=int, default=1000, help="Simulation sample interval, 1-60000 ms (default: 1000).")
    parser.add_argument("--scenario", choices=SCENARIOS, default="NORMAL", help="Synthetic signal scenario (default: NORMAL).")
    parser.add_argument("--signal-count", type=int, default=8, help="Replay signal count, 8-512 (legacy snapshots always contain 8).")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be from 1 to 65535.")
    try:
        generator = SnapshotGenerator(args.device_id, seed=args.seed, interval_ms=args.interval_ms,
                                      scenario=args.scenario, signal_count=args.signal_count)
        server = create_server(generator, os.environ.get("AEGIS_EMULATOR_TOKEN", ""), port=args.port)
    except ValueError as exc:
        parser.error(str(exc))
    except OSError:
        parser.exit(1, "Could not bind the emulator loopback port. Check whether it is already in use.\n")
    print(f"Aegis Emulator {args.device_id}: http://127.0.0.1:{args.port}/v1/snapshot (read-only)", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
