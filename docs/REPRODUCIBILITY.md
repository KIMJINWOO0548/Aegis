# 실행 재현과 검증 증거

2026-10-19. `scripts/setup.ps1`과 `scripts/verify.ps1`을 로컬·GitHub Actions가 함께 사용한다. 요구 환경은 Windows, PowerShell7 이상, Python3.12, .NET SDK10.0.401이다. 설치 스크립트는 시스템 SDK를 임의 설치하거나 기존 가상환경을 삭제하지 않는다.

## 실제로 실행한 범위

기존 `.venv`, `bin/obj`, runtime, artifacts를 제외한 소스를 **한글·공백이 포함된 별도 폴더**에 복사했다. 다른 작업 디렉터리에서 절대 경로로 설치 스크립트를 호출해 새 가상환경과 고정 의존성을 설치하고 .NET locked restore를 수행했다. Python 소스와 시험 파일이 원본과 같은지 해시로 대조했다. 같은 Windows 호스트와 pip/NuGet 캐시를 사용했으므로 새 운영체제·독립 VM 시험은 아니다.

```powershell
.\scripts\setup.ps1 -PythonPath "C:\Path\To\Python312\python.exe"
.\scripts\verify.ps1
```

최종 검증 결과:

| 검사 | 실제 결과 |
|---|---|
| 생성 계약 drift / v3.4 계약·참조 SQL | 통과 |
| Python 전체 | 248개 통과, 실패·건너뜀0 |
| 독립 에뮬레이터 | 25개 통과 |
| .NET locked restore | 통과 |
| Release 빌드 | 경고0·오류0 |
| .NET 시험 | 74개 통과, 실패·건너뜀0 |

[기계 판독 요약·검사별 로그](evidence/portfolio/verification-summary.json), [pytest JUnit](evidence/portfolio/fresh-copy-python.xml), [.NET TRX](evidence/portfolio/fresh-copy-dotnet.trx). Python의 Starlette/httpx deprecation 경고1개는 남아 있다.

## 새 폴더 시험에서 발견한 실패

첫 실행에서는 PowerShell 배열 안의 문자열 결합이 JUnit 인수를 나누어 pytest가0개 시험 후 종료 코드4를 반환했다. 전체 검증도 성공으로 표시하지 않고 실패했다. `--junitxml`과 출력 경로를 별도 인수로 넘기도록 수정하고, 실제 PowerShell 파싱에 한글·공백 경로를 넣는 [회귀 시험](../tests/test_delivery.py)을 추가했다. 이 시험의 실패와 수정 후 통과를 확인한 뒤 전체 스크립트를 다시 실행했다.

[첫 시도 실패 요약](evidence/portfolio/fresh-copy-first-attempt.json)을 보존한다. 당시 개별 로그는 재실행에 덮어써져 보존됐다고 주장하지 않는다. 최종 성공 로그는 별도 evidence 폴더에 복사했다.

## CI와 공개 상태

[Windows CI 정의](../.github/workflows/windows-ci.yml)는 같은 설치·검증 스크립트를 호출하고 실패 시에도 결과를 업로드하도록 구성했다. **원격 GitHub Actions 실행 결과는 없다.** 현재 폴더는 Git 저장소가 아니며 이 작업에서 원격 저장소 생성·공개·push를 하지 않았다. 실제 원격 실행은 사용자가 지정하는 저장소에 연결한 뒤 별도로 확인해야 한다. 가짜 CI 배지나 성공 링크는 넣지 않았다.

실장비 통신·장기 부하·전원 차단·다른 Windows 기기의 설치 안정성은 검증하지 않았다. 간헐적인 Windows 소켓 초기화 오류의 과거 실패 기록도 [모듈 보고서](DURABLE_INGESTION_REPORT.md)에 남아 있으며 이번 성공이 그 원인의 해결을 뜻하지 않는다.
