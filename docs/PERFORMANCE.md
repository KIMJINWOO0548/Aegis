# 수집 성능: 병목, 변경, 같은 설정의 재측정

이 문서의 측정과 source hashes는 신호 검토 기능 추가 전 버전에 대한 과거 증거다. 현재 개발의 우선순위와 새 분석 경로의 한계는 [신호 검토 보고서](SIGNAL_REVIEW.md)를 따른다. 아래 측정값을 새 기능이 포함된 현재 버전의 처리 용량으로 사용하지 않는다.

2026-10-19, Windows 11 / Python 3.12.14 / 논리 CPU 8개. 고객·장비 자료 없이 합성 데이터만 사용했다. 결과는 이 데스크톱의 짧은 실험이며 현장 용량이나 채용 평가를 보증하지 않는다.

## 조사와 작은 변경

200개 스냅샷 × 256개 신호를 프로파일링했다. 수신 검증 뒤 `model_dump(mode="json")`으로 수백 개 신호를 사전으로 만들고, 저장소가 다시 Python `json.dumps`를 수행했다. 원시 신호별 enum 변환도 반복됐다. 별도 비용으로 JSON 생성·수신 검증·SQLite FULL commit이 남아 있었다. [프로파일 원본](evidence/portfolio/profile-before.txt).

기존 입력 검증과 SQLite WAL/FULL, 배치·누락·커서의 원자적 commit을 유지하면서 다음만 바꿨다.

- 검증된 모델을 `model_dump_json()`으로 직접 직렬화하여 중간 사전 생성과 두 번째 Python JSON 인코딩을 줄였다.
- 원시 신호 내부 품질은 검증된 enum 문자열로 보관한다. 허용 enum과 GOOD/null 거부는 유지한다.
- 기존 DB의 정렬된 JSON과 새 JSON의 키 순서가 달라도 같은 payload를 재시도로 인정한다. 내용이 바뀐 동일 ID는 페이지 전체를 rollback한다. 첫 수신 시각·계층은 덮어쓰지 않는다.

수정: [수집기](../src/aegis/collector.py), [저장소](../src/aegis/archive.py). [이전 저장 형식과 새 경로를 함께 쓰는 회귀 시험](../tests/test_archive.py)은 재시도·변경된 중복·BAD/null·커서·누락 rollback을 확인한다. 저장 DB 스키마나 HTTP 프로토콜을 바꾸지 않았다.

## 직렬화 CPU 비용만 분리한 실험

동일하게 검증된 200개 프레임을 두 경로로 인코딩하고 JSON 의미가 같은지 검사했다. cProfile을 끈 뒤 실행 순서를 번갈아6회 측정했다. `process_time_ns`를 사용하며 디스크·네트워크·입력 검증은 측정 구간 밖이다. Windows CPU 타이머의 거친 간격과6회 표본의 한계가 있다.

| 인코딩 경로 | 중앙 CPU 시간 / 200프레임 |
|---|---:|
| 모델 → 사전 → 정렬 JSON | 320.31ms |
| 검증 모델 → 직접 JSON | 78.13ms |

이 실험에서 **직렬화 CPU 시간은 약75.6% 감소**했다. 전체 시스템의4.1배 처리율 향상을 뜻하지 않는다. [6회 전체 표본·프로파일](evidence/portfolio/profile-final.txt).

```powershell
.venv/Scripts/python.exe scripts/profile_ingestion.py
# 이전 사전 저장 경로까지 프로파일하려면:
.venv/Scripts/python.exe scripts/profile_ingestion.py --legacy-serialization
```

## 전체 프로세스: 이전/개선 버전을 교차 실행

이전 소스를 별도로 보존한 뒤 A → B → A → B 순서로 실행했다. 각 실행은 독립 프로세스를 새로 시작하며, 모듈4개 × 신호256개,10ms 표본(목표102400값/초),100ms 조회,3초 준비·15초 측정, WPF/EWMA 제외, SQLite WAL/FULL 포함이다. 백그라운드 프로그램·스토리지 부하와 시작 backlog는 통제하지 못했다. 같은 입력 설정을 사용한 반복 관측이며 인과적 성능 개선을 입증하는 통제 실험은 아니다.

| 실행 순서 | 버전 | 기록 값/초 | API p95 | 데이터 나이 p95 | 종료 시 대기 배치 최대 |
|---:|---|---:|---:|---:|---:|
| 1 | 이전 A | 101407 | 3.92ms | 385ms | 0 |
| 2 | 개선 B | 101184 | 4.73ms | 335ms | 0 |
| 3 | 이전 A | 137829 | 5.10ms | 5375ms | 0 |
| 4 | 개선 B | 107552 | 6.80ms | 4866ms | 247 |

세 번째 실행의 처리율이 발생률보다 큰 것은 측정 시작 전에 쌓인 자료도 처리했기 때문이다. 이를 지속 수집 용량으로 해석하면 안 된다. 네 번째 실행은 종료에도 backlog가 남았다. 모든 실행에서 저장한 접두 구간은 연속되고 DB 무결성은 정상, 확인한 누락 구간0, 소유 프로세스 정리는 통과했다. 아직 수신하지 못한 소스의 꼬리까지 보관했다는 뜻은 아니다.

**결론:** 직접 직렬화의 CPU 비용 감소는 확인했다. 전체 수집은 이전 버전도 따라간 실행이 있었고 시점별 지연 편차가 커서,100Hz 지속 실시간성이나 전체 처리율 향상을 보장할 수 없다. 기존 실패 결과도 [이전 보고서](DURABLE_INGESTION_REPORT.md)에 보존한다. 평균 API 지연만으로 수집 성공을 주장하지 않는다.

원본: [A1](evidence/portfolio/before-r1.json), [B1](evidence/portfolio/after-r1.json), [A2](evidence/portfolio/before-r2.json), [B2](evidence/portfolio/after-r2.json). [이전 소스 식별](evidence/portfolio/baseline-source-hashes.json), [개선 소스 식별](evidence/portfolio/candidate-source-hashes.json). 탐색용 `after-100hz.json`은 실행 중 다른 검사와 겹쳐 비교 대상에서 제외했다고 파일에 명시했다.

같은 Python 의존성으로 이전 경로도 다시 실행할 수 있도록 [동결한 이전 Python 소스](evidence/portfolio/baseline-source.zip)를 보존했다. 압축에는 소스와 측정 스크립트만 있으며 토큰·DB·가상환경은 없다. 압축 내부의 변경 대상 해시도 위 이전 소스 식별과 대조했다.

```powershell
Expand-Archive docs/evidence/portfolio/baseline-source.zip runtime/comparison-A
.venv/Scripts/python.exe runtime/comparison-A/scripts/benchmark_emulators.py --replay --isolate --counts 4 --seconds 15 --output runtime/benchmark-before-repeat.json
.venv/Scripts/python.exe scripts/benchmark_emulators.py --replay --isolate --counts 4 --seconds 15 --output runtime/benchmark-repeat.json
```

다음 검증은 다른 작업 부하를 통제한 장시간 반복, 시작 backlog가 동일한 정상 상태 구간, 모든 commit의 계측, 디스크 용량·보관 정책을 포함해야 한다. 현재 추가적인 내구성 완화나 검증 생략으로 숫자를 높이지 않았다.
