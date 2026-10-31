"""Exercise actual independent CLI processes and their restart boundary."""

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest


PROJECT = Path(__file__).resolve().parents[1]
TOKEN = "independent-process-test-token-32-characters"


def available_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def read_snapshot(port, path="/v1/snapshot"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
    try:
        connection.request("GET", path, headers={"X-Aegis-Emulator-Token": TOKEN})
        response = connection.getresponse()
        if response.status != 200:
            raise AssertionError(f"Unexpected emulator status: {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.processes = []
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for process in self.processes:
            self.stop(process)

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotIn(TOKEN, stdout + stderr)

    def start(self, device_id, port, seed, scenario="NORMAL", signal_count=8):
        environment = dict(os.environ, AEGIS_EMULATOR_TOKEN=TOKEN)
        process = subprocess.Popen(
            [sys.executable, "-m", "aegis_emulator", "--device-id", device_id,
             "--port", str(port), "--seed", str(seed), "--interval-ms", "50", "--scenario", scenario,
             "--signal-count", str(signal_count)],
            cwd=PROJECT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.processes.append(process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f"Emulator stopped during startup: {stdout}{stderr}")
            try:
                return process, read_snapshot(port)
            except (OSError, http.client.HTTPException):
                time.sleep(0.02)
        self.fail("Emulator did not start within five seconds.")

    def test_two_independent_instances_survive_one_outage_and_restart(self):
        first_port, second_port = available_port(), available_port()
        while second_port == first_port:
            second_port = available_port()
        first_process, first = self.start("EQ-PROCESS-A", first_port, 11)
        _, second = self.start("EQ-PROCESS-B", second_port, 22)
        self.assertEqual(first["deviceId"], "EQ-PROCESS-A")
        self.assertEqual(second["deviceId"], "EQ-PROCESS-B")
        self.assertNotEqual(first["runId"], second["runId"])
        time.sleep(0.12)
        self.assertGreater(read_snapshot(first_port)["sequence"], first["sequence"])
        second_before_outage = read_snapshot(second_port)["sequence"]
        self.stop(first_process)
        with self.assertRaises(OSError):
            read_snapshot(first_port)
        time.sleep(0.12)
        self.assertGreater(read_snapshot(second_port)["sequence"], second_before_outage)
        _, restarted = self.start("EQ-PROCESS-A", first_port, 11)
        self.assertNotEqual(restarted["runId"], first["runId"])
        self.assertEqual(restarted["deviceId"], first["deviceId"])
        self.assertEqual(read_snapshot(second_port)["runId"], second["runId"])

    def test_cli_selects_degradation_scenario_without_disclosing_it_in_snapshot(self):
        _, payload = self.start("EQ-DEG-CLI", available_port(), 7, scenario="DEG-02")
        signals = {row["signalKey"]: row for row in payload["signals"]}
        self.assertEqual(signals["setpointFlowLpm"]["value"], 10.0)
        self.assertEqual(signals["actualFlowLpm"]["value"], 9.8)
        self.assertNotIn("scenario", payload)
        self.assertEqual(payload["protocolVersion"], "1.0")

    def test_cli_replays_hundreds_of_signals_while_legacy_snapshot_stays_unchanged(self):
        port = available_port()
        _, legacy = self.start("CMP-01.PMC-01", port, 7, scenario="TRANSIENT", signal_count=256)
        self.assertEqual(legacy["protocolVersion"], "1.0")
        self.assertEqual(len(legacy["signals"]), 8)
        time.sleep(0.6)
        page = read_snapshot(port, "/v1/batches?after=-1&limit=256")
        self.assertEqual(page["streamVersion"], "1.0")
        self.assertEqual(page["runId"], legacy["runId"])
        self.assertEqual(page["snapshots"][0]["sequence"], 0)
        self.assertTrue(all(row["protocolVersion"] == "1.1" and len(row["signals"]) == 256 for row in page["snapshots"]))
        self.assertTrue(any(row["signals"][1]["value"] == -5.0 for row in page["snapshots"]))

    def test_cli_rejects_missing_token_and_invalid_configuration_without_leaking_tokens(self):
        environment = dict(os.environ)
        environment.pop("AEGIS_EMULATOR_TOKEN", None)
        missing = subprocess.run(
            [sys.executable, "-m", "aegis_emulator", "--device-id", "EQ-A"],
            cwd=PROJECT, env=environment, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("AEGIS_EMULATOR_TOKEN", missing.stderr)
        environment["AEGIS_EMULATOR_TOKEN"] = TOKEN
        for arguments in (
            ["--port", "0"], ["--port", "65536"], ["--interval-ms", "0"],
            ["--interval-ms", "60001"], ["--device-id", "invalid device"],
            ["--scenario", "UNKNOWN"],
            ["--signal-count", "7"], ["--signal-count", "513"],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-m", "aegis_emulator", "--device-id", "EQ-A", *arguments],
                    cwd=PROJECT, env=environment, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(TOKEN, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
