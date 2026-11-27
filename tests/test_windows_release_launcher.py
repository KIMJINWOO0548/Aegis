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


def _native_package(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    header = b"MZ" + bytes(58) + (64).to_bytes(4, "little") + b"PE\0\0\x64\x86"
    for name in launch.NATIVE_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header)
    (tmp_path / "manifest.json").write_text(json.dumps({"files": [{"path": name} for name in launch.NATIVE_FILES]}))
    return tmp_path / launch.NATIVE_DIRECTORY / "Aegis.NativeHost.exe"


def test_native_host_requires_packaged_pair_and_x64(tmp_path, monkeypatch):
    host = _native_package(tmp_path, monkeypatch)
    assert launch.native_host() == host.resolve()
    dll = host.with_name("aegis_native.dll")
    dll.write_bytes(b"MZ" + bytes(58) + (64).to_bytes(4, "little") + b"PE\0\0\x4c\x01")
    with pytest.raises(launch.LaunchError, match="x64"):
        launch.native_host()
    dll.unlink()
    with pytest.raises(launch.LaunchError):
        launch.native_host()


def test_native_host_rejects_unrecorded_files(tmp_path, monkeypatch):
    _native_package(tmp_path, monkeypatch)
    (tmp_path / "manifest.json").write_text('{"files": []}')
    with pytest.raises(launch.LaunchError):
        launch.native_host()


@pytest.mark.skipif(os.name != "nt", reason="The portable launcher uses a Windows state lock.")
@pytest.mark.parametrize("native_review", [False, True])
def test_native_launch_opt_in_and_environment_isolation(tmp_path, monkeypatch, native_review):
    package = tmp_path / "package"
    package.mkdir()
    host = _native_package(package, monkeypatch)
    calls = []
    monkeypatch.setenv("AEGIS_NATIVE_HOST_EXE", "outside-host.exe")
    monkeypatch.setenv("AEGIS_NATIVE_REVIEW", "true")
    monkeypatch.setenv("DOTNET_STARTUP_HOOKS", "outside-hook.dll")
    monkeypatch.setenv("DOTNET_ROOT", "outside-dotnet")
    monkeypatch.setattr(launch.subprocess, "run", lambda command, **kwargs: (calls.append((command, kwargs["env"])) or SimpleNamespace(returncode=0)))
    launch.start(SimpleNamespace(state_directory=tmp_path / "state", tray=False, scenario="NORMAL",
                                 overlay=False, seconds=1, background=False, native_review=native_review))
    command, environment = calls[0]
    assert ("--native-host" in command) == native_review
    if native_review:
        assert command[command.index("--native-host") + 1] == str(host.resolve())
    assert not any(key in environment for key in ("AEGIS_NATIVE_HOST_EXE", "AEGIS_NATIVE_REVIEW", "DOTNET_STARTUP_HOOKS", "DOTNET_ROOT"))


def test_private_dotnet_replaces_inherited_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    inventory = tmp_path / launch.PRIVATE_INVENTORY
    inventory.parent.mkdir()
    inventory.write_text("{}")
    monkeypatch.setenv("DOTNET_ROOT", "outside-dotnet")
    monkeypatch.setenv("DOTNET_STARTUP_HOOKS", "outside-hook.dll")
    environment = launch.isolated_environment()
    assert environment["DOTNET_ROOT"] == str(tmp_path / launch.PRIVATE_DOTNET)
    assert environment["DOTNET_ROOT_X64"] == environment["DOTNET_ROOT"]
    assert "DOTNET_STARTUP_HOOKS" not in environment
