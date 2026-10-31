"""Legacy raw snapshots and bounded synthetic replay; no application or control imports."""

import hmac
import json
import math
import random
import re
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl
from uuid import UUID, uuid4


DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
LOCAL_HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?\Z", re.IGNORECASE)
SCENARIOS = ("NORMAL", "DEG-02", "DATA-QUALITY", "TRANSIENT", "NOISY-FLOW")


class SnapshotGenerator:
    """Sample identity advances with monotonic elapsed time, even without readers."""

    def __init__(self, device_id: str, *, seed: int = 1, interval_ms: int = 1000,
                 scenario: str = "NORMAL", signal_count: int = 8):
        if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
            raise ValueError("device-id must be 1-64 ASCII letters, digits, dots, underscores or hyphens.")
        if type(interval_ms) is not int or not 1 <= interval_ms <= 60_000:
            raise ValueError("interval-ms must be an integer from 1 to 60000.")
        if type(seed) is not int:
            raise ValueError("seed must be an integer.")
        if type(signal_count) is not int or not 8 <= signal_count <= 512:
            raise ValueError("signal-count must be an integer from 8 to 512.")
        if scenario not in SCENARIOS:
            raise ValueError("scenario must be " + ", ".join(SCENARIOS) + ".")
        self.device_id = device_id
        self.run_id = str(uuid4())
        self.interval_ms = interval_ms
        self.interval_seconds = interval_ms / 1000
        self.started_monotonic = time.monotonic()
        self.started_utc = datetime.now(timezone.utc)
        self.phase = random.Random(seed).uniform(-math.pi, math.pi)
        self.seed = seed
        self.scenario = scenario
        self.signal_count = signal_count

    def snapshot(self) -> dict:
        sequence = max(0, int((time.monotonic() - self.started_monotonic) / self.interval_seconds))
        return self._snapshot_at(sequence)

    def batch(self, after: int, limit: int, run_id: str | None = None) -> dict:
        if type(after) is not int or after < -1:
            raise ValueError("after must be an integer greater than or equal to -1.")
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("limit must be an integer from 1 to 256.")
        if run_id is not None and (not isinstance(run_id, str) or str(UUID(run_id)) != run_id):
            raise ValueError("runId must be a canonical UUID.")
        latest = max(0, int((time.monotonic() - self.started_monotonic) / self.interval_seconds))
        oldest = max(0, latest - 4095)
        if run_id is not None and run_id != self.run_id:
            start = oldest
        else:
            if after > latest:
                raise ValueError("after cannot exceed the latest sequence of the current run.")
            start = max(oldest, after + 1)
        page = {
            "streamVersion": "1.0", "deviceId": self.device_id, "runId": self.run_id,
            "oldestSequence": oldest, "latestSequence": latest,
            "snapshots": [],
        }
        encoded_size = len(json.dumps(page, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        for sequence in range(start, min(latest + 1, start + limit)):
            snapshot = self._snapshot_at(sequence, replay=True)
            additional_size = len(json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode("utf-8"))
            additional_size += bool(page["snapshots"])
            if encoded_size + additional_size > 900_000:
                break
            page["snapshots"].append(snapshot)
            encoded_size += additional_size
        return page

    def _snapshot_at(self, sequence: int, *, replay: bool = False) -> dict:
        elapsed = sequence * self.interval_seconds
        slow = math.sin(elapsed / 12 + self.phase)
        fast = math.sin(elapsed / 5 + self.phase)
        signals = (
            ("setpointFlowLpm", 10 + 0.1 * slow, "L/min"),
            ("actualFlowLpm", 9.8 + 0.15 * fast, "L/min"),
            ("supplyPressureKpa", 250 + 4 * slow, "kPa"),
            ("valveCommandPct", 50 + 2 * slow, "%"),
            ("valveFeedbackPct", 49.5 + 1.8 * fast, "%"),
            ("motorCurrentAmp", 2.1 + 0.08 * fast, "A"),
            ("temperatureC", 27 + 0.4 * slow, "degC"),
            ("cycleCount", 100 + math.floor(elapsed / 10), "count"),
        )
        rows = [
            {"signalKey": key, "value": round(value, 6), "unit": unit, "quality": "GOOD"}
            for key, value, unit in signals
        ]
        if self.scenario == "DEG-02":
            rows[0]["value"] = 10.0
            rows[1]["value"] = round(max(2.0, 9.8 - max(0, elapsed - 60) * 0.08), 6)
        elif self.scenario == "DATA-QUALITY":
            cycle_second = elapsed % 60
            if 15 <= cycle_second < 30:
                rows[1].update(value=None, quality="MISSING")
            elif 30 <= cycle_second < 45:
                rows[1].update(value=None, quality="BAD")
            elif 45 <= cycle_second < 60:
                rows[1]["value"] = -5.0
        elif self.scenario == "TRANSIENT" and 400 <= (sequence * self.interval_ms) % 1000 < 500:
            rows[1]["value"] = -5.0
        elif self.scenario == "NOISY-FLOW":
            # Indexed randomness makes replay independent of request order and page size.
            noise = random.Random(f"{self.seed}:{sequence}").gauss(0, .12)
            pulse = 1230 <= (sequence * self.interval_ms) % 4000 < 1330
            rows[0]["value"] = 10.0
            rows[1]["value"] = round(10 + noise - (3 if pulse else 0), 6)
            rows[2]["value"] = 220.0 if pulse else 250.0
        if replay:
            rows.extend({"signalKey": f"aux{index:04d}", "value": round(index + slow + fast / 10, 6),
                         "unit": "count", "quality": "GOOD"} for index in range(1, self.signal_count - 7))
        observed_at = self.started_utc + timedelta(seconds=elapsed)
        return {
            "protocolVersion": "1.1" if replay else "1.0",
            "source": "SIMULATOR",
            "deviceId": self.device_id,
            "runId": self.run_id,
            "sequence": sequence,
            "observedAtUtc": observed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "deviceStateVersion": 1,
            "modeId": "PRODUCTION",
            "signals": rows,
        }


def create_server(generator: SnapshotGenerator, token: str, *, port: int = 8771) -> ThreadingHTTPServer:
    """Create an authenticated server bound only to IPv4 loopback."""
    if not isinstance(token, str) or len(token.strip()) < 32 or "\r" in token or "\n" in token:
        raise ValueError("AEGIS_EMULATOR_TOKEN must contain at least 32 characters and no line breaks.")
    expected_token = token.encode("utf-8")

    class SnapshotHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AegisEmulator/1.0"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, _format, *_args):
            # Sensor samples and request headers are deliberately absent from logs.
            pass

        def send_json(self, status: int, body: dict):
            encoded = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def boundary_allowed(self) -> bool:
            hosts = self.headers.get_all("Host", [])
            host = LOCAL_HOST.fullmatch(hosts[0]) if len(hosts) == 1 else None
            if host is None or (host.group(1) and not 1 <= int(host.group(1)) <= 65535):
                self.send_json(400, {"code": "INVALID_HOST", "message": "A loopback Host is required."})
                return False
            if "Origin" in self.headers:
                self.send_json(403, {"code": "BROWSER_ORIGIN_FORBIDDEN", "message": "Browser origins are not supported."})
                return False
            tokens = self.headers.get_all("X-Aegis-Emulator-Token", [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode("utf-8"), expected_token):
                self.send_json(401, {"code": "UNAUTHORIZED", "message": "A valid emulator token is required."})
                return False
            return True

        def do_GET(self):
            if not self.boundary_allowed():
                return
            if self.path == "/v1/snapshot":
                self.send_json(200, generator.snapshot())
                return
            if self.path.partition("?")[0] == "/v1/batches":
                try:
                    pairs = parse_qsl(self.path.partition("?")[2], keep_blank_values=True,
                                      strict_parsing=True, encoding="ascii", errors="strict", max_num_fields=3)
                    params = dict(pairs)
                    if len(params) != len(pairs) or set(params) not in ({"after", "limit"}, {"after", "limit", "runId"}):
                        raise ValueError("Invalid query fields.")
                    if not re.fullmatch(r"-1|[0-9]+", params["after"]) or not re.fullmatch(r"[0-9]+", params["limit"]):
                        raise ValueError("Invalid query integers.")
                    page = generator.batch(int(params["after"]), int(params["limit"]), params.get("runId"))
                except (ValueError, UnicodeError):
                    self.send_json(400, {"code": "INVALID_QUERY", "message": "Valid after, limit and optional runId are required."})
                    return
                self.send_json(200, page)
                return
            self.send_json(404, {"code": "NOT_FOUND", "message": "The requested resource does not exist."})

        def deny_method(self):
            if self.boundary_allowed():
                self.send_json(405, {"code": "METHOD_NOT_ALLOWED", "message": "Only GET snapshot and batch reads are supported."})

        do_POST = deny_method
        do_PUT = deny_method
        do_PATCH = deny_method
        do_DELETE = deny_method
        do_OPTIONS = deny_method
        do_HEAD = deny_method
        do_TRACE = deny_method
        do_CONNECT = deny_method

    return ThreadingHTTPServer(("127.0.0.1", port), SnapshotHandler)
