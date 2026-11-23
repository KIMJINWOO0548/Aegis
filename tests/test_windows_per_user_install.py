import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.windows_release import manage_install


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows per-user installation")


def make_package(directory, version):
    directory.mkdir()
    payload = {"Aegis.cmd": b"@echo off", "ui/dist/index.html": version.encode("ascii")}
    for name, content in payload.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    document = {
        "schemaVersion": 1,
        "platform": "win-x64",
        "files": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                  for name, content in payload.items()],
    }
    (directory / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    return directory


def test_install_update_and_remove_preserve_data(tmp_path, monkeypatch):
    first = make_package(tmp_path / "a", "first")
    second = make_package(tmp_path / "b", "second")
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    data = local / "Aegis" / "PortableSynthetic"
    data.mkdir(parents=True)
    wanted = data / "history.sqlite3"
    wanted.write_bytes(b"history")
    install = local / "Programs" / "Aegis"
    launcher = manage_install.manage(first, install, "install")
    initial = manage_install.active_release(install)
    assert launcher == install / "Aegis.cmd"
    assert launcher.read_bytes() == manage_install.release_command(initial)
    assert manage_install.manage(first, install, "install") == launcher
    with pytest.raises(manage_install.InstallError, match="update"):
        manage_install.manage(second, install, "install")
    assert manage_install.manage(second, install, "update") == launcher
    updated = manage_install.active_release(install)
    assert initial != updated
    assert set(p.name for p in (install / "releases").iterdir()) == {initial, updated}
    assert manage_install.manage(second, install, "uninstall") is None
    assert wanted.read_bytes() == b"history"
    assert not launcher.exists()
    assert not (install / "releases").exists()


def test_protects_changed_or_foreign_program_directory(tmp_path):
    first = make_package(tmp_path / "a", "first")
    unfamiliar = tmp_path / "other"
    unfamiliar.mkdir()
    important = unfamiliar / "existing.txt"
    important.write_bytes(b"unmanaged")
    with pytest.raises(manage_install.InstallError, match="소유권"):
        manage_install.manage(first, unfamiliar, "install")
    assert important.read_bytes() == b"unmanaged"

    install = tmp_path / "application"
    launcher = manage_install.manage(first, install, "install")
    version = manage_install.active_release(install)
    unexpected = install / "releases" / version / "existing.txt"
    unexpected.write_bytes(b"keep")
    with pytest.raises(manage_install.InstallError, match="보존"):
        manage_install.manage(first, install, "uninstall")
    assert launcher.is_file()
    assert unexpected.read_bytes() == b"keep"


def test_rejects_corrupt_candidate_preserving_active_version(tmp_path):
    first = make_package(tmp_path / "a", "first")
    second = make_package(tmp_path / "b", "second")
    (second / "ui/dist/index.html").write_text("changed", encoding="ascii")
    install = tmp_path / "application"
    launcher = manage_install.manage(first, install, "install")
    before = launcher.read_bytes()
    with pytest.raises(manage_install.InstallError, match="검증"):
        manage_install.manage(second, install, "update")
    assert launcher.read_bytes() == before
    assert list(install.glob(".aegis-stage-*")) == []
    assert len(list((install / "releases").iterdir())) == 1


def test_failed_cleanup_can_be_resumed(tmp_path, monkeypatch):
    first = make_package(tmp_path / "a", "first")
    install = tmp_path / "application"
    launcher = manage_install.manage(first, install, "install")
    remove = manage_install.remove_owned_release

    def interrupted(path, files):
        item = Path("ui/dist/index.html")
        (path / item).unlink()
        raise OSError("simulated interrupted removal")

    monkeypatch.setattr(manage_install, "remove_owned_release", interrupted)
    with pytest.raises(OSError, match="interrupted removal"):
        manage_install.manage(first, install, "uninstall")
    assert not launcher.exists()
    assert (install / ".aegis-retired").is_dir()
    monkeypatch.setattr(manage_install, "remove_owned_release", remove)
    assert manage_install.manage(first, install, "uninstall") is None
    assert not (install / ".aegis-retired").exists()


def test_reports_unsupported_deep_install_path_before_creating_target(tmp_path):
    first = make_package(tmp_path / "a", "first")
    target = tmp_path / ("deep-" + "x" * 130)
    with pytest.raises(manage_install.InstallError, match="너무 깁니다"):
        manage_install.manage(first, target, "install")
    assert not target.exists()
