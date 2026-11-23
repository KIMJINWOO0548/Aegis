"""Run independent emulators, raw archives, C# review, and the shared React dashboard."""

import argparse
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from uuid import uuid4

import httpx

if __package__:
    from .benchmark_emulators import ROOT, running_stack, wait_until, _ports, _stop
else:
    from benchmark_emulators import ROOT, running_stack, wait_until, _ports, _stop


class SyntheticProducerRecovery:
    """Restart owned loopback producers independently, within a per-session budget."""

    MAX_RESTARTS = 3

    def __init__(self, stack, report):
        self.stack = stack
        self.report = report
        self.attempts = [0] * len(stack.device_ids)
        self.retry_at = [None] * len(stack.device_ids)
        self.exhausted = set()

    def check(self, now):
        for index, (device, producer) in enumerate(zip(self.stack.device_ids, self.stack.emulator_processes, strict=True)):
            exit_code = producer.poll()
            if exit_code is None or index in self.exhausted:
                continue
            used = self.attempts[index]
            if used == self.MAX_RESTARTS:
                self.exhausted.add(index)
                self.report(f"SYNTHETIC_PRODUCER_RESTART_EXHAUSTED · {device} · exit {exit_code}")
                continue
            if self.retry_at[index] is None:
                self.retry_at[index] = now + 2 ** used
                self.report(f"SYNTHETIC_PRODUCER_EXITED · {device} · exit {exit_code}")
                continue
            if now < self.retry_at[index]:
                continue
            self.attempts[index] += 1
            try:
                self.stack.start_emulator(index)
            except OSError:
                self.retry_at[index] = now + 2 ** self.attempts[index]
                self.report(f"SYNTHETIC_PRODUCER_RESTART_FAILED · {device} · attempt {self.attempts[index]}")
            else:
                self.retry_at[index] = None
                self.report(f"SYNTHETIC_PRODUCER_RESTART_STARTED · {device} · attempt {self.attempts[index]}")


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--signal-count", type=int, default=256)
    parser.add_argument("--interval-ms", type=int, default=50)
    parser.add_argument("--seconds", type=int)
    port = parser.add_mutually_exclusive_group()
    port.add_argument("--port", type=int, default=8780)
    port.add_argument("--auto-port", action="store_true", help="Select a free loopback port.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Keep server running for a browser; do not open the desktop.")
    mode.add_argument("--tray", action="store_true", help="Start the Windows desktop in the tray.")
    parser.add_argument("--overlay", action="store_true", help="Show the read-only overlay when the desktop starts.")
    parser.add_argument("--desktop-state-directory", type=Path, help="Optional directory for local desktop preferences and the WebView profile.")
    parser.add_argument("--scenario", choices=["NORMAL", "DEG-02", "DATA-QUALITY", "TRANSIENT", "NOISY-FLOW"], default="NOISY-FLOW")
    parser.add_argument("--archive-directory", type=Path, help="Retained module archives; defaults to a new runtime/raw/hybrid folder.")
    parser.add_argument("--archive-sessions-root", type=Path, help="Portable launcher's owned runs directory.")
    result = parser.parse_args(arguments)
    if (not 1 <= result.count <= 32 or not 8 <= result.signal_count <= 512
            or not 1 <= result.interval_ms <= 60000 or not 1 <= result.port <= 65535
            or result.seconds is not None and not 1 <= result.seconds <= 3600):
        parser.error("Use 1..32 modules, 8..512 signals, 1..60000ms, port 1..65535, and a bounded duration of 1..3600 seconds.")
    if result.headless and (result.overlay or result.desktop_state_directory is not None):
        parser.error("--overlay and --desktop-state-directory require the desktop.")
    if result.archive_sessions_root is not None and result.archive_directory is None:
        parser.error("--archive-sessions-root requires --archive-directory for the current run.")
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
    origin = f"http://127.0.0.1:{_ports(1)[0] if args.auto_port else args.port}"
    try:
        with running_stack(args.count, evaluate=True, scenarios=[args.scenario, *(["NORMAL"] * (args.count - 1))],
            replay=True, signal_count=args.signal_count, interval_ms=args.interval_ms,
            poll_ms=100, archive_path=archive, isolate=True, bind_stored_history=True,
            archive_sessions_root=args.archive_sessions_root) as stack:
            environment = dict(stack.environment, AEGIS_API_URL=stack.api_url, AEGIS_GATEWAY_URL=origin,
                AEGIS_ARCHIVE_DIRECTORY=str(archive), AEGIS_DASHBOARD_TOKEN=os.environ.get("AEGIS_DASHBOARD_TOKEN") or secrets.token_hex(32),
                DOTNET_NOLOGO="1")
            if args.desktop_state_directory is not None:
                state = args.desktop_state_directory.resolve()
                environment.update(AEGIS_COMPANION_SETTINGS_PATH=str(state / "companion-settings.json"),
                                   AEGIS_DESKTOP_PROFILE_DIR=str(state / "WebView2Profile"))
            gateway_environment = dict(environment)
            if args.archive_sessions_root is not None:
                gateway_environment["AEGIS_PRODUCT_STATE_DIRECTORY"] = str(args.archive_sessions_root.resolve().parent)
            with (archive / "gateway.log").open("w", encoding="utf-8") as output:
                gateway = subprocess.Popen(["dotnet", str(gateway_dll)], cwd=ROOT / "src/Aegis.Gateway", env=gateway_environment,
                    stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    with httpx.Client(base_url=origin, timeout=3, trust_env=False, follow_redirects=False) as client:
                        wait_until(lambda: client.get("/health").status_code == 200, [*stack.processes, gateway], seconds=20)
                    print(f"Shared dashboard: {origin} · synthetic emulator data only", flush=True)
                    print(f"Raw module archives retained: {archive}", flush=True)
                    print("Desktop bootstraps a local session automatically. Browser access uses AEGIS_DASHBOARD_TOKEN from your launch environment.", flush=True)
                    if not args.headless:
                        desktop_args = [str(desktop_exe)]
                        if args.tray:
                            desktop_args.append("--tray")
                        if args.overlay:
                            desktop_args.append("--overlay")
                        desktop = subprocess.Popen(desktop_args, cwd=ROOT, env=environment)
                    deadline = time.monotonic() + args.seconds if args.seconds else None
                    recovery = SyntheticProducerRecovery(stack, lambda message: print(message, flush=True))
                    while (desktop is None or desktop.poll() is None) and (deadline is None or time.monotonic() < deadline):
                        if gateway.poll() is not None or stack.api.poll() is not None:
                            raise RuntimeError("The local API or Gateway exited. Check the retained launch logs.")
                        recovery.check(time.monotonic())
                        time.sleep(.2)
                finally:
                    if desktop is not None:
                        _stop(desktop)
                    _stop(gateway)
    except KeyboardInterrupt:
        pass
    print("All owned hybrid demo processes stopped; raw archives retained.", flush=True)


if __name__ == "__main__":
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    main()
