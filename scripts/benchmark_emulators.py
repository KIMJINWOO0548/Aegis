"""Run bounded, isolated emulator/API processes and report measured local behavior."""

import argparse
import ctypes
import json
import math
import os
import platform
import secrets
import site
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx


ROOT = Path(__file__).resolve().parents[1]


def validate_run(count, seconds):
    if type(count) is not int or not 1 <= count <= 32 or type(seconds) not in {int, float} or not 0 < seconds <= 60:
        raise ValueError("Use 1..32 emulators and a measured window of at most 60 seconds.")


def latency_summary(values):
    ordered = sorted(values)
    return {"samples": len(values), "p50Ms": round(ordered[max(0, math.ceil(len(values) * .50) - 1)], 3) if values else None,
            "p95Ms": round(ordered[max(0, math.ceil(len(values) * .95) - 1)], 3) if values else None}


def wait_until(predicate, processes, seconds=15):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("An owned emulator/API process exited unexpectedly.")
        try:
            value = predicate()
            if value:
                return value
        except (httpx.HTTPError, ValueError, KeyError) as error:
            last_error = type(error).__name__
        time.sleep(.1)
    raise TimeoutError(f"Local stack condition did not become ready: {last_error or 'condition false'}")


def _ports(count):
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_pid_exit(pid, seconds=10):
    """Wait only for a previously owned worker PID; never terminate unrelated processes."""
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return
        try:
            if kernel.WaitForSingleObject(handle, int(seconds * 1000)) != 0:
                raise TimeoutError("An owned module worker did not exit after its supervisor.")
        finally:
            kernel.CloseHandle(handle)
    else:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)
        raise TimeoutError("An owned module worker did not exit after its supervisor.")


@contextmanager
def running_stack(count, *, evaluate=False, scenarios=None, replay=False, signal_count=8,
                  interval_ms=1000, poll_ms=100, archive_path=None, isolate=False):
    validate_run(count, 1)
    scenarios = scenarios or ["NORMAL"] * count
    if len(scenarios) != count or any(value not in {"NORMAL", "DEG-02", "DATA-QUALITY", "TRANSIENT", "NOISY-FLOW"} for value in scenarios):
        raise ValueError("Select one supported scenario per emulator.")
    if type(signal_count) is not int or not 8 <= signal_count <= 512 or type(interval_ms) is not int or not 1 <= interval_ms <= 60000 or type(poll_ms) is not int or not 50 <= poll_ms <= 60000:
        raise ValueError("Use 8..512 numeric signals, 1..60000ms sampling and 50..60000ms polling.")
    if not replay and (signal_count != 8 or interval_ms != 1000 or archive_path is not None):
        raise ValueError("Custom sampling/signals/archive require replay ingestion.")
    children, outputs = [], []
    with tempfile.TemporaryDirectory(prefix="aegis-emulator-") as directory:
        workspace = Path(directory)
        ports = _ports(count + 1)
        ids = [f"EQ-SUPPLY-{index + 1:02d}" for index in range(count)]
        env = dict(os.environ, AEGIS_API_TOKEN=secrets.token_hex(32),
                   AEGIS_EMULATOR_TOKEN=secrets.token_hex(32), PYTHONIOENCODING="utf-8")
        # Child code paths are explicit; no editable installation or current-directory imports required.
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), *site.getsitepackages()])
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def launch(arguments, cwd, label):
            output = (workspace / f"{label}-{len(children)}.log").open("w", encoding="utf-8")
            outputs.append(output)
            # The Windows venv redirector spawns a child; use the real interpreter for ownership and CPU/RSS.
            process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), *arguments], cwd=cwd, env=env,
                                       stdout=output, stderr=output, creationflags=creation_flags)
            children.append(process)
            return process

        emulators = [None] * count

        def start_emulator(index):
            if emulators[index] is not None and emulators[index].poll() is None:
                raise RuntimeError("Emulator is already running.")
            emulators[index] = launch(["-m", "aegis_emulator", "--device-id", ids[index],
                "--port", str(ports[index]), "--seed", str(index + 1), "--interval-ms", str(interval_ms),
                "--signal-count", str(signal_count),
                "--scenario", scenarios[index]],
                ROOT / "emulators/Aegis.Emulator", f"emulator-{index}")
            return emulators[index]

        def stop_emulator(index):
            _stop(emulators[index])

        stack = SimpleNamespace(device_ids=ids, processes=children, start_emulator=start_emulator,
                                stop_emulator=stop_emulator, environment=env)
        class StackView:
            def __getattr__(self, name):
                if name == "active_processes":
                    return [p for p in children if p.poll() is None]
                return getattr(stack, name)
        view = StackView()
        try:
            for index in range(count):
                start_emulator(index)
            config = workspace / "sources.json"
            sources = [{"deviceId": device, "baseUrl": f"http://127.0.0.1:{ports[index]}",
                "tokenEnvironment": "AEGIS_EMULATOR_TOKEN"} for index, device in enumerate(ids)]
            if replay:
                for index, source in enumerate(sources):
                    source.update(transport="replay", pollIntervalMs=poll_ms,
                                  equipmentId="CMP-DEMO-01", moduleId=f"MODULE-{index + 1:02d}")
            config.write_text(json.dumps({"sources": sources}), encoding="utf-8")
            api_arguments = ["-m", "aegis.api", "--emulators", str(config), "--port", str(ports[-1])]
            stack.archive_path = Path(archive_path).resolve() if archive_path else workspace / "runtime/raw.sqlite3"
            if replay:
                api_arguments.extend(["--archive-directory" if isolate else "--archive", str(stack.archive_path)])
            if isolate:
                api_arguments.append("--isolate")
            if evaluate:
                api_arguments.append("--evaluate")
            stack.api = None
            def start_api():
                if stack.api is not None and stack.api.poll() is None:
                    raise RuntimeError("API is already running.")
                stack.api = launch(api_arguments, workspace, "api")
                return stack.api
            stack.start_api = start_api
            stack.worker_pids = set()
            def stop_api():
                if isolate and stack.api.poll() is None and hasattr(stack, "client"):
                    try:
                        stack.worker_pids.update(row["workerPid"] for row in stack.client.get("/v1/ingestion").json() if row["workerPid"])
                    except (httpx.HTTPError, RuntimeError):
                        pass
                _stop(stack.api)
                for pid in stack.worker_pids:
                    wait_pid_exit(pid)
            stack.stop_api = stop_api
            start_api()
            stack.api_url = f"http://127.0.0.1:{ports[-1]}"
            with httpx.Client(base_url=stack.api_url, headers={"X-Aegis-Token": env["AEGIS_API_TOKEN"]},
                              timeout=3, trust_env=False, follow_redirects=False) as client:
                stack.client = client
                wait_until(lambda: client.get("/v1/health").status_code == 200, children, seconds=20)
                try:
                    yield view
                finally:
                    if isolate:
                        stack.stop_api()
        except BaseException:
            if hasattr(stack, "client"):
                try:
                    print("Final connections:", stack.client.get("/v1/connections").json(), file=sys.stderr)
                    print("Final ingestion:", stack.client.get("/v1/ingestion").json(), file=sys.stderr)
                except (httpx.HTTPError, RuntimeError):
                    pass
            for output in outputs:
                output.flush()
                print(Path(output.name).read_text(encoding="utf-8")[-3000:], file=sys.stderr)
            raise
        finally:
            if isolate and hasattr(stack, "stop_api"):
                stack.stop_api()
            for process in reversed(children):
                _stop(process)
            for output in outputs:
                output.close()


def process_usage(process):
    """Windows CPU seconds and current working set from the owned process handle."""
    if os.name != "nt":
        return {"cpuSeconds": None, "workingSetBytes": None}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x410, False, process.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
            raise ctypes.WinError(ctypes.get_last_error())
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        cpu = sum((value.dwHighDateTime << 32) + value.dwLowDateTime for value in times[2:]) / 10_000_000
        return {"cpuSeconds": cpu, "workingSetBytes": counters.WorkingSetSize}
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


def benchmark(count, seconds, *, replay=False, signal_count=8, interval_ms=1000, poll_ms=100, isolate=False):
    validate_run(count, seconds)
    with running_stack(count, replay=replay, signal_count=signal_count, interval_ms=interval_ms, poll_ms=poll_ms, isolate=isolate) as stack:
        def connected():
            rows = stack.client.get("/v1/connections").json()
            return rows if len(rows) == count and all(row["acceptedBatches"] > 0 if replay else row["state"] == "CONNECTED" for row in rows) else None
        initial = wait_until(connected, stack.processes)
        if replay:
            time.sleep(3)
            initial = stack.client.get("/v1/connections").json()
            start_ingestion = stack.client.get("/v1/ingestion").json()
        measured_processes = list(stack.processes)
        if isolate:
            metadata = stack.client.get("/v1/ingestion").json()
            measured_processes.extend(SimpleNamespace(pid=row["workerPid"]) for row in metadata)
        usage_before = [process_usage(p) for p in measured_processes]
        api_latencies, poll_latencies, commit_latencies, observed_lags = [], [], [], []
        peak_memory = [row["workingSetBytes"] or 0 for row in usage_before]
        seen, seen_commits = set(), set()
        started = time.perf_counter()
        deadline = started + seconds
        final = initial
        while time.perf_counter() < deadline:
            request_started = time.perf_counter()
            response = stack.client.get("/v1/connections")
            response.raise_for_status()
            api_latencies.append((time.perf_counter() - request_started) * 1000)
            final = response.json()
            if replay:
                for meta in stack.client.get("/v1/ingestion").json():
                    commit_key = (meta["deviceId"], meta["lastCommittedRunId"], meta["lastCommittedSequence"])
                    if meta["archiveCommitMs"] is not None and commit_key not in seen_commits:
                        seen_commits.add(commit_key)
                        commit_latencies.append(meta["archiveCommitMs"])
                    if meta["observedLagMs"] is not None:
                        observed_lags.append(meta["observedLagMs"])
            for row in final:
                key = (row["deviceId"], row["acceptedBatches"])
                if row["pollLatencyMs"] is not None and key not in seen:
                    seen.add(key)
                    poll_latencies.append(row["pollLatencyMs"])
            for index, process in enumerate(measured_processes):
                peak_memory[index] = max(peak_memory[index], process_usage(process)["workingSetBytes"] or 0)
            time.sleep(min(.1, max(0, deadline - time.perf_counter())))
        elapsed = time.perf_counter() - started
        usage_after = [process_usage(p) for p in measured_processes]
        accepted = sum(row["acceptedBatches"] for row in final) - sum(row["acceptedBatches"] for row in initial)
        cpu_seconds = [after["cpuSeconds"] - before["cpuSeconds"] if after["cpuSeconds"] is not None else None
                       for before, after in zip(usage_before, usage_after)]
        result = {"emulatorCount": count, "signalsPerEmulator": signal_count,
            "sourceSampleIntervalMs": interval_ms, "pollIntervalSeconds": poll_ms / 1000 if replay else 1,
            "measuredElapsedSeconds": round(elapsed, 3), "acceptedBatchesDuringWindow": accepted,
            "acceptedSignalValuesPerSecond": round(accepted * signal_count / elapsed, 3),
            "rejectedBatches": sum(row["rejectedBatches"] for row in final),
            "droppedBatches": sum(row["droppedBatches"] for row in final),
            "finalStates": [row["state"] for row in final], "apiLatency": latency_summary(api_latencies),
            "collectorPollLatency": latency_summary(poll_latencies),
            "apiCpuPercentOfOneCore": round(cpu_seconds[count] / elapsed * 100, 3) if cpu_seconds[count] is not None else None,
            "emulatorsCpuPercentOfOneCore": round(sum(cpu_seconds[:count]) / elapsed * 100, 3) if cpu_seconds[0] is not None else None,
            "apiPeakSampledWorkingSetMiB": round(peak_memory[count] / 1048576, 3),
            "emulatorsPeakSampledWorkingSetMiB": round(sum(peak_memory[:count]) / 1048576, 3)}
        if isolate:
            result.update(moduleWorkersCpuPercentOfOneCore=round(sum(cpu_seconds[count + 1:]) / elapsed * 100, 3) if cpu_seconds[0] is not None else None,
                          moduleWorkersPeakSampledWorkingSetMiB=round(sum(peak_memory[count + 1:]) / 1048576, 3))
        if replay:
            import sqlite3
            end_ingestion = stack.client.get("/v1/ingestion").json()
            stack.stop_api()
            archives = list(stack.archive_path.glob("*.sqlite3")) if isolate else [stack.archive_path]
            cursor_rows, gaps, integrity, archive_bytes = [], 0, [], 0
            for path in archives:
                with closing(sqlite3.connect(path, timeout=10)) as db:
                    cursor_rows.extend(db.execute("SELECT device_id,COUNT(*),MIN(sequence),MAX(sequence) FROM raw_batches GROUP BY device_id").fetchall())
                    integrity.append(db.execute("PRAGMA integrity_check").fetchone()[0])
                    gaps += db.execute("SELECT COUNT(*) FROM ingest_gaps").fetchone()[0]
                archive_bytes += path.stat().st_size + (Path(str(path) + "-wal").stat().st_size if Path(str(path) + "-wal").exists() else 0)
            result.update(archiveIntegrity="ok" if all(value == "ok" for value in integrity) and len(archives) == (count if isolate else 1) else "failed",
                archiveGapRanges=gaps, contiguousCommittedPrefixes=all(number == last - first + 1 and first == 0 for _, number, first, last in cursor_rows),
                totalArchivedBatches=sum(row[1] for row in cursor_rows))
            result.update(archiveBytes=archive_bytes,
                          warmupSeconds=3, beginningDataAgeMs=[row["observedLagMs"] for row in start_ingestion],
                          endingDataAgeMs=[row["observedLagMs"] for row in end_ingestion],
                          endingPendingBatches=[row["pendingBatches"] for row in end_ingestion],
                          archiveCommitLatency=latency_summary(commit_latencies),
                          observedDataAge=latency_summary(observed_lags))
    result["ownedChildrenCleanedUp"] = all(p.poll() is not None for p in stack.processes)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[1, 4, 8])
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--replay", action="store_true", help="Measure durable hundreds-of-tags replay ingestion.")
    parser.add_argument("--isolate", action="store_true", help="Measure independent module workers and integration API.")
    parser.add_argument("--signal-count", type=int)
    parser.add_argument("--interval-ms", type=int)
    parser.add_argument("--poll-ms", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.isolate and not args.replay:
        parser.error("--isolate requires --replay.")
    signal_count = args.signal_count if args.signal_count is not None else (256 if args.replay else 8)
    interval_ms = args.interval_ms if args.interval_ms is not None else (10 if args.replay else 1000)
    args.output = args.output or ROOT / ("docs/evidence/isolated-benchmark.json" if args.isolate else "docs/evidence/replay-benchmark.json" if args.replay else "docs/evidence/emulator-benchmark.json")
    for count in args.counts:
        validate_run(count, args.seconds)
    report = {"generatedAtUtc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "python": platform.python_version(), "logicalCpuCount": os.cpu_count(),
        "scope": "Synthetic replay acquisition and SQLite WAL/FULL; WPF/advisory excluded." if args.replay else "Local synthetic HTTP acquisition only; WPF/ML/database excluded.",
        "limits": "Short single trials; not saturation, soak, hard real-time or customer hardware performance. Working set is sampled. Replay preserves committed prefixes; final source tails are not claimed drained.",
        "runs": []}
    for count in args.counts:
        report["runs"].append(benchmark(count, args.seconds, replay=args.replay, signal_count=signal_count,
                                        interval_ms=interval_ms, poll_ms=args.poll_ms, isolate=args.isolate))
        print(f"Measured {count} emulator(s); owned children stopped.", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
