"""Install, update, or remove Aegis application files in a per-user directory."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile


SCHEMA = {"schemaVersion": 1, "product": "Aegis.Windows.x64"}
MARKER = ".aegis-install.json"
LAUNCHER = "Aegis.cmd"
MANIFEST = "manifest.json"
RELEASE_PATTERN = re.compile(r"[0-9a-f]{64}")


class InstallError(Exception):
    pass


def exists(path):
    return os.path.lexists(path)


def plain_path(path, *, directory):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise InstallError(f"링크, 접합점 또는 예상과 다른 파일 형식입니다. {path}")
    return info


def check_ancestors(path):
    for parent in reversed(path.parents):
        if exists(parent):
            plain_path(parent, directory=True)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(source):
    plain_path(source, directory=True)
    manifest = source / MANIFEST
    plain_path(manifest, directory=False)
    content = manifest.read_bytes()
    document = json.loads(content)
    if (document.get("schemaVersion"), document.get("platform")) != (1, "win-x64"):
        raise InstallError("지원하는 Aegis Windows x64 패키지가 아닙니다.")
    files = {}
    occupied = set()
    for entry in document["files"]:
        raw = entry["path"]
        name = PurePosixPath(raw)
        if (name.is_absolute() or name.as_posix() != raw or not name.parts
                or any(part in {".", ".."} or part[-1:] in {".", " "}
                       or any(character in part for character in '\\:<>|?*"')
                       or Path(part).is_reserved() for part in name.parts)
                or raw.casefold() in occupied or raw.casefold() == MANIFEST):
            raise InstallError(f"패키지 파일 경로가 올바르지 않습니다. {raw}")
        occupied.add(raw.casefold())
        size = entry["bytes"]
        digest = entry["sha256"]
        if type(size) is not int or size < 0 or not RELEASE_PATTERN.fullmatch(digest):
            raise InstallError(f"패키지 파일의 무결성 항목이 올바르지 않습니다. {raw}")
        files[Path(*name.parts)] = (size, digest)
    if Path(LAUNCHER) not in files:
        raise InstallError("실행 파일이 패키지 목록에 없습니다.")
    return hashlib.sha256(content).hexdigest(), files


def check_payload(root, files):
    for relative, (size, digest) in files.items():
        path = root / relative
        check_ancestors(path)
        if plain_path(path, directory=False).st_size != size or sha(path) != digest:
            raise InstallError(f"패키지 파일 검증에 실패했습니다. {path}")


def inventory(root):
    entries = set()
    for child in root.iterdir():
        relative = child.relative_to(root)
        if child.is_dir():
            plain_path(child, directory=True)
            entries.add(relative)
            entries.update(relative / item for item in inventory(child))
        else:
            plain_path(child, directory=False)
            entries.add(relative)
    return entries


def owned_release(path):
    version, files = package(path)
    if path.name != version:
        raise InstallError(f"설치 버전 폴더 이름과 패키지가 일치하지 않습니다. {path}")
    check_payload(path, files)
    owned = {Path(MANIFEST), *files.keys()}
    for item in files:
        owned.update(parent for parent in item.parents if parent != Path("."))
    if inventory(path) != owned:
        raise InstallError(f"추가되거나 누락된 항목이 있어 기존 파일을 보존합니다. {path}")
    return files


def remove_owned_release(path, files):
    # The caller has verified the path, manifest, contents, and absence of extra files.
    directories = {parent for item in files for parent in item.parents if parent != Path(".")}
    for item in files:
        (path / item).unlink(missing_ok=True)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        folder = path / directory
        if folder.is_dir():
            folder.rmdir()
    (path / MANIFEST).unlink(missing_ok=True)
    path.rmdir()


def release_command(version):
    return f'@echo off\r\ncall "%~dp0releases\\{version}\\Aegis.cmd" %*\r\nexit /b %ERRORLEVEL%\r\n'.encode("ascii")


def active_release(root):
    launcher = root / LAUNCHER
    if not exists(launcher):
        return None
    plain_path(launcher, directory=False)
    content = launcher.read_bytes()
    match = re.search(rb"releases\\([0-9a-f]{64})\\Aegis\.cmd", content)
    if not match or content != release_command(match[1].decode("ascii")):
        raise InstallError(f"기존 실행 파일은 이 설치가 만든 형식이 아닙니다. {launcher}")
    return match[1].decode("ascii")


def publish_launcher(root, version):
    handle, name = tempfile.mkstemp(prefix=".aegis-launch-", dir=root)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(release_command(version))
        Path(name).replace(root / LAUNCHER)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def lock_install(root):
    import msvcrt

    path = root / ".aegis-install.lock"
    if exists(path):
        plain_path(path, directory=False)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise InstallError("같은 폴더의 설치 또는 업데이트가 진행 중입니다.") from error
        yield


def stage_version(source, releases, version, files):
    destination = releases / version
    if exists(destination):
        owned_release(destination)
        return
    temp = Path(tempfile.mkdtemp(prefix=".aegis-stage-", dir=releases.parent))
    written = []
    try:
        for relative in [Path(MANIFEST), *files]:
            origin = source / relative
            check_ancestors(origin)
            plain_path(origin, directory=False)
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            written.append(relative)
            shutil.copyfile(origin, target)
        if sha(temp / MANIFEST) != version:
            raise InstallError("설치 중 원본 패키지 정보가 변경되었습니다.")
        check_payload(temp, files)
        temp.rename(destination)
    finally:
        if temp.is_dir():
            # Only discard files created in this operation's exclusive temporary directory.
            for relative in reversed(written):
                (temp / relative).unlink(missing_ok=True)
            for directory in sorted({parent for item in files for parent in item.parents if parent != Path(".")},
                                    key=lambda item: len(item.parts), reverse=True):
                if (temp / directory).is_dir():
                    (temp / directory).rmdir()
            temp.rmdir()


def manage(source, root, command):
    source = source.resolve(strict=True)
    root = root.absolute()
    check_ancestors(root)
    if exists(root):
        plain_path(root, directory=True)
    root = root.resolve()
    if source.is_relative_to(root) or root.is_relative_to(source):
        raise InstallError("설치 위치와 압축을 푼 폴더를 분리하세요.")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        data = (Path(local) / "Aegis").resolve()
        if root.is_relative_to(data) or data.is_relative_to(root):
            raise InstallError("진행 중인 수집과 사용자 데이터는 설치 위치에서 분리합니다.")

    if command != "uninstall":
        version, files = package(source)
        longest_root = root / ".aegis-retired" / version
        if any(len(str(longest_root / item)) >= 260 or len(str((longest_root / item).parent)) >= 248
               for item in [Path(MANIFEST), *files]):
            raise InstallError("선택한 설치 경로가 너무 깁니다. 사용자 프로그램 기본 경로나 더 짧은 로컬 경로를 사용하세요.")

    marker = root / MARKER
    if not exists(marker):
        if command != "install" or (root.is_dir() and any(root.iterdir())):
            raise InstallError(f"Aegis 설치 소유권을 확인할 수 없습니다. {root}")
        root.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(SCHEMA, stream)
    plain_path(marker, directory=False)
    if json.loads(marker.read_text(encoding="utf-8")) != SCHEMA:
        raise InstallError(f"Aegis 설치 소유권을 확인할 수 없습니다. {root}")

    with lock_install(root):
        releases = root / "releases"
        active = active_release(root)
        generations = [] if not exists(releases) else list(checked_releases(releases))
        retired = root / ".aegis-retired"
        if exists(retired):
            if command != "uninstall" or exists(releases):
                raise InstallError("완료되지 않은 제거 작업이 있습니다. 기존 배포 파일에서 uninstall을 다시 실행하세요.")
            finish_uninstall(root, retired)
            return None
        if active and active not in {path.name for path in generations}:
            raise InstallError("현재 실행 파일의 설치 버전을 찾을 수 없습니다.")

        if command == "uninstall":
            for path in generations:
                owned_release(path)
            if exists(releases):
                # Renaming prevents new execution of the installed release while removal runs.
                releases.rename(retired)
                finish_uninstall(root, retired)
            else:
                (root / LAUNCHER).unlink(missing_ok=True)
            return None

        if command == "update" and not active:
            raise InstallError("활성화된 Aegis 설치가 없습니다. 먼저 install을 실행하세요.")
        if command == "install" and active and active != version:
            raise InstallError("기존 설치가 있습니다. 새 버전은 update로 적용하세요.")
        releases.mkdir(exist_ok=True)
        stage_version(source, releases, version, files)
        publish_launcher(root, version)
        return root / LAUNCHER


def finish_uninstall(root, retired):
    claims = []
    for path in checked_releases(retired):
        if not any(path.iterdir()):
            claims.append((path, {}))
            continue
        # Staged removal is resumable and continues only for files it still owns.
        version, files = package(path)
        if version != path.name:
            raise InstallError(f"제거 대기 폴더의 소유권을 확인할 수 없습니다. {path}")
        present = {relative: entry for relative, entry in files.items() if exists(path / relative)}
        check_payload(path, present)
        allowed = {Path(MANIFEST), *files.keys()}
        allowed.update(parent for item in files for parent in item.parents if parent != Path("."))
        if not inventory(path) <= allowed:
            raise InstallError(f"추가된 항목이 있어 기존 파일을 보존합니다. {path}")
        claims.append((path, files))
    (root / LAUNCHER).unlink(missing_ok=True)
    for path, files in claims:
        remove_owned_release(path, files)
    retired.rmdir()


def checked_releases(root):
    plain_path(root, directory=True)
    for path in root.iterdir():
        if not RELEASE_PATTERN.fullmatch(path.name):
            raise InstallError(f"관리 대상이 아닌 버전 폴더가 있어 파일을 보존합니다. {path}")
        plain_path(path, directory=True)
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "update", "uninstall"))
    parser.add_argument("--install-directory", type=Path)
    args = parser.parse_args()
    try:
        local = os.environ.get("LOCALAPPDATA")
        if args.install_directory is None and not local:
            raise InstallError("LOCALAPPDATA 또는 --install-directory가 필요합니다.")
        root = args.install_directory or Path(local) / "Programs" / "Aegis"
        launcher = manage(Path(__file__).resolve().parents[1], root, args.operation)
        print(f"실행 파일: {launcher}" if launcher else "프로그램 파일을 제거했습니다. 사용자 데이터는 보존했습니다.")
    except (InstallError, OSError, ValueError, TypeError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
