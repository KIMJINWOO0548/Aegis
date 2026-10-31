# D0a 구현 및 로컬 검증 보고

이후 사용자가 승인한 에뮬레이터·이상 값·수백 신호·원시 기록·모듈 프로세스 확장은 [현재 기록 보고서](DURABLE_INGESTION_REPORT.md)를 참고한다. 아래 결과는 최초 D0a 범위의 기록이다.

검증일: 2026-10-18 KST. 상태: **Mock 소스 구현 및 로컬 검증 완료, 원격 Windows CI 미실행**.
Gate 전체 승인 또는 현장 검증 완료를 의미하지 않는다. Git 저장소/원격 연결은 만들지 않았다.

## 구현 결과

- YAML에서 Python/C# enum·상수 생성 및 생성물 drift 검사.
- Observation, DeviceHealth, Warning, 구조화 이벤트, Trend, Timeline의 Pydantic/C# 계약.
- 결측 null, GOOD/null 금지, UTC offset, 유한 숫자, 엄격한 boolean/enum/필수 필드 검증.
- 로컬 FastAPI Mock API: 토큰·Host·Origin 검사, requestId/traceId, 조회 한도, 안전한 오류 응답.
- JSONL 허용목록으로 원시 센서 데이터·민감 필드 차단. 쓰기 실패는 응답/health에 DEGRADED로 표시.
- WPF 시작 화면과 .NET/Python 유한 heartbeat. 실제 수집/장비 상태를 가장하지 않는다.
- Windows CI workflow, Python 의존성 고정, .NET SDK 10.0.401 및 NuGet lock 파일.

## 실제 실행과 결과

| 명령 | 결과 | 증거 |
|---|---|---|
| `python scripts/generate_contracts.py --check` | 통과 | `evidence/contract-check.log` |
| `python scripts/validate_v34.py` | 통과, 참조 SQL은 메모리 DB 구문 검사만 | 동일 |
| `python -m pytest -q --junitxml=docs/evidence/python-tests.xml` | **102 통과 / 실패 0 / 건너뜀 0**, 26.03초 | `evidence/python-tests.log`, XML |
| `dotnet build Aegis.slnx --configuration Release --no-restore` | **경고 0 / 오류 0**, WPF 포함, 12.36초 | `evidence/dotnet-build.log` |
| `dotnet test Aegis.slnx --configuration Release --no-build --logger trx --results-directory docs/evidence/dotnet-results` | **32 통과 / 실패 0 / 건너뜀 0** | `evidence/dotnet-tests.log`, TRX |
| `dotnet restore Aegis.slnx --locked-mode` | 4 프로젝트 통과 | `evidence/dotnet-locked-restore.log` |
| 두 별도 프로세스 heartbeat, count 3 / interval 20ms | 종료 코드 0, 공통 traceId 및 이벤트 계약 통과 | `evidence/heartbeat-comparison.json`, JSONL |
| 실제 API 프로세스 localhost 조회 | 인증된 health/Trend/Timeline, 인증 없는 401 | pytest의 `test_cli_serves_authenticated_mock_routes_on_loopback` |
| `pip check` | 의존성 오류 없음 | 실행 출력 확인 |

위 Python 명령은 모두 `.venv\Scripts\python.exe`로 실행했다.
pytest 경고 1개는 Starlette의 httpx TestClient 사용 중단 예정 안내다. 고정 조합에서 통과하며 경고를 숨기지 않았다.

## 검토 후 수정

- 없는 제품 계획 Markdown과 오래된 계약 참조를 정정했다. 원본 DOCX/PDF는 보존했다.
- OpenAPI의 결측값/중첩 객체/오류 응답/enum을 실제 타입에 맞췄다.
- 숫자 문자열·공백 날짜를 받던 Python 경로와 boolean 자동 변환을 제한했다.
- 진단 로그 저장 실패를 숨기던 API를 회귀 테스트 후 수정했다.
- C# 루트 DTO 배열/목록의 검증 우회를 수정했다. GOOD/null·잘못된 버전·null 항목의 회귀 테스트 3개 실패를 재현한 뒤 전체 32개 통과를 확인했다.
- Worker 빈 구현을 Generic Host 실행으로 교체했다. 옵션/이벤트 테스트 11개 실패를 확인한 뒤 통과시켰다.

실패 증거는 `python-*-red.log`, `dotnet-worker-red.log`, `dotnet-collection-red.log`에 보존했다.
초기 API 수동 probe는 timeout/WinError 64로 실패했다. 이후 실제 프로세스 시험과 최종 전체 pytest는 통과했다. 초기 Windows 소켓 오류 원인은 확정하지 않았다.
API/로그/heartbeat와 C# 계약은 다른 에이전트가 교차 검토했다.

## 예시와 관측 범위

`evidence/mock-api-example.json`은 실제 Mock 응답에서 추출한 보고용 요약이다.
2026-10-16 00:00~00:05 UTC 조회는 목표·실제 유량 각 296점, 명시적 5초 결측 구간, `warn-demo-02`의 고정 예제를 반환한다.
실시간 ML 결과가 아니며 SIMULATOR, NO_MODEL, INSUFFICIENT_DATA를 구분한다.
ACK·시나리오 실행과 1m/1h 집계는 501로 거부한다.

민감정보 제거 JSONL 예시는 두 `*-heartbeat.jsonl`이다. 센서 표본을 INFO 로그로 복제하지 않았다.
보존/용량/queue drop/DB 증가량은 미측정이다. 원격 업로드와 제품 DB는 구현하지 않았다.

## 미검증과 다음 조건

- 원격 저장소가 없어 Windows runner 미실행. workflow 및 로컬 통과를 runner 통과로 표기하지 않는다.
- 실제 서비스 설치/자동 시작/재부팅/복구, 장기 수집, DB·보존·부하·rotation·디스크 부족은 D1 이후다.
- WPF는 선택한 1번 시안을 반영했다. 100% 표시에서 980×720 / 640×480 실행 화면, 키보드, 안내창과 창 조작을 확인했다. 비교 증거는 `../design-qa.md`에 있다. 150% DPI는 미검증이다.
- G-LEGAL은 미확인. 실장비·고객 데이터·판매·배포·유료 AI 호출은 진행하지 않았다.
- 독립 비밀 정답 데이터/평가 러너는 아직 없다. D0b에서 접근 격리와 CI 누출 검사를 구성해야 한다.
- LOG-01~08 / TRD-01~14 전체 완료를 주장하지 않는다.
- 사용자 요청으로 Product Design/Figma를 설치했고 사용자 연결 완료를 확인했다. Product Design 시안 3개 중 사용자가 선택한 1번을 WPF에 반영했다. Figma 파일 작성은 진행하지 않았다. 브리프는 `UI_DESIGN_BRIEF.md`에 있다.

다음 Gate로 자동 진행하지 않았다. D0a 결과 검토, 원격 CI 확인, D0b 범위 확인이 다음 조건이다.
