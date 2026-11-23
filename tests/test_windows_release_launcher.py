import os
from types import SimpleNamespace

import pytest

from scripts.windows_release import launch


@pytest.mark.skipif(os.name != "nt", reason="The portable launcher uses a Windows state lock.")
@pytest.mark.parametrize("ready", [False, True])
def test_portable_retries_transient_startup_only(tmp_path, monkeypatch, ready):
    attempts = []

    def run(command, **kwargs):
        attempts.append(command)
        if len(attempts) == 1:
            if ready:
                kwargs["stdout"].write("Shared dashboard: local test\n")
            kwargs["stdout"].write("ERROR: [WinError 10014] test loopback startup fault\n")
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch.subprocess, "run", run)
    arguments = SimpleNamespace(state_directory=tmp_path, workbench=False, tray=True, scenario="DEG-02", overlay=True, seconds=4, background=False)
    if ready:
        with pytest.raises(launch.LaunchError):
            launch.start(arguments)
        assert len(attempts) == 1
    else:
        launch.start(arguments)
        assert len(attempts) == 2
        assert attempts[0][-1] != attempts[1][-1]
        assert all("--auto-port" in call and "--tray" in call and "--overlay" in call for call in attempts)

    assert len(list((tmp_path / "runs").glob("*/launch.log"))) == len(attempts)


@pytest.mark.skipif(os.name != "nt", reason="The portable launcher uses a Windows state lock.")
def test_default_launch_opens_workbench_with_portable_history(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch.subprocess, "run", run)
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "outside-portable-session")
    monkeypatch.setenv("PYTHONPATH", "outside-portable-sources")
    launch.start(SimpleNamespace(state_directory=tmp_path, tray=False, scenario="NOISY-FLOW",
                                 overlay=False, seconds=None, background=False))
    command, environment = calls[0]
    assert "--tray" not in command
    assert command[command.index("--archive-sessions-root") + 1] == str(tmp_path / "runs")
    assert command[command.index("--scenario") + 1] == "NOISY-FLOW"
    assert "AEGIS_DASHBOARD_TOKEN" not in environment
    assert "PYTHONPATH" not in environment


def test_launch_verifies_integrity_before_loading_runtime(monkeypatch):
    operations = []
    args = SimpleNamespace(check=False, background=False)
    monkeypatch.setattr(launch, "arguments", lambda: args)
    monkeypatch.setattr(launch, "verify_manifest", lambda: operations.append("manifest"))
    monkeypatch.setattr(launch, "prerequisites", lambda: operations.append("runtime"))
    monkeypatch.setattr(launch, "start", lambda value: operations.append("start"))
    assert launch.main() == 0
    assert operations == ["manifest", "runtime", "start"]
