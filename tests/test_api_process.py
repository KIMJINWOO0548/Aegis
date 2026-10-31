"""Exercise the real Windows socket/event loop that TestClient does not use."""

import os
import secrets
import socket
import subprocess
import sys
import time

import httpx
import pytest


def test_cli_serves_authenticated_mock_routes_on_loopback(tmp_path):
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex(("127.0.0.1", 8765)) == 0:
            pytest.skip("Local API port 8765 is already in use.")
    token = secrets.token_hex(32)
    trace = "ac901964-a7a3-453c-9941-4641655b6d30"
    env = dict(os.environ, AEGIS_API_TOKEN=token, PYTHONIOENCODING="utf-8")
    with (tmp_path / "server.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "-m", "aegis.api"],
                                   env=env, stdout=output, stderr=output)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False,
                              headers={"X-Aegis-Token": token, "X-Trace-Id": trace},
                              timeout=httpx.Timeout(5, connect=.5)) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        response = client.get("/v1/health")
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        assert process.poll() is None, "API exited before accepting requests."
                        assert time.monotonic() < deadline, "API did not start within 20 seconds."
                        time.sleep(.1)
                assert response.status_code == 200
                assert response.json()["mock"] is True
                assert response.headers["X-Trace-Id"] == trace
                trend = client.get("/v1/devices/EQ-SUPPLY-01/trends", params={
                    "signals": "actualFlowLpm,setpointFlowLpm",
                    "fromUtc": "2026-10-16T00:00:00Z", "toUtc": "2026-10-16T00:05:00Z",
                })
                assert trend.status_code == 200
                assert len(trend.json()["series"]) == 2
                timeline = client.get("/v1/warnings/warn-demo-02/timeline")
                assert timeline.status_code == 200
                assert timeline.json()["warningId"] == "warn-demo-02"
                denied = client.get("/v1/health", headers={"X-Aegis-Token": "wrong"})
                assert denied.status_code == 401
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
