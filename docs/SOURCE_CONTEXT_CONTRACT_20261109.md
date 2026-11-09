# 출처와 수집 방식: 호환되는 첫 단계

기준은 원격 `improve/storage-failure-integrity-20261109`의
`856f7f7f1d55a034185f71d73d6df5f48803b98a`입니다. 독립 개선 이력이며 접근할 수 없는
이전 보존본이나 `5075ccd`를 복구·대체했다는 뜻이 아닙니다. UI·기존 원본·그래픽을
변경하지 않습니다.

## 이번에 제공하는 정보

기존 인증 헤더를 포함해 `GET /v1/health?includeSourceContext=true`를 요청하면
`sourceContext`가 추가됩니다. 이 query는 선택 boolean이며 기본값은 false입니다.
생략/false일 때는 기존 health 키와 값을 유지하고 추가 null 필드도 보내지 않습니다.
엄격한 C# 역직렬화기는 알 수 없는 필드를 거부하므로 새 정보를 이해하는 caller만 opt-in해야 합니다.
현재 Desktop/Gateway와 화면은 opt-in하지 않습니다. runtime OpenAPI에는 선택 query가 나타나며,
기존 canonical YAML·생성 모델·route 집합은 변경하지 않았습니다. 아래는 그 보조 계약입니다.

| 필드 | 값과 의미 |
| --- | --- |
| schemaVersion | `source-context-1`; 이 보조 선언의 버전 |
| scope | `CURRENT_CONFIGURATION`; 현재 수집 설정에 관한 선언 |
| archivedRowProvenance | `NOT_INFERRED`; 과거 저장 행/개별 관측의 출처를 역추정하지 않음 |
| realEquipmentSupported | JSON `false`; 이번 수집 경로는 실제 장비 입력을 지원하지 않음 |
| hardRealtimeGuaranteed | JSON `false`; hard realtime 보장 없음 |
| sources | 설정된 device별 선언 목록. 혼합 방식을 하나의 전역 값으로 압축하지 않음 |

각 sources 항목은 deviceId와 아래 고정 vocabulary만 포함합니다. 설정 URL, 토큰 값·환경변수명,
equipment/module 식별자, 파일 경로, 원시 값과 원본/수신 시각·순번을 포함하지 않습니다.

| 필드 | 고정 fixture | snapshot simulator | replay simulator |
| --- | --- | --- | --- |
| declaredOrigin | SYNTHETIC | SYNTHETIC | SYNTHETIC |
| producerKind | FIXTURE | SIMULATOR | SIMULATOR |
| acquisitionMethod | FIXED_FIXTURE | SNAPSHOT_POLL | SOURCE_BUFFER_REPLAY |
| sourceAccessMode | NOT_APPLICABLE | READ_ONLY | READ_ONLY |
| originEvidence | BUILT_IN_FIXTURE | CONTRACT_DECLARED | CONTRACT_DECLARED |
| sourceTimestampPolicy | FIXTURE_DEFINED | PRODUCER_REQUIRED | PRODUCER_REQUIRED |
| sourceSequencePolicy | NOT_EXPOSED | PRODUCER_REQUIRED | PRODUCER_REQUIRED |

`READ_ONLY`는 외부 소스 조회 방식입니다. 로컬 SQLite 저장은 계속 씁니다. Snapshot 조회도
hard realtime을 보장하지 않으며, 버퍼 재생에 방금 생성된 값이 들어 있어도 수집 방식은 재생입니다.
신선도·연결 성공·시각 정확도와 출처/방식은 서로 다른 축입니다. 선언은 연결 실패 중에도 같고,
실제 장비 신원 인증이나 개별 payload 검증 결과를 대신하지 않습니다. 원격 endpoint가
SIMULATOR를 사칭하는지 하드웨어로 확인하는 기능은 없습니다.

현재 active collector protocol에 대한 선언입니다. 저장소에 별도로 포함된 외부 실제 시험대
데이터셋까지 합성이라고 분류하지 않습니다. 기존 `mock`·`mode`는 호환을 위해 보존했습니다.

## 원본 보전과 입력 한계

현재 snapshot1.0/raw1.1은 `source=SIMULATOR`, `observedAtUtc`, `runId`, `sequence`를
필수로 요구합니다. `REAL`, `READ_ONLY` 출처나 누락 원본 필드는 기존대로 거부합니다.
Source enum에 남은 READ_ONLY는 실장비 출처로 해석하지 않습니다. 받은 시간이 원본 시간을
대체하지 않고 로컬 observation ID가 원본 순번을 대체하지 않습니다. fixture의 원본 순번은
관측 계약에 노출되지 않으므로 NOT_EXPOSED입니다.

이번 제품 변경은 `api.py`의 metadata 생성과 선택 query뿐입니다. SourceConfig·입력/관측·IPC,
RawArchive/schema/key/cursor, 수집/감시/시작 복구, simulator, C#과 UI는 그대로입니다.
snapshot v1.0은 기존 여덟 신호 관측이며 raw 저장을 제공한다고 주장하지 않습니다.
replay v1.1은 여덟/512 tag 원본 저장·중복 처리·재개를 회귀 검증했습니다.

## 후속 계약 이행 순서

실제/합성 출처와 실시간/재생은 독립 조합이어야 하지만, 실제 장비·nullable metadata를
현재 모델에 곧바로 허용하면 DB 기본 키와 커서의 의미까지 바뀝니다. 다음 순서로 분리합니다.

1. **현재 단계:** versioned opt-in 선언, 기본 strict consumer 보전, 기존 입력 거부/저장 경로
   회귀 시험. 실제 장비 입력이나 과거 행 provenance를 제공하지 않습니다.
2. **새 입력 envelope 설계:** adapter별 origin 선언/검증 근거, acquisition method와
   source access mode를 분리합니다. 제공되지 않은 sourceOriginalTime/sourceSequence는
   nullable + presence/status로 표현합니다. source time과 collector receivedAt을 별도 저장하고,
   collector arrival ID/local admission sequence는 로컬 값임을 명시합니다. 누락값을 receipt나
   임의 counter로 채워 원본처럼 보내지 않습니다. 원본 timezone·clock 오류도 별도로 표현합니다.
3. **archive v2 설계·비파괴 이행:** 현 v1 기본 키 `(device,run,sequence)`와 cursor를 보존합니다.
   새 adapter의 opaque resume token 또는 명시적 local admission ID와 원본 순번을 분리합니다.
   기존 DB 백업/읽기 검증, side-by-side v2·rollback·부분 이행 검증을 먼저 정합니다.
   기존 행에 당시 기록하지 않은 수집 방식·출처 근거는 UNKNOWN으로 남기며 현재 설정으로
   역도장하지 않습니다. payload의 SIMULATOR 선언이 남아 있으면 그 근거 수준 그대로 취급합니다.
   중복 충돌/첫 receipt/원본 payload/커서를 보전하고 자동 삭제·덮어쓰기 이행은 하지 않습니다.
4. **IPC/API·소비자 협상:** worker frame, Python·C# 생성 모델, Desktop/Gateway를
   버전별로 함께 검토합니다. 구버전 기본 mode/응답은 유지하고 새 envelope 지원을 협상합니다.
   알 수 없는 origin/method/version을 실제·실시간·완료로 가정하지 않습니다. UI 표시는 별도 승인 후
   적용하며, 지원하지 않는 버전·실제 adapter는 fail closed합니다.
5. **검증 후 adapter 연결:** 아래 matrix를 먼저 통과시키고, 실제 장비·Windows에서 별도
   실증합니다. 현재 단계는 새 장비 연결을 활성화하지 않습니다.

후속 검증 matrix에는 실제/합성 × snapshot/stream/replay 조합, 혼합 device, 누락·중복·역행 순번,
원본 시각 없음/시간대/잘못된 clock, 원본과 receipt 분리, origin 불일치, 이전/새 envelope,
기존 archive 읽기·중복·부분 복구·이행 중단·rollback, worker 재시작·저장 실패·커서 재개가 필요합니다.
현재 시험은 이 미래 matrix가 완료됐다는 증거가 아닙니다.

## 검증과 남은 범위

정확한 실행 수·지문은 [기계 판독 evidence](evidence/source-context-contract-20261109.json)에 기록합니다.
전체 Python은 **391통과·Windows 전용1건너뜀**(392개,130.951초), 독립 emulator는 **26통과**입니다.
선택 API/계약/신규 회귀117개와 생성 계약/v3.4 검사도 통과했습니다. Windows 전달 skip과
기존 Starlette TestClient/httpx 경고1개는 그대로 남습니다.
독립 읽기 전용 검토에서 Critical/Important/Minor0건이며, 검토자는 전체 시험 기록과42개
Python 지문을 대조했고 시험을 별도 재실행하지 않았습니다. 보호 대상373개 기존 파일은 byte 동일합니다.
새 opt-in 시험은 미구현 상태에서 10개 중6실패/4대조 통과 후 구현으로 통과했습니다.
16개 원본 보전 시험은 기존 동작의 대조군으로 기준 코드에서도 통과했습니다.

- 기본/false health의 정확 키·값, optional bool/OpenAPI·잘못된 bool422, 인증/Host/Origin 경계.
- fixture·혼합 snapshot/replay·일반/ProcessCollector의 선언, secret 배제, metadata 요청의 수집 미시작.
- 방금 받은 합성 값도 실제 장비나 hard realtime으로 승격하지 않음.
- snapshot/replay의 REAL·READ_ONLY·필수 필드 누락 거부, replay page 전체 선검증,
  이전 원본/첫 receipt/cursor/통계 보전.
- 여덟/512 tag·중복·SQLite 재열기, 이틀 전 replay의 원본 시각과 첫 receipt 분리,
  오래된 자료가 STALE로 남음.

ASGI/MockTransport와 실제 SQLite를 사용한 Linux 합성 시험입니다. full suite에는 기존
Linux 프로세스·시작/저장 장애 시험도 포함하지만, 새 metadata ProcessCollector 시험 자체는
수집 시작/IPC 통합 시험이 아닙니다. 독립 emulator 시험과 생성 계약/v3.4 검사도 실행합니다.
실제 장비·고객 데이터·Windows 서비스·PowerShell7 Windows 전달 사례·.NET/WPF 실행,
하드웨어 출처 인증·개별 행 provenance·대량 장기 실증은 이번에 수행하지 않았습니다.

Ruling: opt-in은 strict 구버전을 보호하지만 새 구분을 기존 화면에 자동 표시하지 않습니다.
Ruling: SYNTHETIC/CONTRACT_DECLARED는 현재 지원 protocol의 선언입니다. 실제 장비와
nullable 원본 필드 지원은 별도 버전/저장 이행을 거쳐야 합니다.
