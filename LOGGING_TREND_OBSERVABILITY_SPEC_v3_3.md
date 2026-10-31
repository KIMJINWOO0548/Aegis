# Aegis Insight v3.3 — 로그·Trend·이상 추적·유지보수 상세 명세

**문서 상태:** v3.3 상세 설계의 보존본, 기능 구현·현장 검증 전. **v3.4 구현 정본:** `AEGIS_CONTRACT_v3_4.yaml`의 상태·필드·코드·API 이름을 따른다. 아래 v3.3 예제의 버전 표기는 과거 예시이며 현재 출력은 `3.4.0`이다. **대상:** Windows 온프레미스, Insight 읽기 전용, 공급 장치 Mock 먼저. **선행 문서:** v3.2의 구조화 로그·감사·시계열 정책을 보존해 확장함.

## 1. 사용자 문제와 제품 요구사항

**현장 운영자:** 경고의 숫자만 보지 말고 '어느 신호가 언제부터 평소와 달라졌는지', '다른 원인은 없는지', '실제 조치가 필요한지'를 이해해야 함. **유지보수 엔지니어:** 며칠 뒤 연락받아도 동일 warningId로 당시 유량·모드·통신품질·모델 설정·담당자의 ACK를 재구성해야 함. **개발자/고객 IT:** 수집 주기 지연, 메모리·DB 증가, 오류·모델 변경의 영향을 추적해야 함.

- 제품의 약속은 '조기에 이상 징후를 **관찰·설명**'하는 것. 합성 시뮬레이터 경고와 외부 공개 데이터 평가를 현장 고장 예방 실적으로 광고하지 않음.
- **로그(Log)**는 사건/처리 이유, **관측(Observation)**은 센서 원시 시계열, **지표(Metric)**는 지속 상태와 집계, **추적(Trace)**은 한 작업의 인과 관계, **감사(Audit)**는 누가 어떤 상태 변경을 시도했는지 담당. 동일 값을 4곳에 중복 복사하지 않음.
- 기본값은 오프라인 동작. 자동 원격 전송, 실장비 쓰기/재시작/정지/ACK는 제품에 없음. WPF의 ACK는 '점검 권고 확인'이지 PLC 명령이 아님.

## 2. 전체 데이터 흐름과 세분화된 저장 계층

```text
Sim/READ-only Adapter -> Collector -> ObservationBatch -> raw_observations.db
                                        |-> 1min / 1h trend aggregates
                                        |-> ML feature windows -> model_evaluations
                                        |-> warning snapshot + timeline -> warnings/audit.db
WPF/API/Collector/ML -> structured JSONL events -> bounded rotating diagnostic logs
                      -> service metrics (internal counters) -> health view
warningId / traceId / deviceId / eventId links -> trend + log + decision + ACK
```

| 데이터 경로 | 적합한 형태 | 기록 조건 | 초기 보존 가설 | 장애 시 처리 |
|---|---|---|---|---|
| 원시 센서 `observations` | SQLite 분리 DB, batch insert | 신호별 설정 주기 (데모 1Hz) | 72h 원시, 고객 설정 가능 | 수집 큐·디스크 부족을 탐지하고 gap 이벤트 기록 |
| 1분 집계 `trend_1m` | SQLite | raw에서 검증된 품질 집계 | 90일 | raw와 집계 불일치 시 오류 표시·재생성 |
| 1시간 집계 `trend_1h` | SQLite | 1분 집계 기반 유효 수치 | 365일 **후속 가설** | 원본 없으면 회복 불가 표시 |
| ML 평가 `model_evaluations` | SQLite | 30초 평가, 경고 전후 전부 | 90일 초깃값 | 모델 미실행/검증불가 상태 보존 |
| 이벤트 로그 `*.jsonl` | 비동기 큐 + rotation | 변화·오류·복구·규칙 변경 | 컴포넌트별 10MB×5, 최대 14일 중 **먼저 도달한 조건** | DEBUG/INFO 저우선 로그 우선 생략, drop count 경고 |
| 감사 `audit_events` | SQLite 원자 트랜잭션 | ACK·설정·내보내기 시도 모두 | 90일, 고객 정책·관련 법규 확인 | 감사 실패 시 mutation 성공 응답 금지 |
| Warning snapshot | 해당 경고의 관측·모델 버전 연계 | 경고 발생 | D1 이후 필요한 신호만 전 10분/후 10분, 14일 **가설** | 공간 상한 도달 시 미보관 상태 명시 |

**숫자는 초기 제안값일 뿐 고객 요구·장비 성능 시험 전 확정 규격이 아니다.** 8신호×1Hz는 하루 691,200개의 원시 관측 행이다. DEBUG 로그에 이를 중복 기록하지 않는다. 평균 행 크기는 실제 SQLite 인덱스·WAL·압축 정책으로 측정하며 저장 용량을 추측값으로 확정하지 않는다.

## 3. 상세 관측(Trend) 계약

### 3.1 신호와 파생 지표

첫 공급 장치 신호는 기존 정본의 `setpointFlowLpm`, `actualFlowLpm`, `supplyPressureKpa`, `valveCommandPct`, `valveFeedbackPct`, `motorCurrentAmp`, `temperatureC`, `cycleCount` 8개를 사용한다. 기존 `Observation` 필수 필드(관측·수신 UTC, modeId, quality, stateVersion 등)는 변경하지 않는다.

| 시계열 | 단위·계산 | UI 설명 | 원칙 |
|---|---|---|---|
| `setpointFlowLpm` / `actualFlowLpm` | L/min | 목표 vs 실제 유량 | 같은 축, 원시 vs 집계 구분 |
| `flowTrackingErrorLpm` | `abs(setpointFlowLpm - actualFlowLpm)` | 유량 추종 오차 | 입력 타임스탬프 정렬; 미정렬·결측 구간은 null |
| `flowErrorEwmaLpm` | 기존 EWMA α=0.2 (초기가설) | 완만한 이상 추세 | 모델 버전과 config revision 표기 |
| `motorCurrentAmp` | A | 부하 증가 여부 | 원인 단정 금지; 다른 모델 입력 여부 구분 |
| `supplyPressureKpa` | kPa | 압력의 동반 변화 | 다른 단위는 같은 축에 겹치지 않음 |
| `normalReferencePercentile` | 0~100 percentile | 평소 분포 대비 이상도 위치 | **확률/고장률 아님**, 정상 참조 버전 필수 |
| `expectedCount` / `validCount` | count | 데이터 신뢰도 | 누락·stale·bad를 정상 관측처럼 보간 금지 |
| `pollDurationMs`, `receiveDelayMs` | ms | 수집 지연 Trend | 모노토닉·기기시간 차이 구분 |

### 3.2 집계 수학 / 샘플 품질

- 조회 및 집계는 **UTC 타임 버킷**. 로컬 표시는 운영체제 시간대로 변환하며 데이터 원본 UTC는 유지.
- 1분 집계는 **같은 deviceId + signalKey + modeId + bucketStartUtc**별로 `sampleCount`, `validCount`, `missingCount`, `badCount`, `staleCount`, `min`, `max`, `avg`, `first`, `last`, `sum`, `sumSquares`, `unit`, `aggregationVersion`, `isComplete`를 저장. `min/max/avg`는 GOOD 표본만 사용; BAD/STALE은 별도 비율 표시. 없는 표본을 0으로 계산하지 않음.
- `expectedCount`는 신호별 설정 폴링 주기와 해당 구간의 운전 가능 상태를 근거로 추정하고 정책과 주기를 표시. 단순 고정 60개라 가정하지 않음. 사용자에게는 예상 분모가 미확정인 경우 비율을 숨김.
- 표준편차는 Welford 또는 충분한 정밀도의 합/제곱합 방식, 대량 누적 때 부동소수 안정성 검증. 서로 다른 물리 단위의 수치 평균 금지.
- 1시간 집계는 해당 1분 집계의 `sum/count`를 가중합하고 전체 `min/max`를 보존. 분당 평균을 단순 평균해 표본 가중치를 잃지 않음. 합산 시 `aggregationVersion`이 다르면 재집계 또는 오류 표시.
- 시각 누락·순서 역전·중복 `observationId`·품질 전이·모드 전환을 명시적으로 기록. 누락 구간은 **그래프 선을 끊어 표시**; 시각적 보간을 적용하면 반드시 보간으로 표기(기본 꺼짐).
- 동일 시각의 재전송/중복 표본은 원시 저장 단계에서 고유키/버전으로 처리하고, 지표의 sampleCount가 이중 증가하지 않도록 테스트.

### 3.3 조회 범위와 그래프 해상도

| 조회 기간 | 기본 응답 원천 | 차트 표시 | 최대값 및 실패 |
|---|---|---|---|
| 실시간 5분 / 1시간 | raw(권한/보존 안에서) | 원시+EWMA+경고 마커 | 시리즈당 최대 1,500점; 초과 시 명시적 축약 |
| 24시간 | 자동 `1m` 집계 | min/max 폭(peak 보존), avg | raw 요구시 페이지/조회량 제한 |
| 7일 / 30일 / 90일 | `1h` (가용할 때), 없으면 `1m` | 장기 변화, 이벤트 오버레이 | 범위 밖은 `RETENTION_EXPIRED` |
| 경고 상세 | 경고 전후 지정 창의 raw·ML 평가 | 경고 시각·모델 임계값·ACK 시각 | snapshot 없으면 `DATA_GAP` 표시 |

**구현 조건:** 백엔드가 시간범위·interval·series·limit를 검증한다. `resolution=auto`가 응답 해상도와 데이터 출처를 알려준다. 다운샘플링으로 급격한 이상이 묻히지 않도록 `min/max`를 보존한다. 파생 시계열은 사용한 입력 신호/기준선 버전을 결과에 포함한다.

## 4. 로그 이벤트 상세와 인과 추적

### 4.1 필수 공통 메타데이터

기존 이벤트 필수 8필드 `schemaVersion,eventId,occurredAtUtc,component,eventName,severity,traceId,outcome`를 유지한다. 가능한 경우 `deviceId,warningId,incidentId,requestId,runId,modelVersion,reasonCode,durationMs,sourceVersion`을 추가한다. Trend 관련 이벤트는 `aggregationVersion`, `observationGapCount`, `queryResolution`, `dataWindowStartUtc`, `dataWindowEndUtc`, `configRevision`, `droppedCount`, `storageBytes`처럼 **허용 목록**에서만 추가한다.

`traceId`는 WPF/API 단일 요청의 흐름을, 영속 `warningId`는 ML 경고 발생→운영자 ACK→재부팅 이후 조회를 연결한다. 프로세스 간 시각 순서가 항상 보장되지는 않으므로 `sourceSequence`, `stateVersion`, 수신시각을 함께 확인한다. 타임스탬프 지연값은 wall-clock 차이만으로 무조건 성능 지표화하지 않는다.

### 4.2 이벤트 분류 + 발행 시점

| 분류 | 신규/기존 Event ID | 트리거 · 분석 가치 |
|---|---|---|
| Collector | `POLL_TIMEOUT`, `SIGNAL_QUALITY_CHANGED`, `COLLECTOR_RESTARTED`, `OBSERVATION_GAP_DETECTED` | 요청 타임아웃·연속 관측 누락·복구 시작/끝, 누락 신호수 |
| Storage | `STORAGE_WRITE_FAILED`, `AGGREGATION_STARTED`, `AGGREGATION_COMPLETED`, `AGGREGATION_FAILED`, `RETENTION_CLEANUP_COMPLETED` | 버킷 재집계·삭제 건수·실패 사유, 일괄 쓰기 품질 |
| ML | `ML_MODEL_LOADED`, `ML_EVALUATION_UNAVAILABLE`, `EWMA_WARNING_RAISED`, `ML_DEGRADED`, `ML_THRESHOLD_CHANGED`, `ML_DRIFT_DETECTED` | 모델 버전·경계값 변경·품질 문제·경고 발생 |
| Trend/API | `TREND_QUERY_FAILED`, `TREND_QUERY_TRUNCATED`, `API_REQUEST_FAILED` | 과도한 조회, DB 지연, 응답 포인트 축소 |
| Windows | `SERVICE_STARTED`, `SERVICE_STOPPED`, `SERVICE_RECOVERED`, `UI_BACKEND_DISCONNECTED`, `UI_BACKEND_RECONNECTED` | 재부팅·서비스 재시작·연결 품질 |
| Audit | `WARNING_ACKNOWLEDGED`, `ACK_REJECTED`, `CONFIG_CHANGED`, `SUPPORT_BUNDLE_EXPORTED` | 권한·행위자 pseudonym·이유·성공/거부 |
| Logging | `LOGGING_DEGRADED`, `LOG_DROPPED_COUNT` | 기록 자체의 불완전함·용량 제한 |

INFO는 상태변경·핵심 집계 완료 요약에만 사용. 매 폴링, 매 ML 평가, 매 화면 refresh를 INFO 한 줄씩 쓰지 않는다. 주기적 통계는 bounded Metrics를 사용하고, WARN/ERROR는 원인 코드를 포함한다. 동일 장애 로그는 rate limit / suppression과 누락 건수 요약을 동반한다.

### 4.3 경고 하나의 추적 예

```text
10:30:00.000 observation: setpoint=10.0, actual=9.8, quality=GOOD
10:31:00.000 derived: error=0.5, EWMA 상승 [model=EWMA-1]
10:32:30.000 EWMA_WARNING_RAISED warningId=warn-042 evidence=obs-...
10:35:10.000 WARNING_ACKNOWLEDGED warningId=warn-042 actor=operator-17
10:36:00.000 trend: 1m aggregate verified; warning snapshot referent preserved
```

이는 **예시 값**이다. ACK는 권고 확인이며 PLC/설비 제어가 아니다. 경고 전후 추세의 '언제부터'는 기준선과 센서 시간 정확도 한계 내에서만 설명한다.

## 5. 운영 지표/Trend (서비스 유지보수)

| 지표 코드 | 집계 방법 · 차트 | 경고 조건(초기 가설) |
|---|---|---|
| `collector_poll_latency_ms` | 최근 5분 p50/p95, 1h 추이 | 설정 주기의 80% 초과 지속 시 주의 |
| `collector_receive_delay_ms` | observed→received 차이의 분포; 시계 불일치 분리 | timestamp 품질 미확인 시 데이터 오류 |
| `collector_missing_ratio` | 유효표본/예상표본 기준, mode별 | 3연속 창 10% 초과 시 ML DEGRADED 가설 |
| `device_disconnect_count` | 1h/24h 연결 끊김 회수 | 급증 시 원인 조사 |
| `ml_inference_latency_ms` | 평가당 p50/p95; model별 | 고객 PC에서 실측 후 고정 |
| `ml_normal_reference_percentile` | 정상 백분위의 시간 추세, 임계선 | 참조 버전 변경 표시·점수 비교 금지 |
| `db_write_duration_ms` | batch commit p95 및 실패건수 | 지속 지연/초과 시 스로틀 알림 |
| `db_storage_bytes`, `db_wal_bytes` | 디스크 증가 1h/24h/7d | 예산 상한/체크포인트 점검 |
| `log_dropped_total` | 낮은 우선순위 이벤트 누락 누계 | >0이면 로그 완전성 경고 |
| `api_error_count` | reasonCode별 1h/24h | 인증/권한/내부 오류 분리 |
| `warning_ack_delay_s` | warning 발생→최초 ACK 시차 | 운영 KPI. **고장 예방 실적**이 아님 |

운영 지표는 장비 신호가 아니라 **프로그램 자체의 건강 상태**를 알려준다. 성능 목표는 D1 측정 환경과 함께 동결하며 가상 시험에서 좋은 숫자가 나왔다고 모든 고객 PC 성능을 보증하지 않는다.

## 6. WPF 화면 상세 명세

### W1 실시간 Overview

상단 상태바: 현재 소스 `SIMULATOR/READ_ONLY`, 수집 서비스·DB·ML·진단 로그의 `OK/DEGRADED/OFFLINE`, 마지막 정상 수집 시각 및 테스트 데이터 표시. 본문: 목표/실제 유량 동시선, 유량오차/EWMA, 품질구간 음영, 조기경고 마커. 카드: 최신 이상도(정상 참조 백분위), 최근 1h 데이터 수신율, OPEN 경고 수. 버튼: `5m / 1h / 24h`, 경고 상세 보기. **D-demo는 1h trend의 소규모 버전만 구현**.

### W2 Device Trend Explorer (D1)

신호 선택(동일 단위 쌍 또는 별도 축), 기간(5m·1h·24h·7d·30d·90d), 그래프 zoom·pan·crosshair, 시간구간 선택, 모델·운전 모드 변경 오버레이, 경고 발생/ACK 수직마커, 품질 전이/결측 shading. 그래프 하단 데이터표: 시각·값·품질·수집시각, csv(허용 데이터에 한해) 내보내기. 시리즈당 최대 1,500점, 현재 해상도와 불완전한 시계열 여부 표기. `min/max` spike envelope, 집계 구간 수치·표본수 확인.

### W3 Warning Root Cause View (D-demo에서 최소 패널, D2 이후 확장)

warningId 클릭 → 발생 전후 Trend, EWMA 임계선과 초과 시점, 영향을 받은 신호, modelVersion·configRevision·referenceDataVersion, missing ratio, 연결 품질, 이벤트 타임라인, 조치 권고/ACK 상태. **'왜 이런 경고가 떴는지'를 고정 규칙·모델 증거로 설명**; 모르는 것은 `INSUFFICIENT_DATA`. 모델 출력만으로 센서 고장의 원인을 단정하지 않음.

### W4 Event / Log Explorer (D3)

필터: deviceId, warningId, traceId, 기간, eventName, severity, outcome, component, reasonCode. 한 줄 클릭 시 구조화 상세(JSON safeContext allowlist), 연관 Trend와 경고로 이동. 서버 페이지네이션, 총 건수의 추정/확정 구분, 권한 별 조회 제한. 대량 로그를 화면으로 전부 로딩하지 않음.

### W5 Health & Support (D4)

CPU·메모리·Collector p95·DB 용량·WAL 크기·누락률·재접속 횟수·로그 drop Trend. 지원용 진단 번들은 범위/포함정보 미리보기 → **고객 별도 동의** → 민감정보 검사 → 암호화 가능한 내보내기 → 감사 기록. 외부 전송 기본 OFF.

## 7. API 계약: 서버가 데이터 양과 범위 통제

기존 thin-demo API는 유지. **D-demo에서 경고 요약 Trend 2개 GET을 신규 포함**, 나머지 고급 검색·집계·지원 API는 D1 이후. 공통 성공 응답은 `schemaVersion`, `source`, `resolution`, `window`, `completeness`/`gaps`, `dataQuality`, `items`, `nextCursor?`, `generatedAtUtc` 포함(해당 endpoint에서 유효한 필드만).

| Method / 경로 | 단계 | 주요 요청 | 주요 응답/제한 |
|---|---|---|---|
| `GET /v1/devices/{id}/trends` | D-demo | `signals`, `fromUtc`, `toUtc`, `resolution=auto/raw/1m/1h`, `maxPoints<=1500` | 시계열·원천 해상도·gap·조회시각·truncate 정보; timeout/기간 위반 422 |
| `GET /v1/warnings/{id}/timeline` | D-demo | warningId | 알람·ML 평가·ACK 이벤트와 관련 observation 범위; 404/권한 검사 |
| `GET /v1/devices/{id}/trend-summary` | D1 | 기간·그룹화 | min/max/avg/valid/missing 및 품질 비율 |
| `GET /v1/events` | D3 | warningId/traceId/severity/date/page | 서버 페이징; 광범위 조회 422 |
| `GET /v1/health/metrics` | D1 | period=1h/24h | 집계 운영 지표, 최근 시각, 상태 |
| `POST /v1/support/bundles` | D4 | 명시 동의, 기간, 포함 항목 | 요청 식별자, manifest, 다운로드 권한; 자료 사전 미리보기 별도 단계 |

**보안:** 운영/감사 로그 API는 인증·권한 확인 필수. Trace/warning ID를 안다고 다른 고객의 내역을 볼 수 없도록 소유 범위와 설치 단위 격리를 검증. `maxPoints`와 기간 상한을 백엔드에서 적용, UI 검증만으로 충분하다고 주장 금지.

**응답 예시 — 합성 fixture**:

```json
{
  "schemaVersion":"3.3.0", "deviceId":"EQ-SUPPLY-01", "source":"SIMULATOR",
  "resolution":"raw", "window":{"fromUtc":"2026-10-16T00:00:00Z","toUtc":"2026-10-16T00:05:00Z"},
  "generatedAtUtc":"2026-10-16T00:05:02Z", "isComplete":false,
  "gaps":[{"fromUtc":"2026-10-16T00:02:20Z","toUtc":"2026-10-16T00:02:25Z","reason":"SIGNAL_MISSING"}],
  "series":[{"signalKey":"actualFlowLpm","unit":"L/min","points":[{"t":"2026-10-16T00:00:00Z","v":9.8,"quality":"GOOD"}]}],
  "warningMarkers":[{"warningId":"warn-demo-02","atUtc":"2026-10-16T00:04:30Z","type":"EARLY_WARNING"}]
}
```

## 8. 저장·성능·신뢰성 계약

- SQLite WAL은 다수 리더와 단일 writer가 함께 운영되므로 **배치 쓰기/인덱스/집계 워커**의 부하를 측정한다. WPF 쿼리가 수집 writer를 장시간 방해하면 실패. 장기 처리/아카이브는 백그라운드 저우선 큐로 분리.
- 조회 범위 상한: 데모 `1h / 최대 2 signal / 1,500점`, D1 `7~90일 요약`에 대해 `maxPoints=1500`·응답 크기/시간 제한. 장시간 조회는 확대하기 전에 Windows PC 사양별 반복 측정.
- 디스크 상태: 임계수준에서 DEBUG 중지 → 정책 허용 raw 청소 → 재집계 가능한 데이터 보전 → 감사/기록 실패는 UI `DEGRADED`, 결측 구간을 명시. **조용한 데이터 삭제 금지**. DB 용량과 WAL 증가를 운영 지표로 기록.
- JSONL은 `LoggerMessage` 기반 .NET 로그 또는 표준 ILogger와 호환; Python은 structured JSON logger. W3C TraceContext/OpenTelemetry는 표준 필드를 **선택적으로 재사용**하되 첫 데모에 OTEL Collector·Prometheus·Grafana 서버 설치는 강제하지 않음.
- 원격 API/클라우드 없이 현장 기능 유지. 외부 라이브 연결은 P1 권한·부하 승인 이후, 타인 소유 소스와 고객 정보 사용 금지.
- 원시 스냅샷/지원 번들을 저장할 때 암호화와 사용권한을 적용. 시간·용량 보존 정책과 로그 내보내기 동의는 고객별 설정과 적용 법규 검토 후 결정.

## 9. 테스트 시나리오 — 독립 판정값 필수

| ID | 입력·실패 주입 | 통과 기준 |
|---|---|---|
| `TRD-01` | DEG-02 유량 편차 점진적 증가 | 목표/실제/오차/EWMA 같은 시간축, 경고 마커 존재 |
| `TRD-02` | 5초 신호 누락/STALE | null gap·shading·missingCount 증가; 0 보간/정상 간주 0건 |
| `TRD-03` | 동일 observationId 중복·순서 역전 | raw·aggregate count 이중 반영 0건, 품질 설명 유지 |
| `TRD-04` | 1h 데이터를 1m 요약 후 재조회 | 가중 평균·min/max·validCount 독립 오라클과 일치 |
| `TRD-05` | 시간축 5m/1h/24h/7d·다중 신호 | 자동 해상도 전환, maxPoints≤1500, peak 보존·출처 공개 |
| `TRD-06` | 경고 후 ACK, UI/서비스 재시작 | warningId에 modelVersion, Trend, ACK 한 건 연결 |
| `TRD-07` | DEBUG 폭주·DB disk-full | bounded queue, drop 경고, 운영 상태 DEGRADED, 거짓 완료 0건 |
| `TRD-08` | 로그에 token·raw prompt·개인 경로 주입 | JSONL/DB/export 문자열에 유출 0건 |
| `TRD-09` | 경고 타이밍 전후 raw retention 만료 | 실존 snapshot만 표시, 만료/불완전 근거는 UNCERTAIN |
| `TRD-10` | 8signals×1Hz 1h 및 DB reader 동시 실행 | 데이터 누락·DB 크기·WAL·p95 latency 실측 기록 |
| `TRD-11` | 다른 mode의 EWMA 데이터 선택 | NO_MODEL/INSUFFICIENT_DATA, 잘못된 모드 기준 경고 0건 |
| `TRD-12` | Audit DB 오류 + 동시 ACK | ACK false success 0건, 동일 request idempotent 원자 트랜잭션 |
| `TRD-13` | 페이지 API 과다 범위 요청·타 설치 ID 접근 | 422/403 또는 404, 내부 대용량 조회·데이터 유출 없음 |
| `TRD-14` | 고객 지원 번들 동의하지 않음 | 외부 전송 0건, 저장 원본 DB 미포함, AUDIT 기록 적절 |

독립 평가 오라클은 구현 전에 고정한다. 합성 신호에서 경고를 성공적으로 표시한 것은 **파이프라인 기능 시험 결과**이지 현장 예지율은 아니다.

## 10. 단계별 Gate (데모 속도 유지)

| Gate | 구현할 로그/Trend 범위 | 검증할 결과 |
|---|---|---|
| `D0a` | 공통 event/observation/metric YAML, JSONL 최소 writer, trace/warningId | 스키마·Redaction 테스트, 계약 drift PASS |
| `D0b` | DEG-02 raw 8신호, 유량오차/EWMA 파생, `EWMA_WARNING_RAISED` | TRD-01/02 fixture와 경고 생성 |
| `D-demo` | WPF Overview 단일 Trend + Warning 패널, ACK timeline | TRD-01,06,12; 누락/미확인 표시; 시연 가능 |
| `D1` | 1분 집계·원시 보존·서비스·운영 지표·24h Trend | TRD-03~05,07,10; 디스크·WAL 실측 |
| `D2` | ML 모델 비교·드리프트·model provenance·mode별 Trend | TRD-09,11; 합성/외부/현장 증거 구분 |
| `D3` | 사건·추적 로그 탐색기, 고급 필터/검색 | TRD-08,13; 연관 Warning↔Trend 드릴다운 |
| `D4` | 고객용 진단 번들·설치/업데이트 복원 | TRD-14; 비밀정보/동의/감사/보존 시험 |

**D0a 전에 법적·IP 권리 게이트를 반드시 별도로 점검한다.** '판매 가능'은 현장 검증·지원·계약 후의 별도 판단. Astra는 각 게이트의 명시 범위만 구현하고 자동으로 다음 게이트에 진입하지 않는다.

## 11. 실제 고객 유지보수 조사 시나리오

1. 고객이 “10월 16일 오전 경고가 뜨고 유량이 떨어졌다”고 문의.
2. 고객이 로그에서 warningId를 검색하고 해당 시점 ±10분 Trend를 연다.
3. 목표/실제 유량 비교, EWMA 상승, 품질 누락, 밸브·압력·모터 전류 동시 변화를 **증거와 불확실성**을 표시하며 비교.
4. 이벤트 타임라인에서 collector timeout/모델 버전/설정 변경/사용자 ACK를 구분.
5. 검증 완료 데이터만 설명에 사용하며 누락 구간은 '확인 불가'로 표시.
6. 사용자가 확인하고 민감정보 제거 후 선택된 기간의 진단 번들을 작성; 고객 동의 없는 원격 업로드 금지.
7. 지원 엔지니어는 고객 장비 명령 없이 원인 후보와 추가 점검을 안내하고, 원인 미확정은 그대로 보고.

## 12. 참고 프로젝트·공식 문서 및 채택/제외 결정

- **OpenTelemetry (docs):** 로그/메트릭/트레이스 공통 어휘와 식별자·context 전파를 참고. https://opentelemetry.io/docs/specs/semconv/general/ . **데모 제외:** 별도 OTEL Collector 서버·클라우드 저장소 상시 운영.
- **Microsoft .NET ILogger LoggerMessage:** 구조화된 고성능 로그 메서드. https://learn.microsoft.com/en-us/dotnet/core/extensions/high-performance-logging . **선택:** 운영 과부하 영역에서 직접 profiler로 검증 후 도입.
- **SQLite WAL:** 읽기·쓰기 동시성 및 단일 writer 제약, checkpoint. https://www.sqlite.org/wal.html . **선택:** 초기 온프레미스 제품; 실제 병목 확인 전 TimeSeries DB/분산 로깅 시스템 도입 금지.
- **기존 v3.2 스펙:** correlation, 개인정보·감사, 진단 번들의 요구사항을 보존한다. 새로운 내용을 추가할 때는 YAML 계약·OpenAPI·Astra 지시문을 함께 수정한다.

**제외:** 실시간 모든 샘플 INFO 로깅, 차트 이미지 파일 무한 저장, 회사/고객 정보의 외부 AI 자동 전송, 경고 결과를 고장 확률/잔여 수명처럼 표시, 자동 장비 쓰기 조치, 무제한 로그 검색.
