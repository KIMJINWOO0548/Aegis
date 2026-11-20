# 보존된 숫자 태그의 관찰 사전과 과거 조회

기준 원격은 `improve/source-context-contract-20261109`, commit은
`7d303ee27d9ec31cc33d2c1ec69cbc8b036ac867`입니다. 새 backend 기능은 이 접근 가능한
개선 이력에 추가했습니다. 이전 접근 제한 보존본을 복구·대체하거나 `5075ccd`와 동일한
이력을 만들었다고 주장하지 않습니다. UI 디자인·그래픽·simulator·원본을 변경하지 않습니다.

## 선택 이유와 기존 계약

기존 `/observations`는 여덟 유량 신호의 최신 상태이고, `/trends`는 고정 fixture 유량 계약이며
현재 collector 모드에는501입니다. 이를 범용 원본 API로 해석하거나 확장하지 않았습니다.
별도 `raw-query-1` 응답으로 SQLite에 보존된 raw1.1의 관찰 태그 목록/선택 tag projection을
제공합니다. RawArchive writer·schema·cursor·중복/복구·worker/IPC·SourceConfig는 그대로입니다.
기존 기본/false health와 선택 sourceContext도 유지했습니다. canonical thin-demo YAML·생성
Python/C#·native consumer는 그대로이고, 새 runtime 확장은 별도 OpenAPI query 시험을 갖습니다.

## 호출과 연결

기존 loopback Host·API token·Origin 금지 경계를 그대로 적용합니다. URL은 다음 두 가지입니다.

| 경로 | 필수 query | 응답 kind |
| --- | --- | --- |
| `/v1/devices/{id}/raw-tags` | runId, fromUtc, toUtc | RAW_ARCHIVE_OBSERVED_TAGS |
| `/v1/devices/{id}/raw-history` | 위 필드 + signals | RAW_ARCHIVE_HISTORY |

`runId`는 조회할 archive의 정확한 UUID 문자열 키입니다. 대소문자 표기를 바꾸지 않습니다.
현재 run은 기존 ingestion의 lastCommittedRunId로 확인할 수 있습니다. 과거 run 목록 탐색은
추가하지 않았으므로 과거 run의 키는 호출자가 알고 있어야 합니다. `signals`는 쉼표 구분의
서로 다른1..16개 raw signalKey이며 유량 여덟 enum에 한정되지 않습니다. 두 경로 모두
선택 query `maxBatches`는 기본64, 허용1..64입니다. fromUtc/toUtc는 timezone이 있는 ISO8601,
최대24시간, **시작 포함·끝 제외 [from,to)** 입니다. 기존 Python UTC datetime의 마이크로초
정밀도로 비교하며 sub-microsecond 정밀도를 보장하지 않습니다. offset은 UTC로 비교하되 원본 문자열은
응답 batch에서 유지합니다. 정렬은 source observed time, 동률은 sequence 순입니다.

일반 실행은 기존 CLI `--archive`(기본 replay 경로), 분리 실행은 기존 모듈별 archive_paths를
reader에 연결합니다. replay source만 연결하고 snapshot에는 연결하지 않습니다. embedding은
`create_app(..., collector=collector, raw_archive_paths={device_id: trusted_path})`를 명시해야 합니다.
mapping은 복사하며 알 수 없는/snapshot device 키를 거부합니다. HTTP caller는 파일 경로를
선택할 수 없습니다. fixture·snapshot·미연결 embedding은501이고, raw archive가 없다고
합성 fixture나 정상 empty로 대체하지 않습니다.

## 응답의 의미

공통 schemaVersion은 `raw-query-1`입니다. deviceId/runId/window, scannedBatches,
matchedBatches, 적용 limits, tags와 run sequenceGaps가 포함됩니다.
`selectionComplete=true`는 제한 안에서 **저장된 선택 범위**를 빠짐없이 조회했다는 뜻입니다.
`sourceCoverage=NOT_ASSERTED`이므로 미수집·원천 유실·모든 원본 시계열의 완전성을 보장하지 않습니다.
한도 초과/손상은 성공 prefix나 잘린 성공으로 반환하지 않습니다.

`raw-tags`의 tags는 해당 기간에 관찰한 key, `units` variants, presentBatches/absentBatches,
`qualities`의 원래 품질별 빈도입니다. 장비의 권위 tag 사전, 설명/engineering 의미, 전체 과거 목록,
최신값이나 하드웨어 인증을 만들지 않습니다. 동일 key의 단위가 달라도 한 값으로 덮지 않습니다.

`raw-history`는 `batches`를 추가합니다. 각 batch는 저장된 protocolVersion/source/deviceId/runId,
sequence/observedAtUtc/deviceStateVersion/modeId, **최초** receivedAtUtc와 당시 equipmentId/moduleId를
유지합니다. 현재 설정의 모듈 정보를 과거 행에 덧씌우지 않습니다. 모든 tag 원문 다운로드가 아닌
요청 tag projection입니다. JSON 값·문자열을 검증 후 원래 dict에서 꺼내어 원본 정수를 float로
변환하지 않습니다. JSON 텍스트의 공백/숫자 표기 자체를 byte 복제하는 기능은 아닙니다.

signals의 항목은 `{signalKey, present, reading}`입니다. 태그가 batch에 없으면
`present=false, reading=null`; 실제 저장된 태그는 `present=true`와 원래 `{value,unit,quality}`입니다.
따라서 원본 `quality=MISSING, value=null`, `BAD/null`과 태그 부재를 구분합니다. null 보간,
최근값 대입, 품질 수정이나 수치 요약을 하지 않습니다. source observed time과 local receipt를
섞거나 미제공 원본 시각/순번을 만들지 않습니다.

sequenceGaps는 `{scope: SELECTED_RUN_SEQUENCE_ONLY, items: [...]}`이며 저장된
firstSequence/lastSequence/reason만 제공합니다. 조회 시간 창과 별개인 **선택 run 전체의 순번
메타데이터**입니다. sequence gap을 임의 시간 gap으로 바꾸거나 알려진 gap이 없다고 실제 유실이
없다고 결론 내리지 않습니다. 원본 자체의 source=SIMULATOR 선언을 유지하며 실장비로 승격하지 않습니다.

## 자원 한도와 오류

| 대상 | 한도 |
| --- | --- |
| device+run의 전체 후보 | 1,024 batch |
| 기간에 들어오는 batch | maxBatches, 최대64 |
| 요청 tag | 16 |
| 기간 관찰 tag catalog | 512 key |
| 후보 payload 하나/합계 | 256KiB / 8MiB |
| 선택 run의 저장 gap | 1,024 |
| SQL progress budget | 1,000,000 VM step / 1초 |
| SQLite busy timeout | 0.2초 |

시간 인덱스가 없어 run의 후보 전체를 제한한 뒤 정확한 시간 비교를 합니다. run이1,024행 또는
8MiB를 넘으면 기간을 좁혀도413입니다. 대량 시간 seek/index·비파괴 DB 이행·pagination은 후속입니다.
SQL progress와 Python 단계 사이 시간 검사는 hard realtime 지연 보장이 아닙니다. 물리 I/O와
운영체제 지연·동시 다중 요청 성능을 실증하지 않았습니다. raw1.1 및 schema v1을 검증하고,
source/receipt 시각64자, 저장 module/equipment metadata128자 범위도 확인합니다.

| 상태/code | 의미 |
| --- | --- |
| 422 INVALID_QUERY | 필수 선택·기간·tag·run·행 제한 입력 오류 |
| 413 RAW_QUERY_LIMIT | 후보/결과/bytes/catalog/gap/SQL budget 초과; 부분 성공 없음 |
| 409 RAW_ARCHIVE_INVALID | schema/고유 gap 계약 또는 후보 payload·identity·시각 검증 실패 |
| 503 RAW_ARCHIVE_UNAVAILABLE | 미생성·잠금·SQLite/파일 읽기 불가; empty로 대체하지 않음 |
| 501 RAW_ARCHIVE_NOT_SUPPORTED | 해당 device의 지원되는 reader mapping 없음 |

기존401/400/403 인증·Host·Origin 및404 device 경계도 적용합니다. 경로·토큰·원본 오류 내용은
오류 응답에 포함하지 않습니다. 후보에 손상 행이 있으면 기간 밖의 행이어도 실패할 수 있습니다.

## 구현과 보전 근거

새 `raw_history.py`의 read-only URI(`mode=ro`)·query_only·단일 read transaction으로 별도 연결을
사용합니다. RawArchive.start를 조회에 호출하지 않고 파일/DB·테이블·커서 생성이나 변경을 하지
않습니다. `immutable=true`로 WAL을 무시하지 않습니다. SQLite 자체의 WAL sidecar/lock 관리는
그대로입니다. 후보 count와 BLOB length를 먼저 제한하고, JSON 중복 key/비정상 수치/raw1.1 및
DB device/run/sequence/observed time 일치를 검증합니다. 원래 입력 숫자는 검증 모델로 재직렬화하지
않습니다. API의 sync route는 FastAPI threadpool에서 읽으므로 수집 writer/worker executor를 공유하지 않습니다.

시험은 ASGI/TestClient, 합성 SQLite, MockTransport를 사용합니다. 실제 WAL의 미커밋 UPDATE를
동시에 열어 원래 committed 값만 읽고 commit 후 새 값을 읽으며, writer cursor 보전과 후속 append를
확인했습니다. 실제1,024후보·64선택·16tag 경계,1,025후보 거부,512tag catalog/projection,
timezone/소수 초/끝 경계, integer/null/quality/absence, 모듈/run 분리, 단위 변경, 원본/첫 receipt/
cursor/gap 불변, 잠금·잘못된 schema·고유 gap index 누락·손상 JSON·중복 key·identity 오류를 다룹니다.
일부 byte/catalog/VM/시간 상한은 상수를 낮춘 장애 주입으로 검증했으며 실제 대량 성능 실증이 아닙니다.

CLI 일반/분리 경로 주입 시험은 uvicorn.run을 가로채 수집/서버를 시작하지 않습니다.
ProcessCollector mapping 시험도 실제 IPC를 시작하지 않습니다. 기존 full suite의 Linux 프로세스
회귀와 이 신규 API 연결 시험을 구분합니다. 실제 장비·고객 데이터·Windows 서비스·.NET/WPF/
실제 UI/대량 장기 성능은 이번에 검증하지 않았습니다. UI는 변경하지 않았습니다.

정확한 실행 결과·지문·독립 검토·보전 기록은
[evidence](evidence/raw-history-20261109.json)에 남깁니다. 실제 commit/push/SHA와 원격 ref 보전은
precommit evidence와 분리해 최종 인계에서 확인합니다.

최종 전체 Python은 **481통과·Windows 전용1건너뜀**(482개,134.267초)입니다.
선택 회귀226개와 추가 실제 경계1개, 독립 emulator26개 및 생성 계약/v3.4 검사가 통과했습니다.
core/API 미구현 RED와 gap 고유 schema RED를 먼저 확인한 뒤 구현했습니다. 추가 경계 시험은
이미 구현된 동작의 대조군입니다. 기존 Windows 전달 skip과 TestClient/httpx 경고1개는 남습니다.

이 단계 당시 독립 검토는 Critical0/Important0/Minor1이었습니다. 검토자는 전체 기록/지문과 작은 읽기 전용
UTC overflow probe를 확인했으며 suite는 재실행하지 않았습니다. **당시 남았던 Minor:**
`0001-01-01T00:00:00+01:00` 또는 `9999-12-31T23:00:00-02:00`처럼 UTC 변환이 datetime 범위를
벗어나는 극단 입력은422(조회)/409(저장행) 대신 일반500이 될 수 있습니다. 실패로 종료되며
DB 쓰기나 정상 부분 결과는 없었습니다. 후속 변경에서 이 오류 분류를 보완했습니다.
[UTC 수정 및 실제 모듈 IPC 검증](RAW_QUERY_UTC_IPC_20261109.md)을 확인하세요.
기존 evidence의 Minor 기록은 이 단계 당시의 이력으로 보존합니다.

Ruling: 한도 밖 run은 오류입니다. 시간 인덱스 이행을 피하고 기존 원본을 보호하는 비용으로 큰 run의
좁은 시간 조회도 이 단계에서는 지원하지 않습니다.
Ruling: 사전은 관찰 metadata입니다. 장비별 의미/설명과 전체 과거 run 탐색은 제공하지 않습니다.
