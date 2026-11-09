# raw-v1 / nullable adapter 공통 읽기 첫 단계

기준은 `fcca85fd5e85b5b376e73635bcbb29a36f89eee8`입니다. 접근 가능한 개선 이력에서 독립 구현했으며, 접근 제한 보존본이나 `5075ccd`를 복원하지 않았습니다.

## 실제 연결 범위

`python -m aegis.stored_records`와 `query_stored_records`는 기존 raw-v1 SQLite 및 별도 nullable `adapter-store-1`의 **저장 완료된 기록을 공통 읽기 계약**으로 조회합니다. 기존 writer·raw-history HTTP/IPC·유량·simulator 경로와 UI는 그대로 사용합니다. 설정한 저장소마다 읽기 snapshot의 원본/커서/gap 또는 fixture/페이지/원본/최초 수신/커서/EOF를 검사한 후 결과를 반환합니다.

raw-v1 쓰기는 source run/sequence/time과 SIMULATOR 문맥을 필수로 요구합니다. nullable adapter 기록을 여기에 넣기 위해 원본 값을 채우지 않았습니다. 응답의 `adapterToRawV1WriteDelivery: NOT_CONNECTED`는 **직접 쓰기 이행이 남아 있음**을 나타냅니다. 이번 연결은 `readCompatibility: DUAL_STORE_PROJECTION`이며, 기존 API가 자동으로 새 형식을 읽거나 일반 장비 수집기가 연결된 상태는 아닙니다. `sourceAcknowledgement: NOT_SENT`, `schemaMigrationPerformed: false`도 항상 명시합니다. 자동 이전·삭제·archive 전환은 수행하지 않습니다.

## 사용

명시적으로 선택한 합성 저장소 두 개를 예로 들면, 설정 파일은 다음 형태입니다. 존재하는 원본을 그대로 참조하며 새 DB를 생성하지 않습니다.

```json
{"schemaVersion":"stored-record-sources-1","sources":[
  {"storeId":"legacy-example","kind":"RAW_V1","path":"./synthetic-raw.sqlite"},
  {"storeId":"adapter-example","kind":"ADAPTER_STORE_V1","path":"./synthetic-adapter.sqlite"}
]}
```

설치된 checkout 또는 `PYTHONPATH=src`에서 실행합니다. 상대 DB 경로는 **현재 작업 디렉터리** 기준입니다.

```sh
python -m aegis.stored_records --sources sources.json \
  --received-from 2026-11-09T00:00:00Z --received-to 2026-11-10T00:00:00Z \
  --signals synthetic.temperature,synthetic.optional --max-records 64
```

`--device`는 선택 사항이며 adapter의 최대128자/colon 식별자도 지원합니다. 라이브러리의 sources는 같은 필드의 dict 목록이며 path에 Path도 사용할 수 있습니다. `signals`는 CSV 문자열 또는 list/tuple이고 생략하면 저장된 모든 태그를 반환합니다. 선택된 미존재 태그는 `present:false, reading:null`, 존재하는 결측값은 `present:true, reading:{value:null,quality:"MISSING",unit:...}`로 구분합니다.

실패는 종료2와 stderr `{"error":"CODE"}`입니다. 잘못된 설정/시간/선택은 INVALID_QUERY, 읽기 불가는 SOURCE_UNAVAILABLE, 저장 무결성 실패는 SOURCE_INVALID, 선택 pin 불일치는 SOURCE_BINDING_MISMATCH, 중복 물리 파일은 DUPLICATE_SOURCE, 한도 초과는 QUERY_LIMIT입니다. 어느 한 저장소가 실패해도 부분 조회 결과를 stdout에 내보내지 않습니다. 결과 출력 장애도 성공으로 종료하지 않습니다. 성공 시에는 조회를 위해 선택한 **원본 값·식별자**가 의도적으로 포함됩니다. 파일 경로나 내부 after/next replay token 필드는 내보내지 않습니다.

## 원본과 근거 의미

`timeBasis: FIRST_RECEIPT`는 원본 시각이 없는 기록도 조회하기 위한 **최초 host 수신 시각** 기준입니다. aware ISO8601 입력을 UTC로 비교하며 최대1일의 `[from,to)` 구간입니다. `firstReceivedAtUtc`는 보관한 표현을 그대로 반환하고, `sourceMetadata.observedAtUtc/sequence/runId`와 각 PROVIDED/NOT_PROVIDED는 원본 그대로 남깁니다. 수신 시각·SQL 순서·delivery token으로 누락된 원본 필드를 채우지 않습니다. 제공된 원본 시각의 offset 문자열과 큰 정수도 보존합니다.

`identity`는 storeId/storeKind/nativeKeyKind/nativeKey입니다. raw-v1 키는 저장된 device/run/sequence, adapter 키는 adapter/version/**delivery stream**/recordId입니다. adapter delivery stream은 source run이 아닙니다. 각 저장소의 정상 재전송은 기존 저장 중복 정책을 따릅니다. 형식이 다르거나 다른 저장소에 있는 유사 기록은 같은 물리 이벤트라는 근거가 없으므로 합치지 않습니다. 하나의 실제 파일을 여러 label로 설정하면 거부합니다.

raw-v1 `source: SIMULATOR`는 `declaredOrigin: SYNTHETIC`, `originEvidence: DECLARED_ONLY`, `producerDeclaration: SIMULATOR`로 표현합니다. 당시 저장하지 않은 acquisitionMethod/sourceAccessMode는 `NOT_RECORDED`입니다. 현재 config로 과거를 추정하지 않습니다. adapter 출처·수집 방식·접근 모드는 검증된 최초 committed page에서 읽습니다. 장비 확인을 하지 않았으므로 `hardwareIdentityVerified:false`입니다. raw hierarchy는 저장 column이며 adapter hierarchy는 `NOT_RECORDED`/null입니다.

`evidence.recordSha256`는 raw의 **정확한 저장 payload JSON 문자열**, adapter의 **canonical original record JSON**에 각각 대응합니다. representation을 함께 표기하므로 두 hash가 같은 표현이라는 뜻이 아닙니다. adapter는 해당 기록이 처음 나타난 검증된 pageWireSha256도 제공합니다. 저장된 fixture·hash의 자기 일관성은 서명/진본 확인이 아닙니다. adapter source 설정의 선택 `expectedFixtureSha256`은 canonical parsed fixture의 SHA256 pin이며 일치하면 `bindingEvidence: EXPECTED_FIXTURE_SHA_MATCHED`입니다. pin의 신뢰는 호출자가 확보해야 하며 장비 인증으로 바뀌지 않습니다. raw source에는 이 옵션을 허용하지 않습니다.

## 읽기 경계와 한도

각 파일은 `mode=ro`, query_only, trusted_schema off와 명시적 read transaction으로 고정합니다. 기존 adapter writer를 생성하지 않고, 연결 전용으로 추출한 동일 검증기를 writer와 reader가 사용합니다. writer의 binding·commit/readback·fence 동작은 기존 회귀시험으로 유지합니다. 원본 행·최초 receipt·커서·gap에 DML을 수행하거나 journal mode/checkpoint/max_page_count를 변경하지 않습니다.

일반 SQLite WAL reader는 WAL/SHM companion 생성·read-mark 등 metadata를 변경할 수 있습니다. 이는 모든 파일의 byte 무변경 보장이 아닙니다. immutable/nolock 또는 live main 파일만 복사하는 편법은 사용하지 않습니다. 신뢰하는 로컬 디렉터리의 일반 단일 hardlink 파일만 지원하며 마지막 경로의 symlink·특수파일·중복 물리파일·reserved companion 이름을 거부합니다. 적대적 동시 경로 교체나 원격 파일시스템을 다루는 기능은 아닙니다.

각 저장소 snapshot은 내부적으로 일관되지만 여러 파일을 한 번에 고정하는 분산 transaction은 없습니다. `snapshotConsistency: PER_STORE_SNAPSHOT_NO_DISTRIBUTED_ATOMICITY`, `sourceCoverage: NOT_ASSERTED`로 표시합니다. `selectionComplete:true`는 설정한 모든 snapshot에 대한 제한 내 선택 처리가 완료됐다는 의미입니다. raw gap count는 **저장소 전체** sequence gap evidence이며 시간 구간의 완전성 판정이 아닙니다. adapter gap evidence는 NOT_RECORDED/null이며 0으로 표시하지 않습니다.

최대4개 저장소, raw 저장소당10,000개 원본 행, adapter는 기존64page/1,024record/32MiB 검증 한도를 적용합니다. 반환 최대64개, 선택 태그16개, 출력4MiB, 공유 cooperative5초/SQLite VM5백만 step 한도입니다. 초과 시 잘라서 성공하지 않습니다. raw 검증은 기존256MiB DB/행256KiB/총 payload128MiB와 cursor/gap 한도도 적용합니다. 전체 저장 무결성 검사이므로 기간 밖의 손상이나 많은 행으로도 거부할 수 있습니다. 0.2초 busy timeout이며 native I/O/파일 호출의 강제 종료나 실시간 deadline 보장이 아닙니다. 설정은64KiB regular nonblocking/no-follow JSON이고 중복 key/비유한 숫자를 거부합니다. CLI 설정의 이 파일 접근은 Linux/POSIX에서만 검증했습니다.

## 검증과 남은 이행

합성 시험은 null 원본 조회, exact offset/큰 정수, raw/adapter 출처 구분, 태그 누락/결측, 구간 경계, 별도 identity, 저장 실패 rollback·재시작·정상 retry의 최초 receipt 보전, page/record/receipt/cursor/숨긴 schema 변조 거부, 두 형식의 동시 WAL append snapshot, 읽기 authorizer의 DML 거부, main DB/원본 설정 보전 및 실제 CLI 실패 시 부분 stdout 방지를 포함합니다. 수치·해시는 [검증 기록](evidence/dual-store-read-20261109.json)에 있습니다.

후속 쓰기 통합은 nullable 원본·delivery 식별자를 가진 새 archive version 협의, 검증된 backup/rollback, 명시적 opt-in 이행, 기존 raw-v1 reader 병행 유지, 장기 adapter worker/source ACK 설계가 필요합니다. 이번 단계는 이 작업을 자동 수행하지 않습니다. 실제 장비·회사 라이브러리·실데이터, Windows/서비스, 물리 디스크 고갈·전원 차단, 장기/대량 부하, 실제 운영·원격 CI는 검증하지 않았습니다. UI·디자인·장비 제어는 변경하지 않았습니다.
