# Aegis Windows 실행 안내

Aegis는 Windows x64용 설비 신호 작업대입니다. 이 패키지는 두 개의 로컬 합성 에뮬레이터에서 값을 수집해 SQLite 보관과 작업대를 연결합니다.

## 시작 및 종료

1. ZIP의 전체 파일을 Windows 로컬 폴더로 풀고 `Aegis.cmd`를 더블클릭합니다. 작업대와 Windows 알림 영역의 Aegis 아이콘이 열립니다.
2. 알림 영역 메뉴에서 작업대를 다시 열거나 오버레이와 고정 신호를 설정합니다. 창을 닫아도 트레이에 남습니다.
3. 알림 영역 메뉴에서 **Aegis 종료**를 누르면 이 실행이 시작한 합성 수집도 멈춥니다.

| 명령 | 동작 |
| --- | --- |
| `Aegis.cmd --check` | 실행 없이 패키지 SHA-256, Python, .NET 확인 |
| `Aegis.cmd --tray --overlay` | 트레이에서 수집을 시작하고 읽기 전용 오버레이 열기 |
| `Aegis.cmd --workbench --scenario DEG-02` | 합성 유량 편차 사례와 작업대 열기 |
| `Aegis.cmd --workbench --seconds 30` | 실행을 30초로 제한하는 합성 시험 |

## 사용자별 설치 · 업데이트 · 되돌리기 · 제거

압축을 푼 폴더에서 `Install-Aegis.cmd`를 더블클릭하면 현재 사용자 프로그램 폴더에 설치합니다. 완료 메시지의 `%LOCALAPPDATA%\Programs\Aegis\Aegis.cmd`에서 실행합니다. 별도 경로는 명령 뒤에 `--install-directory "C:\Aegis-Test\Program"`을 붙여 지정합니다.

- 최초 설치나 같은 버전의 다시 확인에는 `Install-Aegis.cmd install`을 실행합니다.
- 새로운 패키지를 별도 폴더에 푼 뒤 그 폴더에서 `Install-Aegis.cmd update`를 실행합니다. 파일 검증 뒤 새 버전이 다음 실행부터 적용됩니다. 기존 버전의 프로그램 폴더와 사용자 데이터는 보존됩니다.
- 이전 프로그램으로 되돌리려면 Aegis를 종료하고 최신 배포 폴더에서 `Install-Aegis.cmd rollback`을 실행합니다. 설치 또는 업데이트 때 표시한 전체 매니페스트 SHA-256이 이전 저장본의 폴더 이름과 일치하고, 목록·파일 크기·해시가 모두 맞을 때 다음 실행 버전만 바뀝니다. 이전 버전 기록이 없는 설치에서는 `rollback --to-release <전체 SHA-256>`으로 보관된 버전을 명시합니다. 보관 중인 설정과 원시 기록은 유지됩니다.
- Aegis를 종료하고 압축을 푼 배포 폴더에서 `Install-Aegis.cmd uninstall`을 실행합니다. 설치한 프로그램 파일을 검증한 뒤 제거하며 설정과 보관 기록은 유지합니다. 추가하거나 수정한 프로그램 파일이 있으면 해당 경로를 알려주고 보호합니다.

이 설치 도구는 사용자 폴더에서 동작합니다. 관리자 권한, 시스템 변경, 시작 메뉴·바탕화면 바로가기 또는 Windows 설정의 앱 등록을 사용하지 않습니다. 프로그램 서명 상태와 WebView2 준비 여부는 배포 환경에서 확인하세요.

## 실행 환경

- `runtime/runtime.json`이 있는 ZIP은 패키지 안의 **Python 3.12 x64 및 Aegis Python 의존성**과 **.NET 10 x64 Desktop·ASP.NET Core·Core 런타임**을 우선 사용합니다. 포함된 버전은 해당 파일에서 확인할 수 있습니다.
- **Microsoft Edge WebView2 Runtime**은 Windows에 준비되어 있어야 합니다. 작업대를 열 때 확인하며, 빠져 있으면 앱에서 원인을 표시합니다.
- `runtime`이 없는 개발 검증 패키지는 준비된 Windows x64용 Python 3.12 환경과 .NET 10 x64 Desktop·ASP.NET Core 런타임이 필요합니다. `AEGIS_PYTHON312`에 준비된 `python.exe`의 전체 경로를 지정하거나, 해당 Python을 사용하는 Windows Python Launcher의 `py -3.12`로 실행하세요.
- 패키지 검사에서 불일치가 나오면 새 폴더에 ZIP 전체를 다시 풉니다. 실행기는 설치된 시스템 런타임이나 보안 설정을 변경하지 않습니다.

## 설정과 보관 기록

창 위치, 테마, 고정 신호 설정은 `%LOCALAPPDATA%\Aegis\PortableSynthetic\desktop`에 저장합니다. 합성 원시 기록과 로그는 같은 작업 폴더의 `runs\run-…`에 실행별로 남습니다. 작업대 **보관 이력**에서 이전 실행을 읽기 전용으로 조회할 수 있습니다. 목록에서 과거 기록을 열어도 화면의 현재값과 수집은 계속 분리됩니다.

설정과 기록은 프로그램 폴더 밖에 유지되므로 새 ZIP을 다른 폴더에 풀어도 기존 설정·기록을 조회할 수 있습니다. 별도 합성 시험 폴더를 쓰려면 `--state-directory "C:\Aegis-Test\State"`로 파일이 저장될 위치를 지정합니다.

이 패키지는 로컬 루프백에서 합성 장비를 실행합니다. 실제 설비 연결과 Windows 원시 아카이브 전체 백업·복원은 지원 범위 밖이며, 실행 시 기존 실장비 설정과 토큰을 상속하지 않습니다.
