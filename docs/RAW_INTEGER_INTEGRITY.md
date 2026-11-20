# 범용 숫자 태그의 정수 변환 무결성

2026-11-09, Linux 클라우드 환경에서 합성 입력으로 검증했다.

## 기준 이력과 범위

기준은 공식 원격 `improve/competitive-depth-20261106`의
`df02049ca52621084f57d95a20e256c37c8145a6`이다. 새 작업 브랜치는
`improve/numeric-tag-integrity-20261109`이며 기존 커밋을 부모로 유지한다.
접근할 수 없는 보존본 `5075ccd`는 읽거나 복원하지 않았다. 이번 변경은
그 보존본의 대체·복원·통합을 의미하지 않는 별도의 신규 개선 이력이다.

## 변경 동작

원시 숫자 태그의 `RawSignal.value`는 기존의 유한 float 계약을 유지한다.
이전에는 JSON 정수 `9007199254740993`이 검증 과정에서
`9007199254740992.0`으로 바뀌어 저장될 수 있었다.

이제 float 변환 전에 정수의 정확한 표현 가능 여부를 검사한다. 값이 달라지는
양수·음수 정수와 float 범위를 넘는 정수는 페이지 검증 오류로 거부한다.
정확히 표현되는 `9007199254740994`나 `2**100`은 계속 허용한다.
기존 bool 거부, 유한 float와 품질에 맞는 null 처리는 유지한다.

JSON replay 페이지 전체를 검증한 뒤 저장하므로 정상 배치 뒤에 잘못된 정수가
있어도 해당 페이지의 원본 행·누락 구간·커서는 기록하지 않는다. 재시작 전에
저장한 원본·첫 수신 시각·장비·모듈 정보는 보존되고, 입력을 수정한 재시도는
정상 저장된다. UI 디자인과 분석용 공통 `Number` 계약은 변경하지 않았다.

## 실행한 검증

Python 3.12.14와 `requirements-dev.txt`의 고정 의존성을 별도 `.venv`에 설치하고
프로젝트를 `uv pip install --python .venv/bin/python --no-deps --no-build-isolation -e .`로 연결했다.

- 수정 전 기존 archive/replay 시험: 34개 통과.
- 신규 회귀시험을 먼저 실행: 4개 실패·4개 통과. 손실 정수의 허용과 페이지 저장을 재현했다.
- 수정 후 `.venv/bin/python -m pytest -q tests/test_archive.py tests/test_replay_collector.py`: 42개 통과.
- `.venv/bin/python -m pytest -q`: 307개 통과·1개 건너뜀. PowerShell 7이 필요한 Windows 전달 시험은 건너뛰었고, 기존 Starlette/httpx deprecation 경고 1개가 있었다.
- `.venv/bin/python scripts/generate_contracts.py --check`: 통과.
- `.venv/bin/python scripts/validate_v34.py`: 통과. SQL 검사는 reference 문법 검사 범위다.

시험이 갱신한 기존 실행 증거 파일은 기준 버전으로 복구하고 이 변경에 포함하지 않았다.
Windows/.NET 전체 연결과 실장비 시험은 실행하지 않았다.

## 남은 한계

이번 가드는 JSON 정수 토큰과 Python int 입력의 float 변환 손실을 차단한다.
소수·지수 표기나 이미 float로 반올림된 값의 원래 십진 정밀도, Python의
Decimal/Fraction 입력까지 보장하지 않는다. 모든 큰 정수를 원래 정수형으로
저장하는 기능과 과거 원본 검색 API는 별도 작업이다.

기존에 저장된 반올림 값은 이 변경으로 복원되지 않는다. 기존 archive의
동일 키 재전송 비교가 `-0.0`과 `0.0`을 동등하게 취급하는 문제도 별도로 남아 있다.
