# 독립 에뮬레이터 연결·성능 검증

이 문서는 최초 8신호·1Hz 연결 구현 당시의 기록이다. 이후 이상 값 대응·수백 개 숫자 신호·원시 보관·모듈별 프로세스가 추가되었으며 현재 실행과 한계는 [모듈별 기록 보고서](DURABLE_INGESTION_REPORT.md)를 기준으로 한다. 아래 수치·NO_MODEL 범위는 최초 시험에만 적용된다.

2026-10-18. 사용자의 우선순위 변경에 따라 디자인 작업을 종료하고, 별도 에뮬레이터 프로젝트와 로컬 HTTP 수집·재연결을 구현했다. 이 연결 범위는 기존 D0a 이후의 새 요청이며 EWMA·실장비·영구 저장 Gate 전체 완료를 뜻하지 않는다.

## 구성과 실행

- 독립 프로젝트: `emulators/Aegis.Emulator`. 자체 pyproject, 테스트, 실행 명령을 가지며 Aegis 내부 모듈이나 외부 런타임 패키지를 import하지 않는다.
- 장비마다 별도 프로세스, ID·포트·시드·토큰 설정. Aegis 설정 파일의 sources 배열에 추가한다. 현재 상한 32개, 검증한 성능 범위는 1·4·8개다.
- 흐름: 에뮬레이터의 GET /v1/snapshot → Aegis 비동기 수집기 → 로컬 API → WPF 장비 선택·최신 8개 신호. 에뮬레이터 원시 프로토콜 1.0과 기존 Observation v3.4.0을 구분한다.
- 최신 배치만 보관한다. 수집별 순차 요청, 장비 간 동시 처리, 500ms 전체 응답 제한, 최대 64KiB, 재시도 폭주 방지, 프록시·리다이렉트 금지. 토큰은 환경변수에서만 읽는다.
- 연결 상태는 모델 상태와 별개다. 항상 SIMULATOR / NO_MODEL이며, 중단된 데이터는 STALE 또는 MISSING, 평가는 UNAVAILABLE. 실제 경고·점수·장비 명령은 생성하지 않는다. 에뮬레이터 모드에 고정 Fixture 경고·추세를 섞지 않는다.

가장 간단한 실행:

```powershell
dotnet build Aegis.slnx --configuration Release
.venv/Scripts/python.exe scripts/run_emulator_demo.py --count 2
```

명령이 새 토큰·사용 가능한 포트를 생성하고 독립 에뮬레이터 2개와 API·WPF를 실행한다. 장비 선택 메뉴에서 전환한다. 창을 닫거나 Ctrl+C로 이 명령이 시작한 프로세스를 정리한다. GUI 없이 짧게 확인하려면 `--headless --seconds 10`을 추가한다.

수동 구성은 각 에뮬레이터 프로젝트 폴더에서 별도 터미널로 실행한다. 같은 토큰을 사용할 경우 각 터미널과 API 환경에 동일한 `AEGIS_EMULATOR_TOKEN`을 설정한다. 서로 다른 토큰은 설정의 tokenEnvironment에 서로 다른 환경변수 이름을 지정한다. 토큰을 JSON에 넣지 않는다.

```powershell
# emulators/Aegis.Emulator 폴더, 인스턴스별 별도 터미널
../../.venv/Scripts/python.exe -m aegis_emulator --device-id EQ-SUPPLY-01 --port 8771 --seed 1
../../.venv/Scripts/python.exe -m aegis_emulator --device-id EQ-SUPPLY-02 --port 8772 --seed 2

# Aegis 루트, API 터미널
.venv/Scripts/python.exe -m aegis.api --emulators config/emulators.example.json

# API와 같은 AEGIS_API_TOKEN 환경을 가진 다른 터미널
dotnet run --project src/Aegis.Desktop --configuration Release
```

## 실제 검증

| 검사 | 결과 |
|---|---|
| Python 전체 | 129 통과, 실패·건너뜀 0; 상위 Starlette의 httpx 지원 변경 경고 1개 |
| 독립 에뮬레이터 표준 라이브러리 테스트 | 11 통과 |
| .NET 계약·Worker·데스크톱 클라이언트 | 61 통과, 실패·건너뜀 0 |
| Release 솔루션 빌드 | 경고 0, 오류 0 |
| YAML/생성 타입/OpenAPI/참조 SQL 검사 | 통과; 기존 v3.4 계약 변경 없음 |
| 실제 별도 프로세스 연결·한 장비 종료·재시작 | 통과. 다른 장비 갱신 유지, 끊긴 값 GOOD 제거, 새 runId와 초기화 순번 수락 |
| GUI 통합 | 실제 WPF에서 EMULATOR/연결됨/NO_MODEL과 갱신되는 8개 신호 확인 |

증거: `evidence/emulator-python-tests.xml`, `evidence/emulator-dotnet-results/*.trx`, `evidence/emulator-integration.json`, `evidence/emulator-desktop-connected.png`. 모든 시험에서 생성한 자식 프로세스를 정리했다.

발견·수정한 문제:

- 바이트를 조금씩 보내는 응답은 개별 소켓 읽기 제한만으로 끝나지 않을 수 있었다. 재현 테스트 실패 후 전체 요청 제한을 추가했고 해당 테스트가 통과했다.
- Windows Proactor 소켓 수락에서 WinError 64 후 재시도가 진행되지 않는 현상을 실제 CLI 시험으로 확인했다. 이 TCP 전용 API는 Windows에서 Selector 루프를 사용하도록 수정했다. 기존 CLI 시험과 전체 129개가 다시 통과했다. 현재 32개 소스 제한은 Windows Selector의 소켓 수 한계보다 충분히 작다.
- Windows 가상환경 실행 래퍼의 메모리를 잘못 측정하거나 종료 직후 로그 파일 잠금이 남는 것을 피하도록, 측정 도구는 실제 Python 인터프리터를 직접 소유·측정한다.

## 성능 측정

Windows 11, Python 3.12.14, 논리 CPU 8개. 장비당 8신호×1Hz, 규모별 10초 창, 각 1회. 일반 데스크톱 환경에서 실행했으며 전용·유휴 벤치마크 머신이 아니다. API·수집기와 에뮬레이터 프로세스만 측정했다. WPF·ML·DB 비용은 제외한다. CPU는 코어 1개를 100%로 계산하며 메모리는 샘플링한 working set이다.

| 에뮬레이터 | 받은 신호값/초 | API 응답 p95 | 수집 요청 p95 | API 메모리 | 에뮬 합계 메모리 | API CPU |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8.0 | 4.557ms | 62.627ms | 55.855MiB | 23.039MiB | 2.031% |
| 4 | 32.0 | 9.589ms | 103.527ms | 56.129MiB | 91.852MiB | 5.156% |
| 8 | 64.0 | 21.434ms | 83.699ms | 56.398MiB | 183.895MiB | 8.125% |

이번 측정에서는 거부·순번 누락이 모두 0이며 끝까지 CONNECTED였다. 이는 최대 처리량이나 무손실 수집 보장이 아니다. 시작 시점·운영체제 스케줄링·응답 지연에 따라 latest-only 구조에서 순번 누락이 발생할 수 있고, 그 수를 별도로 표시한다. 정상 1Hz 흐름을 확인한 짧은 측정으로 장시간 안정성이나 현장 장비 성능을 보증하지 않는다.

원본 수치: `evidence/emulator-benchmark.json`. 재실행:

```powershell
.venv/Scripts/python.exe scripts/benchmark_emulators.py --counts 1 4 8 --seconds 10
```

## 남은 범위

장기 부하·포화점·고속 샘플링·32개 규모는 미검증이다. 최신 값만 보유하므로 영구 이력, 유실분 재전송, DB/WAL 성능은 구현하지 않았다. DEG-02/EWMA·경고/ACK·실제 Modbus/OPC UA·Windows 서비스 설치는 다음 별도 단계다. 150% DPI와 원격 Windows CI도 미검증이다. 별도 에뮬레이터 프로젝트는 이번 폴더에서 독립 실행 가능한 코드 프로젝트이며 별도 원격 저장소는 만들지 않았다.
