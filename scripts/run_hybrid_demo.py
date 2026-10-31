"""Run independent emulators, raw archives, C# review, and the shared React dashboard."""

import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import httpx

if __package__:
    from .benchmark_emulators import ROOT, running_stack, wait_until, _stop
else:
    from benchmark_emulators import ROOT, running_stack, wait_until, _stop


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--signal-count", type=int, default=256)
    parser.add_argument("--interval-ms", type=int, default=50)
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--headless", action="store_true", help="Keep server running for a browser; do not open the desktop.")
    parser.add_argument("--scenario", choices=["NORMAL", "DEG-02", "DATA-QUALITY", "TRANSIENT", "NOISY-FLOW"], default="NOISY-FLOW")
    parser.add_argument("--archive-directory", type=Path, help="Retained module archives; defaults to a new runtime/raw/hybrid folder.")
    result = parser.parse_args(arguments)
    if (not 1 <= result.count <= 32 or not 8 <= result.signal_count <= 512
            or not 1 <= result.interval_ms <= 60000 or not 1 <= result.port <= 65535
            or result.seconds is not None and not 1 <= result.seconds <= 3600):
        parser.error("Use 1..32 modules, 8..512 signals, 1..60000ms, port1..65535, and a bounded duration of 1..3600 seconds.")
    return result


def main():
    args = parse_args()
    gateway_dll = ROOT / "src/Aegis.Gateway/bin/Release/net10.0/Aegis.Gateway.dll"
    desktop_exe = ROOT / "src/Aegis.Desktop/bin/Release/net10.0-windows/Aegis.Desktop.exe"
    if not gateway_dll.is_file() or not (ROOT / "ui/dist/index.html").is_file():
        raise SystemExit("Build ui and the Release gateway first; see docs/HYBRID_DASHBOARD.md.")
    if not args.headless and not desktop_exe.is_file():
        raise SystemExit("Build the Release desktop first, or select --headless.")
    archive = (args.archive_directory or ROOT / "runtime/raw" / f"hybrid-{uuid4()}").resolve()
    archive.mkdir(parents=True, exist_ok=True)
    gateway = desktop = None
    origin = f"http://127.0.0.1:{args.port}"
    try:
        with running_stack(args.count, evaluate=True, scenarios=[args.scenario, *(["NORMAL"] * (args.count - 1))],
            replay=True, signal_count=args.signal_count, interval_ms=args.interval_ms,
            poll_ms=100, archive_path=archive, isolate=True) as stack:
            environment = dict(stack.environment, AEGIS_API_URL=stack.api_url, AEGIS_GATEWAY_URL=origin,
                AEGIS_ARCHIVE_DIRECTORY=str(archive), AEGIS_DASHBOARD_TOKEN=os.environ.get("AEGIS_DASHBOARD_TOKEN") or secrets.token_hex(32),
                DOTNET_NOLOGO="1")
            with (archive / "gateway.log").open("w", encoding="utf-8") as output:
                gateway = subprocess.Popen(["dotnet", str(gateway_dll)], cwd=ROOT / "src/Aegis.Gateway", env=environment,
                    stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    with httpx.Client(base_url=origin, timeout=3, trust_env=False, follow_redirects=False) as client:
                        wait_until(lambda: client.get("/health").status_code == 200, [*stack.processes, gateway], seconds=20)
                    print(f"Shared dashboard: {origin} · synthetic emulator data only", flush=True)
                    print(f"Raw module archives retained: {archive}", flush=True)
                    print("Desktop bootstraps a local session automatically. Browser access uses AEGIS_DASHBOARD_TOKEN from your launch environment.", flush=True)
                    if not args.headless:
                        desktop = subprocess.Popen([str(desktop_exe)], cwd=ROOT, env=environment)
                    deadline = time.monotonic() + args.seconds if args.seconds else None
                    while (desktop is None or desktop.poll() is None) and (deadline is None or time.monotonic() < deadline):
                        if gateway.poll() is not None or any(process.poll() is not None for process in stack.processes):
                            raise RuntimeError("An owned hybrid process exited. Check the retained gateway/module logs.")
                        time.sleep(.2)
                finally:
                    if desktop is not None:
                        _stop(desktop)
                    _stop(gateway)
    except KeyboardInterrupt:
        pass
    print("All owned hybrid demo processes stopped; raw archives retained.", flush=True)


if __name__ == "__main__":
    main()
