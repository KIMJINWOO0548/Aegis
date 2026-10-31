"""Start independent local emulators, the Aegis API, and optionally the desktop."""

import argparse
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from benchmark_emulators import ROOT, running_stack, wait_until


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--scenario", choices=["NORMAL", "DEG-02", "DATA-QUALITY", "TRANSIENT", "NOISY-FLOW"], default="NORMAL",
                        help="Fault scenario for the first device; other devices remain NORMAL.")
    parser.add_argument("--headless", action="store_true", help="Run acquisition without opening WPF.")
    parser.add_argument("--seconds", type=int, help="Stop automatically after 1..3600 seconds.")
    parser.add_argument("--signal-count", type=int, default=256)
    parser.add_argument("--interval-ms", type=int, default=100, help="Synthetic source sample interval, independent of UI refresh.")
    parser.add_argument("--archive", type=Path, help="Persistent raw archive; default runtime/raw/demo-<new-id>.sqlite3.")
    parser.add_argument("--isolate", action="store_true", help="Use separate acquisition/archive/analysis processes per module.")
    args = parser.parse_args()
    if not 1 <= args.count <= 32 or (args.seconds is not None and not 1 <= args.seconds <= 3600):
        parser.error("Use 1..32 devices and 1..3600 seconds when bounded.")
    if not 8 <= args.signal_count <= 512 or not 1 <= args.interval_ms <= 60000:
        parser.error("Use 8..512 signals and 1..60000ms sampling.")
    desktop_path = ROOT / "src/Aegis.Desktop/bin/Release/net10.0-windows/Aegis.Desktop.exe"
    if not args.headless and not desktop_path.is_file():
        parser.error("Build the Release desktop before running this demo.")
    desktop = None
    try:
        scenarios = [args.scenario, *(["NORMAL"] * (args.count - 1))]
        archive_path = args.archive or ROOT / "runtime/raw" / (f"modules-{uuid4()}" if args.isolate else f"demo-{uuid4()}.sqlite3")
        with running_stack(args.count, evaluate=True, scenarios=scenarios, replay=True,
                           signal_count=args.signal_count, interval_ms=args.interval_ms, archive_path=archive_path,
                           isolate=args.isolate) as stack:
            def connected():
                rows = stack.client.get("/v1/connections").json()
                return rows if len(rows) == args.count and all(row["state"] in {"CONNECTED", "STALE"} and row["acceptedBatches"] > 0 for row in rows) else None
            wait_until(connected, stack.processes)
            print(f"Acquisition started for {args.count} independent emulator(s). API: {stack.api_url}", flush=True)
            print(f"Synthetic data only; development EWMA enabled; first scenario {args.scenario}.", flush=True)
            print(f"{args.signal_count} numeric signals/module; sampling {args.interval_ms}ms. Raw archive retained: {stack.archive_path}", flush=True)
            print("Close Aegis or press Ctrl+C here to stop all demo processes.", flush=True)
            if not args.headless:
                environment = dict(stack.environment, AEGIS_API_URL=stack.api_url)
                desktop = subprocess.Popen([str(desktop_path)], cwd=ROOT, env=environment)
            deadline = time.monotonic() + args.seconds if args.seconds else None
            while (desktop is None or desktop.poll() is None) and (deadline is None or time.monotonic() < deadline):
                if any(process.poll() is not None for process in stack.processes):
                    raise RuntimeError("A demo backend exited unexpectedly.")
                time.sleep(.2)
    except KeyboardInterrupt:
        pass
    finally:
        if desktop is not None and desktop.poll() is None:
            desktop.terminate()
            desktop.wait(timeout=5)
    print("All owned demo processes stopped.")


if __name__ == "__main__":
    main()
