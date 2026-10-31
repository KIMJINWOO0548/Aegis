# Aegis Insight v3.4 — Astra 실행 지시서

**정본:** `AEGIS_CONTRACT_v3_4.yaml` → 실행 계약; `Aegis_Product_Plan_v3_4.docx` / PDF → 제품/검증 방향. **두 문서가 불일치하면 진행하지 말고 `docs/ADR-CONTRACT-CONFLICT.md`에 남기고 확인받을 것.**

## v3.4 추가 범위 규칙 — 연구 기반 제품 개선

- 첫 데모는 **절대 확장하지 않는다**: DEG-02 하나, EWMA 하나, WPF 실시간 Trend/경고/ACK. 새 12개 fixture 및 API는 D1/D2/P1에 배치한다.
- D1: `advisoryManagement`의 현장 알람/ML advisory 분리, hysteresis·중복 묶음·capture state, `ALM-01~04`, `OBS-03~04`를 완료 조건에 반영.
- D2: UCI Hydraulic #447 (실험 장비) cycle-based 평가와 출처/라이선스 기록, mode-specific reference 검증, `MLX-01~02`. NASA C-MAPSS는 보조 벤치마크로만 사용.
- P1: 서면 OT 승인 후 OPC UA trusted certificate/overflow/revised interval, Modbus read-only function-code 허용목록, PLC 부하 수치 시험. `OBS-01~02`, `SEC-01`.
- **v3.4 YAML만 정본**: 버전만 바꾸고 v3.3 이벤트를 임의 삭제하지 않는다. 제품 전략과 기술 선택 근거는 `ENGINEERING_ADDENDUM_v3_4.md`, 근거 URL은 `RESEARCH_EVIDENCE_v3_4.md`를 참고한다.
- 사용자·현장 안전·ML 현장 예지 성능은 실제 시험 전 모두 미확인. 합성/공개 실험셋 결과로 고객 고장 예방 실적을 주장하지 않는다.

## 0. 공통 절대 제한

- **기본 모드: Mock only.** 실제 PLC/실제 고객망 접속, 생산 장비 쓰기, 유료 AI 호출, 모델 자동 학습/배포 금지.
- 상품/안전 성능을 실측·인증 받았다고 주장 금지. 고객 자료·회사 내부 문서·실제 센서 로그를 요구하거나 복제하지 말 것.
- 계약/IP 확인(G-LEGAL)이 해결되지 않으면 영업, 상업 배포, 고객 접촉, 실데이터 취득을 진행하지 말 것.
- 매 요청마다 **해당 Gate만 구현하고 중지**. 산출물: 변경 파일, 명령, 진짜 테스트 로그, 실패, 예상 대비 소요, 다음 차단 조건.
- 독립 정답/인젝션 메타데이터는 소스 에이전트 워크스페이스 밖. 메타데이터가 노출되는 경로를 차단하는 CI 검사 포함.
- 모든 서술과 테스트 fixture ID, enum, 관측 schema는 YAML과 일치해야 한다. `NO_MODEL`은 모델 상태, `NORMAL`은 평가 결과.

## 1. G-LEGAL — 사용자가 수행하는 선행 확인

Astra는 법률 준수 여부를 대신 확정할 수 없다. `LEGAL_IP_CHECKLIST.md` 템플릿을 만들고, 확인되지 않은 조항은 `미확인`으로 표시한다. 근로/고용 상태를 추측하지 않는다. 상용 라이선스·지식재산·고객 데이터 권리 확인 없이는 실제 판매를 시도하지 않는다.

## 2. D0a — 첫 요청 프롬프트 (복사해서 사용)

```text
Aegis Insight v3.4의 D0a만 구현하세요. 이번 목적은 빠른 thin-demo의 기반입니다.
첨부한 AEGIS_CONTRACT_v3_4.yaml을 타입·이벤트·fixture의 단일 계약으로 사용하고,
Aegis_Product_Plan_v3_4.docx(또는 PDF)와 ASTRA_GATES_v3_4.md를 읽으세요.
상용 장비는 연결하지 않으며 오직 독자 작성 Mock/simulator만 사용합니다.
1. .NET LTS WPF·Python FastAPI·pytest/xUnit·Windows CI 뼈대 생성. 설치된 SDK 버전 확인 후 잠금.
2. 계약 YAML enum/required 필드 → Python Pydantic 및 C# DTO 생성/검증.
3. Windows WPF build, Python 테스트, 계약 drift 검사 실행.
4. .NET Worker Service와 Python Windows 서비스 방식의 'hello heartbeat' 최소 스파이크를 비교하고 ADR 작성.
5. 공통 구조화 로그 스키마/traceId 상관관계/JSONL 기록 최소 스파이크를 구현하고 민감정보 차단 테스트를 실행. 테스트에서 센서 표본 전체를 INFO 로그로 중복 남기지 않음.
6. 예측 소요와 실제 소요를 구현·통합·테스트·UI 검수로 나누어 ESTIMATES에 기록.
7. 현재 수행한 명령/로그/실패/변경 파일을 정확히 보고하고 여기서 중지.
구현·검증되지 않은 기능을 완료 표시하거나 다음 Gate를 자동 진행하지 마세요.
```

### D0a 수용 조건

- clean checkout에서 `dotnet build`, xUnit, `pytest`, 계약 검사 실제 실행 결과 남김.
- Windows runner 자동 빌드 확인. 공통 로그 스키마와 추적/Redaction 단위 테스트 확인. Worker 서비스 비교 ADR 기록. 실제 서비스 설치는 D1에서 검증.
- 실장비 live adapter 구현·사용 없음.

## 3. D0b — 결정적 입력/평가 경계

**입력:** `DEG-02` 하나만. 정상 `PRODUCTION` 상태에서 목표 유량 대비 실제 유량 오차가 점진적으로 증가하도록 구현하되 **정답 열화 시작·고장 원인·정답 알람 시각을 모델 입력에서 제외**한다. 관측은 신호별 행 `ObservationBatch`.

**Astra에 지시:** EWMA 60초/30초 갱신으로 Mock 경고를 생성. 경고 발생 이벤트에 동일 warningId/traceId와 입력 품질/모델 버전(안전한 메타데이터만)을 남길 것. 초기 하이퍼파라미터/임계값은 eval 이전 동결. `NORMAL`을 model state로 사용하지 않음. 독립 러너만 private truth 접근. 정적 검사 + 작은 공개 fixture로 정상/점진 열화 테스트. 통과 전 D-demo 진행 금지.

## 4. D-demo — 1화면 조기경고 세로 흐름

WPF Overview + Warning Detail **패널**만. 시뮬레이터 정상 → DEG-02 injection → EWMA 경고 → 피처·센서 품질·추세 표시 → **점검 권고 ACK(장비 command가 아님)** → **경고와 연결된 구조화 이벤트+감사 저장** → UI 재실행 후 warningId 기준 조회. 서버/ML 중단은 `UNAVAILABLE`/`DEGRADED`이며 정상 경고로 오도하지 않음.

**시험:** 잘못된 관측 시간/결측/모드 전환은 ML 결과 `INSUFFICIENT_DATA`, 동일 ACK 재요청은 중복 감사 생성하지 않음, API 죽으면 오류 상태. 2~3분 동영상은 동일 fixture로 재생 가능해야 함.

## 5. D1 — 독립 수집 서비스 + 저장·부하

Windows Worker Service auto-start/stop/uninstall, WPF 종료 후 5분간 수집, OS 재부팅 뒤 자동시작, DB migration·복구, 8signals × 1Hz 성능과 WAL/인덱스 증가·1분 집계, JSONL rotation/디스크 가득 참/로그 큐 오버플로, raw retention 정책(초깃값 72h, aggregate 90d) 테스트. 실제 DB 크기/CPU/메모리 측정 결과 첨부. 이 과정은 모두 가상 서버 기준.

## 6. D2 — 추가 ML과 외부 데이터

CUSUM/IsolationForest + 적절한 mode-specific reference; 정상 기준 분포의 percentile 표시; drift 및 DEGRADED 규칙; 최소 300초 embargo + 시드/사고 그룹 분리; NASA PCoE C-MAPSS 1종 외부 독립 데이터 파이프라인 체크. **C-MAPSS도 시뮬레이션이라는 점을 명시**. 모델 아티팩트 해시 및 롤백, ONNX 변환/점수 비교(가능할 경우). `ML_EVAL_REPORT.md`에 정본의 합성 성능 한계 문구 필수.

## 7. D3 — 사고 설명/LLM 연결(기본 오프라인)

규칙 기반 설명, optional LLM Mock, 근거 ID 검증, 이벤트·감사, 시뮬레이터에서만 등록 Action 테스트. **Insight의 실장비 쓰기 API를 만들지 않음.** 명령 상태와 UNKNOWN 검사는 오직 후속 Assist 평가 사양으로 분리.

## 8. D4 — Windows 설치·유지보수

격리 Windows VM에서 설치/실행/서비스 복원/제거/마이그레이션, 모델/설정 백업·롤백, 로그 검색·Redaction·사전 미리보기·고객 동의형 지원 번들, 세션 권한, UI DPI 및 장애 상태 검증. install/deploy 설정과 licence 검토 기록. 인증/양산 검증 주장 금지.

## 9. P1/P2 이후 — 고객 허가와 별도 담당자 판단

P1 실제 연결은 서면 OT 동의, 장비별 polling/subscription/connection 상한을 먼저 설정하고 **읽기만 허용하는 contract tests를 선행**. P2 SHADOW 이후 운영자와 ADVISORY 결과를 평가. 그 전에는 lead time을 현장 성과라고 판매하지 않는다.

## 9.1 로그·고객 지원 추가 명세 (v3.4)

자세한 계약과 독립 테스트는 `LOGGING_TREND_OBSERVABILITY_SPEC_v3_3.md`를 따른다. **D0a~D-demo에는 traceId/warningId, EWMA 경고 및 ACK 감사까지만** 필수. 원격 전송/로그 대시보드/지원 번들은 D4 이후. 테스트 `LOG-01~LOG-08`의 실행·비실행 상태를 구분하고, debug/INFO 폭주·토큰 유출·거짓 ACK 성공을 금지한다. 각 Gate 보고서에 로그 샘플(민감정보 제거), 실제 보존/용량 측정값, 실패 로그/누락 로그, 로그 관련 회귀 테스트를 첨부한다.

## 9.2 Trend·지표·장애 추적 구현 계약 (v3.4)

단일 계약 `AEGIS_CONTRACT_v3_4.yaml`, `OPENAPI_THIN_DEMO_v3_4.yaml`와 세부 명세 `LOGGING_TREND_OBSERVABILITY_SPEC_v3_3.md`가 정본이다. **다음 요구사항을 개발 한 번에 모두 구현하지 말 것**.

- **D0a:** observation/event/trend contract 변환, `GET /v1/devices/{id}/trends`와 `/v1/warnings/{id}/timeline` 응답 Mock 계약 테스트, 민감값 검출. 실제 데이터 조회는 이후 Gate.
- **D0b:** DEG-02 유량 신호, `flowTrackingErrorLpm`, `flowErrorEwmaLpm`, 샘플 품질 및 5초 gap. 테스트 TRD-01,02.
- **D-demo:** WPF Overview에서 `setpointFlowLpm/actualFlowLpm` + 경고 마커와 단일 warningId timeline(경고→ACK)을 렌더링. '로그 탐색기'는 이 단계 범위가 아님. TraceId와 warningId가 프로세스 경계를 넘어 동일하게 이어지는지 확인. UI/백엔드 재시작 후 ACK 중복 금지.
- **D1:** raw 72h, 1m aggregate 90d, 8 signals×1Hz 저장량 실측, WAL/인덱스, 결측/품질·다운샘플 테스트, 24h 디바이스 Trend. 운영 Health metrics.
- **D2:** ML score percentile, threshold/model/mode overlay, 모델 성능/드리프트 + 데이터 품질 투명 표시. 합성 성공으로 고장 예방 주장 금지.
- **D3:** 필터·기간·traceId·warningId 로그 탐색기, 경고 Trend 드릴다운. 대량 검색 범위 제한/권한 체크.
- **D4:** 민감정보 제거·고객 동의형 지원 번들, 내보내기 감사, 보존·백업/복원·파일 보호.

상세 수용조건은 TRD-01~TRD-14와 기존 LOG-01~LOG-08로 구성. 게이트 보고서에는 실제 테스트 실행명령·결과·API 응답 예시·WPF 캡처·로그 drop 수·DB 증가량을 구분해 기록. 구현하지 않은 Trend 화면과 실장비 성과를 완료로 표기하지 않음.

## 10. Gate 종료 보고 양식

```text
Gate: D0a | Commit: ... | Status: IMPLEMENTED/VERIFIED_SIM (근거가 있을 때만)
Changed files: ...
Build commands and actual logs: ...
Unit/contract/E2E tests: total/pass/fail/skipped ...
Estimated vs actual: implementation ... / integration ... / tests ... / UI ...
Constraints & actual read-only verification: ...
Observability: schema/trace tests ... | trend query 5m/1h ... | gap/aggregation ... | redaction ... | log gaps/drop count ...
Known limitations and blockers: ...
Required user approval for next gate: ...
```
