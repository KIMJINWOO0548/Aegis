# Raw UTC 오류 처리와 실제 모듈 IPC 검증

기준은 원격 `improve/raw-history-query-20261109`의
`5b189ef239ca5107698c98275d5c2bb7659ed988`입니다. 이전에 남겼던 극단 UTC 변환 오류를
고치고, 새 raw query를 실제 모듈 프로세스 경로에서 검증하는 후속 작업입니다.
이전 접근 제한 보존본이나 `5075ccd`를 복구·대체했다는 뜻이 아닙니다.

## UTC 수정

`0001-01-01T00:00:00+01:00`와 `9999-12-31T23:00:00-02:00`는 ISO 문자열로 파싱되지만
UTC 정규화 때 Python datetime 범위를 벗어나 `OverflowError`가 발생합니다. 기존 raw query의
두 오류 경계가 ValueError/TypeError만 처리해 HTTP500으로 끝났습니다.

제품 수정은 `raw_history.py`의 두 except 목록에 OverflowError를 추가한 것뿐입니다.
전역 timestamp parser, 원본 날짜·순번·값, schema/cursor, API route/응답 형태와 한도는 그대로입니다.

| 입력 위치 | 수정 후 오류 |
| --- | --- |
| 조회 fromUtc/toUtc | 422 INVALID_QUERY |
| 저장 payload observedAtUtc | 409 RAW_ARCHIVE_INVALID |
| 저장 index observed_at_utc | 409 RAW_ARCHIVE_INVALID |
| 최초 received_at_utc | 409 RAW_ARCHIVE_INVALID |

원본 오류값/파일 경로는 응답에 echo하지 않고, 성공 prefix나 DB 쓰기는 없습니다. 시간·순번을
새로 만들거나 잘못된 원본을 고쳐 저장하지 않습니다. 다른 subsystem의 timestamp 정책은 변경하지 않습니다.

새 UTC 시험30개 중 미수정 상태의22개가 예상대로 실패했습니다(500→422/409 및 직접 helper의
OverflowError). 유효한 year1/year9999·offset8개 대조군은 통과했습니다. 최소 수정 후 UTC30개와
기존 raw/API/계약을 포함한 선택219개가 통과했습니다. 유효 경계의 원본 시각 문자열·첫 receipt·
MISSING/null과 absent 및 DB rows/cursors/gaps 보전을 확인했습니다.

## 실제 IPC 검증 범위

기존 emulator/API CLI/ProcessCollector/module_worker·stdin/stdout IPC·모듈별 SQLite/WAL·live
loopback HTTP를 사용합니다. synthetic Linux 범위이며 실제 장비나 Windows 실증이 아닙니다.
기존 mapping-only TestClient 시험과 구분하며, 검증에서 제품 연결이 이미 동작하면 worker/IPC를
추가 수정하지 않습니다. 실제 실행 결과와 종료·회수 증거는 아래 최종 검증 기록에 남깁니다.

단독 실제 통합 시험1개가19.313초에 통과했습니다. 2모듈·256tags·200ms 수집의 DATA-QUALITY
시나리오가 실제 생성한 sequence75/76의 MISSING/null과 존재하지 않는 tag를 비교했습니다.
raw-history의 값/source time/first receipt/저장 당시 module이 module DB와 일치하고 catalog256개,
maxBatches=1의413/no prefix, 조회 후 양 모듈의 커서 진행을 확인했습니다.

첫 모듈의 소유 worker만 종료한 뒤 새 worker로1회 복구하고 같은 source run에서 재개했습니다.
peer는 재시작 없이 진행했으며 과거 batch/first receipt/태그 응답은 전후 동일했습니다.
종료 후 두 DB 각각94개 연속 행, cursor=실제 마지막 행, gap0, integrity_check=ok였습니다.
소유 직접 자식과 알려진 worker3세대의 종료를 확인했습니다. 이 숫자는 단독 실행 결과이며
전체 회귀 실행은 별도 증거로 구분합니다. 시험 첫 작성 시 조건식 SyntaxError는 서비스 시작 전
발견·수정했고 실제 통합 통과 결과와 구분해 실행 기록에 남깁니다.

원본 숫자 tag는 기존 설계대로 module DB에 남고, 요약과 여덟 유량 관측만 IPC를 통과합니다.
API는 서버가 지정한 module DB를 read-only로 조회합니다. 새 raw tag를 IPC frame이나 기존 flow
Observation에 추가하지 않았으며 실제 legacy 관측은 여전히8개/aux 없음, 출처 선언은 SYNTHETIC입니다.
새 IPC 시험은 이미 있는 연결의 합성 통합 대조군이므로 미구현 RED라고 주장하지 않습니다.

## 보전과 한계

기존 raw 한도(후보1,024/8MiB, 선택64, 요청tag16, catalog512, SQL budget 등)를 유지합니다.
큰 run은 좁은 시간 선택도413이며 사전은 관찰 metadata입니다. 과거 run 탐색·대량 시간 seek·
장비 tag 의미/인증은 추가하지 않습니다. sourceCoverage=NOT_ASSERTED, run 순번 gap의 범위,
tag 부재와 원래 quality=MISSING/null, source observed time과 receipt를 계속 구분합니다.

UI·디자인·simulator·collector·archive writer/schema/cursor·worker IPC·canonical YAML·생성/native
계약은 보존 대상입니다. 실제 장비·고객 데이터·Windows 서비스·PowerShell7 전달 사례·.NET/WPF·
대량/장기/다중 조회 성능은 이번 실증에 포함하지 않습니다. 기존 역사 evidence는 당시 기록으로 남깁니다.

Ruling: UTC 오류 처리를 raw 조회의 두 경계로 제한했습니다. 다른 subsystem의 datetime 오류까지
확장하지 않는 비용이 있지만 기존 계약의 변경 범위를 작게 유지합니다.

최종 실행 수·지문·실제 IPC 요약·독립 검토는
[evidence](evidence/raw-query-utc-ipc-20261109.json)에 기록합니다. 실제 commit/push 및 원격 SHA는
precommit evidence와 분리해 최종 인계에 기록합니다.

최종 전체 Python은 **512통과·Windows 전용1건너뜀**(513개, runner154.576초)입니다.
독립 emulator26개와 생성 계약/v3.4 검사도 통과했습니다. 독립 검토는 Critical/Important/Minor0건이며
검토자는14개 작은 읽기 전용 timestamp probe와 선택/IPC 기록을 확인했습니다. 전체 suite는
controller가 실행했고 검토자는 별도 재실행하지 않았습니다. 기존 보호 파일385개는 byte 동일,
기존7개 checkout과10개 원격 head를 보존했습니다. 새로 실행한 전체 suite의 IPC 기록도 별도로
evidence에 포함하고 모든 알려진 소유 worker와 직접 자식의 종료를 확인했습니다.
