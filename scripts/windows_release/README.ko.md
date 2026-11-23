# Aegis Windows 포터블

Windows x64에서 실행하는 Aegis 합성 계측 작업대와 오버레이입니다. 두 개의 로컬 에뮬레이터에서 수집한 값을 기존 Aegis 엔진, SQLite 보관 및 인증 Gateway로 전달합니다.

## 실행

1. 전체 폴더를 압축에서 꺼낸 뒤 `Aegis.cmd`를 더블클릭합니다. Aegis 아이콘이 Windows 알림 영역에 표시됩니다. 아이콘이 보이지 않으면 알림 영역의 숨겨진 아이콘을 펼칩니다.
2. 트레이 메뉴에서 작업대, 오버레이 표시, 고정 신호 설정을 엽니다.
3. 트레이 메뉴의 종료 명령으로 Aegis와 이 실행에 속한 합성 수집 프로세스를 종료합니다.

| 명령 | 동작 |
| --- | --- |
| `Aegis.cmd --tray --overlay` | 트레이와 읽기 전용 오버레이를 바로 표시 |
| `Aegis.cmd --workbench` | 작업대를 바로 열기 |
| `Aegis.cmd --check` | 실행 없이 파일 SHA-256, Python과 .NET 확인 |
| `Aegis.cmd --workbench --seconds 30` | 시작 후 합성 테스트를 30초로 제한 |

## 준비된 런타임

- Windows x64용 **Python 3.12**와 `pyproject.toml`의 Aegis 런타임 의존성이 있는 환경이 필요합니다. 미리 준비된 환경의 전체 `python.exe` 경로를 `AEGIS_PYTHON312` 환경 변수로 지정할 수 있습니다. 지정이 없으면 Windows Python Launcher의 `py -3.12`를 사용합니다. 런타임 직접 의존성 버전 5개를 확인하며, 저장소의 개발·전이 의존성 핀은 `requirements-dev.txt`에 남겨 두었습니다.
- **.NET 10 x64 Desktop Runtime**, **ASP.NET Core Runtime**과 **Microsoft Edge WebView2 Runtime**이 필요합니다. `--check`는 설치된 .NET 프레임워크를 확인합니다. WebView2는 작업대를 열 때 검사하며, 시작에 실패하면 화면에서 원인을 확인할 수 있습니다.
- 포터블 실행본에 Python·.NET·WebView2 설치 파일은 포함되지 않습니다. 실행기가 패키지를 설치하거나 시스템 설정을 바꾸지 않습니다.

PowerShell에서 기존 가상 환경을 선택하는 예입니다.

```powershell
$env:AEGIS_PYTHON312 = 'C:\aegis-runtime\Scripts\python.exe'
.\Aegis.cmd --check
.\Aegis.cmd --tray --overlay
```

## 저장 위치

창 위치와 고정 태그 설정은 `%LOCALAPPDATA%\Aegis\PortableSynthetic\desktop`에 남습니다. 새 실행의 합성 보관 DB와 로그는 `runs\run-…`에 따로 기록됩니다. 프로그램 폴더 안에 상태를 저장하지 않으므로 실행본을 교체해도 기존 실행 기록이 남습니다. 시험별로 나누려면 `--state-directory`로 프로그램 폴더 밖의 다른 위치를 지정합니다.

장시간 화면을 열어도 합성 보관 파일의 증가 속도를 줄이도록, 기본 시연은 각 장비의 표준 태그 8개를 250ms 간격으로 내보냅니다. 저장 크기와 지속 시간은 실행 환경에 따라 달라집니다.

이 실행기는 로컬 루프백에서 합성 장비로 동작합니다. 기존 실장비 설정과 토큰은 상속하지 않습니다. 실제 장비 연결 및 Windows 원시 아카이브 백업·복원 기능은 이 패키지의 지원 범위에 포함되지 않습니다.
