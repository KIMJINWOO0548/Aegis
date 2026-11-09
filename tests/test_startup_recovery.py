"""Startup writes are bounded even when their own asyncio loop cannot make progress."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("startup_recovery_harness.py")
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux pipe sizing and child subreaping.")


def _run_harness(tmp_path, scenario):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT),
                       PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen([sys.executable, str(HARNESS), scenario, str(tmp_path)],
        cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        # Last-resort protection if the external supervisor itself fails.
        registry = tmp_path / "events.jsonl"
        if registry.exists():
            entries = []
            for line in registry.read_text().splitlines():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            for entry in entries:
                if entry.get("event") == "application_spawned":
                    # Signal a recorded group only if a current, registered member
                    # still has its original starttime and that process group.
                    for member in entries:
                        if member.get("event") not in {"application_spawned", "worker_spawned"}:
                            continue
                        try:
                            fields = Path(f"/proc/{member['pid']}/stat").read_text().split(") ", 1)[1].split()
                        except FileNotFoundError:
                            continue
                        if fields[19] == member["starttime"] and int(fields[2]) == entry["pid"]:
                            try:
                                os.killpg(entry["pid"], signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            break
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        pytest.fail("The external startup supervisor exceeded its own cleanup deadline.")
    assert process.returncode == 0, "The external startup supervisor failed before reporting a result."
    result = json.loads(stdout)
    assert result["descendants_reaped"], "An owned test descendant was not reaped."
    writes = result["configuration_writes"]
    assert writes and all(row["config_valid"] and row["paths_valid"]
                          and row["total_wire_bytes"] > row["capacity"] == 4096 for row in writes), (
        "The scenario did not establish a valid configuration exceeding the actual pipe capacity.")
    return result


def test_blocked_configuration_does_not_block_the_shared_event_loop(tmp_path):
    result = _run_harness(tmp_path, "responsiveness")
    assert all(row["payload_bytes"] > row["capacity"] and row["prefill_bytes"] == 0
               for row in result["configuration_writes"])
    assert not result["deadline_expired"], (
        "Startup configuration blocked its event loop beyond the external wall-clock deadline.")
    assert result["inner"] is not None and result["inner"]["ok"]
    assert result["inner"]["ticks"] >= 3
    assert result["inner"]["attempts"] == 1
    assert result["inner"]["all_workers_exited"]


def test_stop_reaps_a_worker_whose_configuration_write_is_still_blocked(tmp_path):
    result = _run_harness(tmp_path, "stop")
    assert all(row["payload_bytes"] > row["capacity"] and row["prefill_bytes"] == 0
               for row in result["configuration_writes"])
    assert not result["deadline_expired"], "Stopping a blocked startup exceeded the external deadline."
    assert result["inner"] is not None and result["inner"]["ok"]
    assert result["inner"]["attempts"] == 1
    assert result["inner"]["all_workers_exited"]


def test_valid_stdout_frames_cannot_extend_the_configuration_deadline_or_recovery_budget(tmp_path):
    result = _run_harness(tmp_path, "budget")
    assert all(row["payload_bytes"] > row["capacity"] and row["prefill_bytes"] == 0
               for row in result["configuration_writes"])
    assert not result["deadline_expired"], "Pending configuration or premature frames bypassed finite recovery."
    assert result["inner"] is not None and result["inner"]["ok"]
    assert result["inner"]["attempts"] == 2
    assert result["registered_workers"] == 2
    assert len(result["configuration_writes"]) == 2
    assert result["inner"]["all_workers_exited"]
    entries = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(entry["event"] == "premature_frame_written" for entry in entries)


def test_startup_recovery_preserves_two_real_replay_workers_and_original_archive_rows(tmp_path):
    result = _run_harness(tmp_path, "peer")
    assert all(row["prefill_bytes"] == row["capacity"] and row["payload_bytes"] < row["capacity"]
               for row in result["configuration_writes"])
    assert not result["deadline_expired"], "A blocked replacement froze two-module recovery."
    assert result["inner"] is not None and result["inner"]["ok"]
    assert result["inner"]["attempts"] == result["registered_workers"] == 4
    assert result["inner"]["peer_pid_preserved"]
    assert result["inner"]["peer_committed_while_config_pending"]
    assert result["inner"]["original_prefix_and_receipts_preserved"]
    assert result["inner"]["contiguous_archives"] and result["inner"]["sqlite_integrity"]
    assert result["inner"]["all_workers_exited"]
