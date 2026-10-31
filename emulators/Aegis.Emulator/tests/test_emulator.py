"""Independent protocol tests; run with the Python standard library."""

import http.client
import importlib
import importlib.util
import json
import math
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import UUID


TOKEN = "emulator-test-token-at-least-32-characters"
UNITS = {
    "setpointFlowLpm": "L/min",
    "actualFlowLpm": "L/min",
    "supplyPressureKpa": "kPa",
    "valveCommandPct": "%",
    "valveFeedbackPct": "%",
    "motorCurrentAmp": "A",
    "temperatureC": "degC",
    "cycleCount": "count",
}


class EmulatorTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(
            importlib.util.find_spec("aegis_emulator"),
            "The independent emulator package does not exist yet.",
        )
        return importlib.import_module("aegis_emulator")


class SnapshotTests(EmulatorTest):
    def test_snapshot_contains_only_the_versioned_raw_signal_contract(self):
        module = self.module()
        generator = module.SnapshotGenerator("EQ-SUPPLY-01", seed=7, interval_ms=1000)
        snapshot = generator.snapshot()
        self.assertEqual(set(snapshot), {
            "protocolVersion", "source", "deviceId", "runId", "sequence",
            "observedAtUtc", "deviceStateVersion", "modeId", "signals",
        })
        self.assertEqual(snapshot["protocolVersion"], "1.0")
        self.assertEqual(snapshot["source"], "SIMULATOR")
        self.assertEqual(snapshot["deviceId"], "EQ-SUPPLY-01")
        self.assertEqual(snapshot["modeId"], "PRODUCTION")
        self.assertEqual(str(UUID(snapshot["runId"])), snapshot["runId"])
        self.assertGreaterEqual(snapshot["sequence"], 0)
        self.assertGreaterEqual(snapshot["deviceStateVersion"], 0)
        self.assertEqual(datetime.fromisoformat(snapshot["observedAtUtc"]).utcoffset(), timedelta(0))
        self.assertEqual(len(snapshot["signals"]), 8)
        self.assertEqual({row["signalKey"]: row["unit"] for row in snapshot["signals"]}, UNITS)
        for row in snapshot["signals"]:
            self.assertEqual(set(row), {"signalKey", "value", "unit", "quality"})
            self.assertEqual(row["quality"], "GOOD")
            self.assertIsInstance(row["value"], (int, float))
            self.assertNotIsInstance(row["value"], bool)
            self.assertTrue(math.isfinite(row["value"]))

    def test_sequence_follows_elapsed_simulation_time_not_poll_count(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=100.0) as clock:
            generator = module.SnapshotGenerator("EQ-SUPPLY-01", seed=1, interval_ms=1000)
            first = generator.snapshot()
            clock.return_value = 100.5
            for _ in range(20):
                self.assertEqual(generator.snapshot(), first)
            clock.return_value = 103.4
            later = generator.snapshot()
        self.assertEqual(first["sequence"], 0)
        self.assertEqual(later["sequence"], 3)
        self.assertEqual(
            datetime.fromisoformat(later["observedAtUtc"]) - datetime.fromisoformat(first["observedAtUtc"]),
            timedelta(seconds=3),
        )
        self.assertNotEqual(first["signals"], later["signals"])

    def test_same_seed_reproduces_values_and_restart_changes_run_identity(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=5.0):
            first = module.SnapshotGenerator("EQ-A", seed=9, interval_ms=1000).snapshot()
            restarted = module.SnapshotGenerator("EQ-A", seed=9, interval_ms=1000).snapshot()
            other_seed = module.SnapshotGenerator("EQ-A", seed=10, interval_ms=1000).snapshot()
        self.assertEqual(first["signals"], restarted["signals"])
        self.assertNotEqual(first["runId"], restarted["runId"])
        self.assertNotEqual(first["signals"], other_seed["signals"])

    def test_invalid_generator_configuration_is_rejected(self):
        module = self.module()
        for device_id in ("", "invalid device", "x" * 65):
            with self.subTest(device_id=device_id), self.assertRaises(ValueError):
                module.SnapshotGenerator(device_id, seed=1, interval_ms=1000)
        for interval in (0, -1, 60_001, True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                module.SnapshotGenerator("EQ-A", seed=1, interval_ms=interval)


class ScenarioTests(EmulatorTest):
    def test_noisy_flow_replays_valid_short_pulses_without_cause_labels(self):
        module = self.module()
        generator = module.SnapshotGenerator("EQ-NOISE", seed=7, interval_ms=10, scenario="NOISY-FLOW")
        other = module.SnapshotGenerator("EQ-NOISE", seed=7, interval_ms=10, scenario="NOISY-FLOW")
        values = []
        for tick in range(100, 160):
            raw = generator._snapshot_at(tick, replay=True)
            self.assertEqual(raw["signals"], other._snapshot_at(tick, replay=True)["signals"])
            self.assertNotIn("scenario", raw)
            self.assertNotIn("truth", raw)
            signals = {row["signalKey"]: row for row in raw["signals"]}
            actual = signals["actualFlowLpm"]
            self.assertEqual(actual["quality"], "GOOD")
            self.assertGreater(actual["value"], 0)
            values.append(actual["value"])
            if 123 <= tick < 133:
                self.assertLess(actual["value"], 8)
                self.assertEqual(signals["supplyPressureKpa"]["value"], 220)
            else:
                self.assertGreater(actual["value"], 9)
        self.assertGreater(len(set(values)), 50)

    def test_default_normal_scenario_preserves_existing_signal_values(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=10.0) as clock:
            default = module.SnapshotGenerator("EQ-A", seed=8)
            explicit = module.SnapshotGenerator("EQ-A", seed=8, scenario="NORMAL")
            clock.return_value = 70.0
            self.assertEqual(default.snapshot()["signals"], explicit.snapshot()["signals"])

    def test_deg02_flow_deteriorates_after_sixty_seconds_and_stops_at_floor(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=100.0) as clock:
            generator = module.SnapshotGenerator("EQ-DEG", seed=1, scenario="DEG-02")
            for seconds, expected_flow in ((0, 9.8), (59, 9.8), (60, 9.8), (61, 9.72), (100, 6.6), (160, 2.0), (500, 2.0)):
                with self.subTest(seconds=seconds):
                    clock.return_value = 100.0 + seconds
                    payload = generator.snapshot()
                    self.assertEqual(len(payload["signals"]), 8)
                    signals = {row["signalKey"]: row for row in payload["signals"]}
                    self.assertEqual(signals["setpointFlowLpm"]["value"], 10.0)
                    self.assertEqual(signals["actualFlowLpm"]["value"], expected_flow)
                    self.assertTrue(all(row["quality"] == "GOOD" and math.isfinite(row["value"]) for row in signals.values()))
                    self.assertEqual(set(payload), {
                        "protocolVersion", "source", "deviceId", "runId", "sequence",
                        "observedAtUtc", "deviceStateVersion", "modeId", "signals",
                    })

    def test_data_quality_cycle_exposes_missing_bad_and_finite_bad_value_without_metadata(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=100.0) as clock:
            generator = module.SnapshotGenerator("EQ-QUALITY", seed=1, scenario="DATA-QUALITY")
            expected = ((0, "GOOD", "normal"), (14, "GOOD", "normal"),
                        (15, "MISSING", None), (29, "MISSING", None),
                        (30, "BAD", None), (44, "BAD", None),
                        (45, "GOOD", -5.0), (59, "GOOD", -5.0),
                        (60, "GOOD", "normal"), (75, "MISSING", None))
            for seconds, quality, value in expected:
                with self.subTest(seconds=seconds):
                    clock.return_value = 100.0 + seconds
                    payload = generator.snapshot()
                    signals = {row["signalKey"]: row for row in payload["signals"]}
                    actual = signals.pop("actualFlowLpm")
                    self.assertEqual(actual["quality"], quality)
                    if value == "normal":
                        self.assertGreater(actual["value"], 9.0)
                    else:
                        self.assertEqual(actual["value"], value)
                    self.assertTrue(all(row["quality"] == "GOOD" and math.isfinite(row["value"]) for row in signals.values()))
                    self.assertNotIn("scenario", payload)
                    self.assertNotIn("receivedAtUtc", payload)
                    json.dumps(payload, allow_nan=False)

    def test_unknown_scenario_is_rejected(self):
        module = self.module()
        with self.assertRaises(ValueError):
            module.SnapshotGenerator("EQ-A", scenario="UNKNOWN")


class ReplayTests(EmulatorTest):
    def test_pages_preserve_raw_samples_and_return_strictly_later_sequences(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=100.0) as clock:
            generator = module.SnapshotGenerator("CMP-01.PMC-01", seed=9, interval_ms=10)
            first = generator.snapshot()
            clock.return_value = 101.0
            page = generator.batch(-1, 50, None)
            self.assertEqual(set(page), {"streamVersion", "deviceId", "runId", "oldestSequence", "latestSequence", "snapshots"})
            self.assertEqual(page["streamVersion"], "1.0")
            self.assertEqual(page["deviceId"], "CMP-01.PMC-01")
            self.assertEqual(page["runId"], first["runId"])
            self.assertEqual((page["oldestSequence"], page["latestSequence"]), (0, 100))
            self.assertEqual([row["sequence"] for row in page["snapshots"]], list(range(50)))
            self.assertEqual(page["snapshots"][0], dict(first, protocolVersion="1.1"))
            second = generator.batch(49, 256, generator.run_id)
            self.assertEqual([row["sequence"] for row in second["snapshots"]], list(range(50, 101)))
            self.assertEqual(second["snapshots"][-1], dict(generator.snapshot(), protocolVersion="1.1"))
            self.assertEqual(generator.batch(100, 256, None)["snapshots"], [])
            self.assertEqual(generator.batch(-1, 50, None), page)

    def test_retention_bounds_and_changed_run_allow_restart_catchup(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=0.0) as clock:
            generator = module.SnapshotGenerator("CMP-01.CMC-01", interval_ms=10)
            clock.return_value = 50.0
            page = generator.batch(-1, 256, None)
            self.assertEqual((page["oldestSequence"], page["latestSequence"]), (905, 5000))
            self.assertEqual([row["sequence"] for row in page["snapshots"]], list(range(905, 1161)))
            self.assertLess(len(json.dumps(page).encode("utf-8")), 1_048_576)
            with self.assertRaises(ValueError):
                generator.batch(5001, 256, generator.run_id)
            changed_run = generator.batch(999_999, 2, "00000000-0000-4000-8000-000000000000")
            self.assertEqual([row["sequence"] for row in changed_run["snapshots"]], [905, 906])

    def test_batch_rejects_invalid_cursor_limit_and_run_identity(self):
        generator = self.module().SnapshotGenerator("EQ-A")
        for after, limit, run_id in ((-2, 1, None), (True, 1, None), (0.0, 1, None),
                                     (-1, 0, None), (-1, 257, None), (-1, True, None),
                                     (-1, 1, ""), (-1, 1, "not-a-uuid")):
            with self.subTest(after=after, limit=limit, run_id=run_id), self.assertRaises(ValueError):
                generator.batch(after, limit, run_id)

    def test_transient_is_preserved_between_slower_latest_reads(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=0.0) as clock:
            generator = module.SnapshotGenerator("CMP-01.PMC-01", interval_ms=10, scenario="TRANSIENT")
            self.assertGreater(generator.snapshot()["signals"][1]["value"], 9.0)
            clock.return_value = 2.0
            self.assertGreater(generator.snapshot()["signals"][1]["value"], 9.0)
            page = generator.batch(-1, 256, None)
            abnormal = [row["sequence"] for row in page["snapshots"] if row["signals"][1]["value"] == -5.0]
            self.assertEqual(abnormal, [*range(40, 50), *range(140, 150)])
            for row in page["snapshots"]:
                self.assertEqual(row["signals"][1]["quality"], "GOOD")
                self.assertNotIn("scenario", row)
                self.assertEqual(len(row["signals"]), 8)

    def test_hundreds_of_replay_signals_are_byte_bounded_without_losing_sequences(self):
        module = self.module()
        with patch("aegis_emulator.core.time.monotonic", return_value=0.0) as clock:
            generator = module.SnapshotGenerator("CMP-01.PMC-01", interval_ms=10, signal_count=256)
            clock.return_value = 1.0
            legacy = generator.snapshot()
            self.assertEqual(legacy["protocolVersion"], "1.0")
            self.assertEqual(len(legacy["signals"]), 8)
            after = -1
            received = []
            while after < 100:
                page = generator.batch(after, 256, generator.run_id)
                self.assertLessEqual(len(json.dumps(page, allow_nan=False, separators=(",", ":")).encode("utf-8")), 900_000)
                self.assertGreater(len(page["snapshots"]), 0)
                self.assertLess(len(page["snapshots"]), 101)
                for row in page["snapshots"]:
                    self.assertEqual(row["protocolVersion"], "1.1")
                    self.assertEqual(len(row["signals"]), 256)
                    self.assertEqual({signal["signalKey"]: signal["unit"] for signal in row["signals"][:8]}, UNITS)
                    self.assertEqual(row["signals"][8]["signalKey"], "aux0001")
                    self.assertEqual(row["signals"][-1]["signalKey"], "aux0248")
                    self.assertTrue(all(signal["unit"] == "count" and signal["quality"] == "GOOD"
                                        and math.isfinite(signal["value"]) for signal in row["signals"][8:]))
                received.extend(row["sequence"] for row in page["snapshots"])
                after = received[-1]
            self.assertEqual(received, list(range(101)))

    def test_signal_count_rejects_invalid_bounds_and_always_fits_one_replay_frame(self):
        module = self.module()
        for count in (0, 7, 513, 8.0, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                module.SnapshotGenerator("EQ-A", signal_count=count)
        generator = module.SnapshotGenerator("EQ-A", signal_count=512)
        page = generator.batch(-1, 1, None)
        self.assertEqual(len(page["snapshots"]), 1)
        self.assertEqual(len(page["snapshots"][0]["signals"]), 512)
        self.assertLess(len(json.dumps(page).encode("utf-8")), 900_000)


class HttpTests(EmulatorTest):
    def setUp(self):
        module = self.module()
        self.generator = module.SnapshotGenerator("EQ-HTTP", seed=1, interval_ms=1000)
        self.server = module.create_server(self.generator, TOKEN, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def request(self, method="GET", path="/v1/snapshot", *, token=TOKEN, origin=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        if token is not None:
            headers["X-Aegis-Emulator-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_authenticated_snapshot_uses_loopback_and_json(self):
        status, body, headers = self.request()
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["deviceId"], "EQ-HTTP")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertLess(len(body), 65_536)

    def test_missing_wrong_and_duplicate_tokens_are_rejected(self):
        for token in (None, "incorrect"):
            with self.subTest(token=token):
                status, body, _ = self.request(token=token)
                self.assertEqual(status, 401)
                self.assertNotIn(TOKEN.encode(), body)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest("GET", "/v1/snapshot")
            connection.putheader("X-Aegis-Emulator-Token", TOKEN)
            connection.putheader("X-Aegis-Emulator-Token", TOKEN)
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 401)
        finally:
            connection.close()

    def test_browser_origins_and_external_hosts_are_rejected(self):
        for origin in ("https://example.com", "null", ""):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(origin=origin)[0], 403)
        for host in ("example.com", "127.0.0.1.example.com", "127.0.0.1:999999", "127.0.0.1@evil.test"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host=host)[0], 400)

    def test_only_get_snapshot_is_exposed(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method)[0], 405)
        for path in ("/", "/v1/health", "/v1/snapshot?command=start", "/v1/start"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 404)
        self.assertEqual(self.request()[0], 200)

    def test_invalid_token_configuration_fails_before_binding(self):
        module = self.module()
        generator = module.SnapshotGenerator("EQ-A", seed=1, interval_ms=1000)
        for token in ("", "short", "x" * 32 + "\n", "x" * 32 + "\r"):
            with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                module.create_server(generator, token, port=0)

    def test_replay_http_pages_are_bounded_and_keep_the_same_auth_boundary(self):
        with patch("aegis_emulator.core.time.monotonic", return_value=self.generator.started_monotonic + 300):
            status, body, headers = self.request(path="/v1/batches?after=-1&limit=256")
            self.assertEqual(status, 200)
            page = json.loads(body)
            self.assertEqual((page["oldestSequence"], page["latestSequence"]), (0, 300))
            self.assertEqual(len(page["snapshots"]), 256)
            self.assertEqual(page["snapshots"][0]["sequence"], 0)
            self.assertEqual(page["snapshots"][-1]["sequence"], 255)
            self.assertLess(len(body), 1_048_576)
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual(self.request(path=f"/v1/batches?after=255&limit=256&runId={self.generator.run_id}")[0], 200)
            self.assertEqual(self.request(path="/v1/batches?after=301&limit=256")[0], 400)
            status, changed, _ = self.request(path="/v1/batches?after=10000&limit=1&runId=00000000-0000-4000-8000-000000000000")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(changed)["snapshots"][0]["sequence"], 0)
        path = "/v1/batches?after=-1&limit=256"
        self.assertEqual(self.request(path=path, token=None)[0], 401)
        self.assertEqual(self.request(path=path, origin="null")[0], 403)
        self.assertEqual(self.request(path=path, host="example.com")[0], 400)

    def test_replay_http_rejects_missing_duplicate_unknown_and_invalid_parameters(self):
        queries = ("", "?after=-1", "?limit=1", "?after=-1&limit=1&unknown=1",
                   "?after=-1&limit=1&limit=2", "?after=-1&after=0&limit=1",
                   "?after=-1&limit=1&runId=&runId=", "?after=&limit=1",
                   "?after=-2&limit=1", "?after=0.0&limit=1", "?after=+0&limit=1",
                   "?after=-1&limit=0", "?after=-1&limit=257", "?after=-1&limit=1.0",
                   "?after=-1&limit=1&runId=not-a-uuid", "?after=-1&limit=1&",
                   "?after=-1&limit=1&runId=", "?after=-1&limit=%FF")
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.request(path="/v1/batches" + query)[0], 400)


if __name__ == "__main__":
    unittest.main()
