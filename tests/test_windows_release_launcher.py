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
    arguments = SimpleNamespace(state_directory=tmp_path, workbench=False, overlay=True, seconds=4, background=False)
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
