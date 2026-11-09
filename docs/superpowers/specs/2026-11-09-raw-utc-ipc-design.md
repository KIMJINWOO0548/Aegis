# Raw UTC 경계와 실제 모듈 IPC 검증

사용자는 이전 단계에서 남은 UTC 변환500의 수정과 실제 모듈 IPC의 합성 검증을 승인했습니다.
기준은 `5b189ef239ca5107698c98275d5c2bb7659ed988`입니다. 기존 raw 계약·원본·커서·한도·UI를 보존합니다.

## UTC 오류

로컬 재현에서 `0001-01-01T00:00:00+01:00`와 `9999-12-31T23:00:00-02:00`는 어휘상 ISO8601이지만
UTC로 바꾸면 Python datetime 범위를 벗어나 OverflowError가 납니다. `_query` 및 `_batch`의
기존 ValueError/TypeError 경계가 이를 놓칩니다. 두 경계에서 OverflowError도 고정 오류로
처리합니다. 전역 timestamp parser·원본 시각·숫자·모든 기존 모델은 바꾸지 않습니다.

조회 from/to overflow는422 INVALID_QUERY, 저장 payload/index observed time 및 first receipt의
overflow는409 RAW_ARCHIVE_INVALID입니다. 경로·원본 오류값을 echo하지 않고 정상 부분 결과나
DB 쓰기가 없어야 합니다. UTC 범위 안의 year1/year9999 및 offset 경계는 계속 허용합니다.

## 합성 실제 IPC 검증

기존 emulator 두 개·API CLI·ProcessCollector·실제 module_worker 프로세스와 stdin/stdout IPC,
모듈별 SQLite/WAL 및 live loopback HTTP로 raw-history/raw-tags를 검증합니다. fake frame/Popen/
HTTP/reader를 쓰지 않습니다. 원본 DB를 수정·행 주입하지 않고 실제 simulator 자료만 사용합니다.

실제 worker PID/IPC summary, 원본과 최초 receipt/모듈/순번/숫자/품질/null·absent 비교, 한도413 뒤
커서/peer 진행·원본 보전, 안전하다면 첫 worker의 독립 재시작·재개와 재조회 동일성을 확인합니다.
기존 stack context cleanup으로 소유 프로세스를 종료/회수하고 알려진 worker만 확인합니다.
제품 연결이 이미 동작하면 IPC 제품 코드를 바꾸지 않고 검증 대조군으로 보고합니다.

새 시험은 synthetic/local Linux 범위이며 실제 장비·고객 데이터·Windows·.NET·대량 성능을
검증했다고 주장하지 않습니다. 기존 한도(후보1024/8MiB, 선택64, tag16 등)와 sourceCoverage,
MISSING/null 대비 absent, source/receipt 구분을 보전합니다. UI 및 접근 제한 보존본은 건드리지 않습니다.

Ruling: 오류 수정을 raw 조회의 두 검증 경계로 제한합니다. 비용은 다른 기존 subsystem의
timestamp 오류 처리까지 바꾸지 않는 것입니다. 실증 범위도 실제로 실행한 Linux 합성 경로로 한정합니다.
