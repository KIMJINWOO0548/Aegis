"""Start the Windows portable synthetic workspace without changing the host."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FRAMEWORKS = {"Microsoft.NETCore.App", "Microsoft.AspNetCore.App", "Microsoft.WindowsDesktop.App"}
REQUIRED_FILES = (
    "src/Aegis.Gateway/bin/Release/net10.0/Aegis.Gateway.dll",
    "src/Aegis.Desktop/bin/Release/net10.0-windows/Aegis.Desktop.exe",
    "ui/dist/index.html",
    "scripts/run_hybrid_demo.py",
    "scripts/benchmark_emulators.py",
    "pyproject.toml",
    "manifest.json",
)


class LaunchError(Exception):
    pass


def arguments():
    parser = argparse.ArgumentParser(description="Aegis Windows portable synthetic workspace")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tray", action="store_true", help="Start in the tray (default).")
    mode.add_argument("--workbench", action="store_true", help="Open the workbench at startup.")
    parser.add_argument("--overlay", action="store_true", help="Show the passive overlay at startup.")
    parser.add_argument("--check", action="store_true", help="Verify the package and runtimes without starting.")
    parser.add_argument("--state-directory", type=Path, help="External directory for isolated synthetic test data.")
    parser.add_argument("--seconds", type=int, help="Optional bounded smoke run of 1 to 3600 seconds.")
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.seconds is not None and not 1 <= args.seconds <= 3600:
        parser.error("--seconds must be from 1 to 3600.")
    return args


def verify_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1 or manifest.get("platform") != "win-x64":
        raise LaunchError("지원하는 Windows x64 패키지 형식이 아닙니다.")
    paths = set()
    for entry in manifest["files"]:
        name = entry["path"]
        candidate = (ROOT / name).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.is_file():
            raise LaunchError(f"패키지 파일이 없거나 경로가 올바르지 않습니다. {name}")
        with candidate.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if candidate.stat().st_size != entry["bytes"] or digest != entry["sha256"]:
            raise LaunchError(f"패키지 파일 검증에 실패했습니다. {name}")
        paths.add(name)
    if not set(REQUIRED_FILES).difference({"manifest.json"}).issubset(paths):
        raise LaunchError("패키지 목록에 필수 파일이 빠져 있습니다.")
    print(f"패키지 SHA-256 확인 완료 · {len(paths)}개 파일", flush=True)


def prerequisites():
    if os.name != "nt" or sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 12):
        raise LaunchError("Windows x64용 Python 3.12가 필요합니다. AEGIS_PYTHON312에 준비된 인터프리터를 지정하세요.")
    missing_files = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    if missing_files:
        raise LaunchError("압축을 모두 풀고 실행하세요. 빠진 파일 " + ", ".join(missing_files))
    proposal = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    missing_packages = []
    for requirement in proposal["project"]["dependencies"]:
        name, pinned = requirement.split("==", 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != pinned:
            missing_packages.append(requirement)
    if missing_packages:
        raise LaunchError("준비된 Python 환경에서 다음 제품 의존성을 확인하세요. " + ", ".join(missing_packages))

    dotnet = shutil.which("dotnet.exe")
    if not dotnet:
        raise LaunchError(".NET 10 x64 Desktop 및 ASP.NET Core 런타임을 준비하세요.")
    runtimes = subprocess.run([dotnet, "--list-runtimes"], check=True, capture_output=True, text=True, timeout=10)
    available = {fields[0] for line in runtimes.stdout.splitlines() if len(fields := line.split()) >= 2 and fields[1].startswith("10.0.")}
    missing_frameworks = REQUIRED_FRAMEWORKS - available
    if missing_frameworks:
        raise LaunchError(".NET 10 x64에서 다음 런타임이 필요합니다. " + ", ".join(sorted(missing_frameworks)))


def get_state_root(directory: Path | None) -> Path:
    if directory is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local or not Path(local).is_absolute():
            raise LaunchError("LOCALAPPDATA가 비어 있습니다. --state-directory로 별도 작업 경로를 지정하세요.")
        directory = Path(local) / "Aegis" / "PortableSynthetic"
    state_root = directory.resolve()
    if state_root.is_relative_to(ROOT):
        raise LaunchError("합성 저장소와 작업 설정은 프로그램이 들어 있는 폴더 밖에 두세요.")
    return state_root


def start(args):
    import msvcrt

    state_root = get_state_root(args.state_directory)
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / "launch.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise LaunchError("이 작업 폴더의 Aegis가 실행 중입니다. Windows 알림 영역에서 Aegis를 확인하세요.") from error

        profile = (state_root / "desktop").resolve()
        archives = (state_root / "runs").resolve()
        if any(not directory.is_relative_to(state_root) or directory.is_relative_to(ROOT) for directory in (profile, archives)):
            raise LaunchError("합성 저장소와 작업 설정이 작업 폴더 밖을 가리킵니다.")
        profile.mkdir(exist_ok=True)
        archives.mkdir(exist_ok=True)
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        command = [
            str(interpreter), "-E", "-s", "-B", str(ROOT / "scripts" / "run_hybrid_demo.py"),
            "--auto-port", "--signal-count", "8", "--interval-ms", "250",
            "--desktop-state-directory", str(profile),
        ]
        if not args.workbench:
            command.append("--tray")
        if args.overlay:
            command.append("--overlay")
        if args.seconds is not None:
            command.extend(["--seconds", str(args.seconds)])
        environment = {name: value for name, value in os.environ.items() if not name.upper().startswith(("AEGIS_", "WEBVIEW2_"))}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", DOTNET_ENVIRONMENT="Production", ASPNETCORE_ENVIRONMENT="Production")
        print("Aegis 합성 수집을 시작합니다. 트레이에서 작업대 또는 오버레이를 열 수 있습니다.", flush=True)
        for attempt in range(3):
            archive = Path(tempfile.mkdtemp(prefix="run-", dir=archives))
            logfile = archive / "launch.log"
            print(f"이 실행의 기록 · {archive}", flush=True)
            with logfile.open("x", encoding="utf-8") as stream:
                result = subprocess.run([*command, "--archive-directory", str(archive)], cwd=ROOT, env=environment,
                                        stdout=stream, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if args.background else 0)
            if result.returncode == 0:
                return
            with logfile.open(encoding="utf-8", errors="replace") as stream:
                startup = stream.read(32768)
            if attempt == 2 or "[WinError 10014]" not in startup or "Shared dashboard:" in startup:
                raise LaunchError(f"Aegis 시작 또는 실행을 완료하지 못했습니다. 종료 코드 {result.returncode}. 실행 기록 {logfile}")
            # The prior synthetic runner is down. Only retry the identified startup fault, with a fresh archive.
            print("Windows 루프백 시작 오류를 감지했습니다. 새 합성 세션으로 다시 시도합니다.", flush=True)


def main() -> int:
    args = arguments()
    try:
        prerequisites()
        if args.check:
            verify_manifest()
            print("Python 3.12 x64와 .NET 10 런타임 확인 완료. WebView2는 작업대 실행 시 확인합니다.")
        else:
            start(args)
    except (LaunchError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        message = str(error)
        if args.background and os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message + "\n\n실행 조건은 동봉된 README.ko.md에서 확인할 수 있습니다.", "Aegis 시작 확인", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
