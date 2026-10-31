# Aegis Insight 시작 안내

이 저장소는 CMP 계열 장비 데이터를 다루는 로컬 합성 시뮬레이터 프로토타입입니다. 장비나 고객 자료로 검증된 제품이라고 주장하지 않습니다.

최신 화면은 React/TypeScript + ASP.NET Core/SignalR + Windows WebView2입니다. [현재 실행·검증·실패 상태](docs/HYBRID_DASHBOARD.md)를 먼저 확인하세요. 아래 WPF 안내와 과거 증거는 별도로 보존합니다.

## 검토 순서

노이즈 처리와 실제 짧은 변화의 보존에 관한 최신 개발 방향은 [신호 검토 보고서](docs/SIGNAL_REVIEW.md)에 있습니다. 기존248개 설치 검증은 이전 버전의 재현 증거이며 새 신호 검토의 현장 성능을 입증하지 않습니다.

1. [README](README.md)에서 범위와 실행 경로를 확인합니다.
2. [포트폴리오 사례](docs/PORTFOLIO.md)에서 아키텍처, 데이터 보장, 실패 증거와 미완료 범위를 봅니다.
3. [데모 가이드](docs/DEMO_GUIDE.md)를 따라 정상·결측·BAD·범위 오류를 확인합니다.
4. [모듈 수집 보고서](docs/DURABLE_INGESTION_REPORT.md)에서 과거 시험 조건을 확인하고, [성능 보고서](docs/PERFORMANCE.md)에서 병목·변경·반복 측정의 결과와 한계를 봅니다.
5. [실행 재현 기록](docs/REPRODUCIBILITY.md)에서 새 가상환경·한글/공백 경로 시험, 실패 수정과 최종248개 Python 검증을 확인합니다.

## 한 번씩 실행하기

Windows의 PowerShell 7 이상에서 Python 3.12와 .NET SDK 10.0.401을 사용합니다. Python 실행 파일을 자동으로 찾지 못하면 전체 경로를 지정합니다.

~~~powershell
.\scripts\setup.ps1
# 필요할 때만:
.\scripts\setup.ps1 -PythonPath "C:\Path\To\Python312\python.exe"
.\scripts\verify.ps1
.venv\Scripts\python.exe scripts\run_emulator_demo.py --isolate --count 3 --scenario DATA-QUALITY --seconds 70
~~~

verify가 Release 빌드와 시험을 수행합니다. 완료 후 마지막 명령으로 WPF 데모를 시작하세요. 실행 로그에 표시된 SQLite 경로의 원시 기록은 종료 뒤에도 보존됩니다. 자세한 순서와 화면에서 볼 항목은 [데모 가이드](docs/DEMO_GUIDE.md)에 있습니다.
