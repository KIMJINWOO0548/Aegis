# Aegis Insight v3.4 — 제품 완성도 강화 / 개발 추가 명세

**상태:** 구현 전 제품·기술 의사결정. v3.3의 원본 로그/Trend 상세 명세를 바꾸지 않고 보완한다. 단일 기술 정본은 `AEGIS_CONTRACT_v3_4.yaml`, API 필드/경로 정본은 `OPENAPI_THIN_DEMO_v3_4.yaml`. v3.4 추가 API는 **후속 작업**이며 첫 데모 구현 대상이 아니다.

## 1. 고객 가치와 경쟁 기준

**포지셔닝:** PLC/HMI/SCADA의 제어·보호 기능을 대체하지 않는, Windows 로컬 기반의 읽기 전용 Condition Intelligence 레이어. 제품 차별점은 (1) 경고 전 변화 (2) 왜 감지했는가 (3) 당시 관측의 신뢰도 (4) 담당자 대응과 이후 추세를 한 화면에서 설명하는 **재현 가능한 사건 기록**.

- 구매자: 장비 유지보수 담당·생산기술·장비 제조사의 서비스 조직. 사용자: 현장 운영자, 유지보수 엔지니어, 고객 IT/OT 관리 담당자.
- 요구사항 우선순위: **P0** 절대로 잘못 설명/조치하지 않음·데이터 품질·읽기 전용·재시작 복원; **P1** 사용성을 높이는 경고 묶음·비교 Trend·Evidence; **P2** 모델 탐색·외부 시스템 연동·상용 기능.
- 경쟁 검증: 일반 SCADA Historian과 비교해 '이미 수집 가능한지', '해석/원인 탐색 시간이 줄었는지'를 실제 사용자 과업으로 비교한다. ML 정확도만으로 가치를 주장하지 않음.

## 2. 경고 체계: Alarm과 Advisory의 경계 [R01 R02 R03]

PLC safety/process alarm은 **원본 이벤트를 그대로 표현**하고 Aegis의 ML-generated `ADVISORY`와 데이터 모델/화면을 구분한다. 읽기 전용 Insight는 알람을 PLC에서 ACK하거나 숨기지 않는다. Aegis warning ACK는 점검 권고를 **확인했다는 감사 이벤트**일 뿐이고 경고 원인이 해결됐다는 뜻이 아니다.

- Warning episode 키: `deviceId + modeId + advisoryType + incidentGroupingWindow`. 그룹화는 severity가 높은 원본 PLC 알람을 결코 소거하지 않는다.
- 상승 조건: `GOOD` 품질의 N개 연속 창 + minimumDuration + mode-specific baseline 만족. N=3, 30초 갱신은 초기 시험값이며 현장 조건에 따라 조정/검증.
- 회복 조건: 진입 임계값과 다른 이탈 임계값(hysteresis) 및 연속 정상 창. ACK 이후 상태가 정상이라고 자동 주장하지 않는다.
- 억제 정책: 동일 신호·동일 모델 버전의 chattering만 지정된 수명 내 시각적으로 묶고 `suppressedCount`/최초·마지막 시각을 보존. **신규 원인·위험 상승·보호 알람은 절대 숨기지 않는다.**
- 상태: 기존 `OPEN→ACKNOWLEDGED→RESOLVED` 또는 `MANUAL_REVIEW` 유지. 알림의 노출/중복 처리(`visible/grouped/cooldown`)는 별도 `presentationState`로 둔다.
- 알람 KPI: `advisories_per_device_day`, `repeats_per_episode`, `time_to_ack`, `unacknowledged_age`, `operator_confirmed_actionable_ratio`. **actionable ratio는 운영자 의견이며 실제 fault precision이 아님**.

## 3. ML 설계 강화: 장비 모드와 품질·오경보 [R04 R06 R14]

- `modeId/recipeRevision`별 정상 참조 데이터를 분리. `SETUP/MAINTENANCE`에서는 PRODUCTION 기준으로 경고하지 않는다. 기준 데이터 없으면 `INSUFFICIENT_DATA`.
- 관측값이 GOOD인지와 응답이 최신인지 먼저 검사. 센서 dead/stale·외부 장비 시계 드리프트·시퀀스 역전 시 경고가 아니라 `DEGRADED` + 데이터 품질 이슈.
- EWMA `flowTrackingError`는 동작 조건별 정상 범위와 **상대/절대 오차**를 확인한다. setpoint가 0 또는 비운전 모드일 때 분모 0을 피한다.
- Normal-reference percentile은 모드·설비별 정상 기준의 이상 점수 순위; **RUL·고장 확률·생존 확률이 아니다.** baseline 버전 변경 전후 같은 Trend에 원시 점수 직접 비교 금지.
- 데이터 분리: 장비/주기/시계열 그룹 분리, 윈도 경계는 300초 이상 embargo(300초 윈도 기준 초기값), 임계값 조정은 평가셋을 본 뒤 바꾸지 않는다. 시뮬레이터 정답은 진단 Agent 작업공간 외부에 둔다.
- UCI Hydraulic ID447: 실제 시험장치 센서 데이터이지만 **60초 주기 사이클과 부품 상태 라벨**이다. 외부 데이터에서 평가할 것은 정상/열화 상태 구분 등 오프라인 모델/피처 파이프라인이며 연속 실운전 조기 lead-time이 아니다. `profile.txt` 안정 플래그를 활용한 분리 결과도 함께 표시. CC BY 4.0 출처 기록.
- 평가표: `precision/recall/F1` (라벨 가능시), `false alerts/device-day` (연속 샘플 배경시간 확보 시), `detection delay`, `time in DEGRADED`, `calibration coverage`, `abstention rate`. 해당 분모와 정의 불가 시 N/A로 표기. 현장 예방 실적은 P2 SHADOW 이후 검증.

## 4. 사건 Evidence 패킷 / Trend 품질 [R11 R12 R15]

v3.3의 원시(72h)·1분(90d)·1시간(365d 가설) 보존 체계를 유지한다. 경고당 `warningId, rootTraceId, episodeId, deviceId, modeId, captureStartedAtUtc, capturedThroughUtc, captureStatus, observationRangeRef, configRevision, detectorVersion, normalReferenceVersion, qualitySummary, hashManifest`를 기록한다. 원시 센서값을 감사 테이블에 중복 복제하지 않는다.

- WARN 이벤트에서 참조한 raw 기간의 저장 보장 여부는 `COMPLETE/PARTIAL/EXPIRED/UNAVAILABLE`로 표시. **보관 기간 초과 데이터는 되살린 척하지 않는다.**
- Trend: 목표 vs 실제, residual, EWMA, mode, warning markers, quality gap. `min/max` envelope 및 집계 버전/데이터 원천; 비교 기간은 같은 단위·모드일 때만 겹치기.
- 첫 이벤트(last known good) vs 경고 시작 시각을 같은 시점이라고 말하지 않는다. 기준선/관측 주기/타임스탬프 신뢰도에 따라 `estimatedOnsetWindow` 제공(후속 단계).
- Timeline은 wall clock 역전 가능성을 고려해 `occurredAtUtc`, `receivedAtUtc`, `sourceSequence`, `eventId`를 보존; 단순 시간 정렬을 '인과관계 확정'으로 표시 금지.
- 사용자 지원용 `Support Bundle`: JSONL·장치 상태·모델 버전·설정 해시·경고 요약·검증 가능한 Trend CSV/JSON. 동의·범위 미리보기·Redaction 검사·size cap·암호화 지원·내보내기 감사. **자동 업로드 기본 금지**.
- 그래프 자체의 저하는 제품 장애: `plotLoadLatency`, `queryTruncated`, `snapshotMissing`, `aggregationGap`, `logDroppedCount` 등은 운영 Health 화면으로 추적.

## 5. 수집·저장 안정성과 측정 기법 [R07 R08 R09 R10 R11]

- P1 OPC UA: `Read`, `CreateSubscription`, `CreateMonitoredItems` 등 승인된 읽기·구독만 사용. 실제 revised sampling interval, queue size, data change overflow, status quality를 반드시 읽어내며 overflow는 `OBSERVATION_GAP_DETECTED`와 연관 표시. 서버 인증서는 관리자의 TrustList에 등록된 것만 수락.
- P1 Modbus TCP: Function Code 01/02/03/04만 허용 목록에 포함. 05/06/15/16/23(0x17, 읽기/쓰기 혼합) 등 write/혼합 기능 제거/차단. 라이브러리 호출 제한을 인터페이스+테스트로 확인. 읽기에도 RTU TCP 게이트웨이·PLC 부하 상한 필요.
- P1 초기 부하 한도는 **정책 템플릿**: 통신당 연결 ≤1·장비당 동시 폴 ≤1·최소 폴링 1초·최대 실패 재시도 2회/분. 이 수치는 설비별 승인 전 실제 적용 불가; 고객 OT가 승인한 값을 우선하며 초과 시 자동 완화.
- DB는 SQLite WAL로 시작하지만 single writer/long reader/checkpoint/디스크 가득 참 시나리오 측정. WPF 장기 Trend 쿼리 때문에 수집 writer가 멈추는지 독립 시험. 원시 센서 표본을 INFO 로그에 중복 남기지 않음.
- `8 signals×1Hz`는 장비 1대 하루 **691,200행**(원시 테이블 신호별 행), 72시간 **2,073,600행**. 저장 용량은 행 평균·인덱스·WAL 측정으로만 판단. `50대×8×1Hz`는 1일 **34,560,000행**이므로 SQLite를 '기업 전체 고용량 historian'으로 포장하지 않음.
- 대안 비교: 기본 SQLite(오프라인/배포 용이) → 계측된 병목이 있을 경우 파티셔닝/압축 전략 검토 → 그래도 미충족 시 InfluxDB 등 별도 스토리지 PoC. 고객 네트워크에 추가 서버를 기본 설치하지 않음.

## 6. 보안·상용 배포 [R07 R09 R13 R16]

- 산업 OT에서 사용되는 읽기 전용 프로그램도 PLC 부하·가용성·보안에 영향을 줄 수 있다. 제품 안전 인증·IEC62443 인증 주장 금지.
- 현장 기본값 **수집 서비스/로컬 API만 허용, WPF가 내려가도 수집 지속**. Windows 서비스의 자동 시작, 권한 최소화, 설정/모델 해시 검증, 재시작·전원 차단·롤백 시험. 기존 .NET Worker/Python Windows Service는 D0a에서 비교 ADR 확정.
- localhost API는 host/origin 검증·토큰 수명·CSRF 방어를 점검하고, 서비스/DB/로그 폴더 ACL 설정. 실제 인증서·API 키·고객 원시 로그를 지원 번들에 포함하지 않는다.
- 업데이트: 버전/호환성 표, 서명/해시/롤백, DB migration backup 및 실패 복구. 오프라인 라이선스/실고객 자동 업데이트는 후순위.
- 겸업/IP·고객 데이터 권리는 착수 선행 확인; 특정 회사의 코드를 복제하거나 회사 설비·실고객 로그를 무단 수집하지 않는다.

## 7. UI/UX: 운영자와 기술지원 담당자 분리 [R01 R03]

| View | 최우선 정보 | 주요 동작 | Gate |
|---|---|---|---|
| Overview | 소스 SIM/READ_ONLY, 장비 상태, 상태품질, Trend 1h | Warning 상세, ACK | D-demo |
| Warning Detail | 왜 감지? 신호/Mode/모델/참조 품질, 시간축 증거 | 비교 구간, ACK, 점검 메모 | D1 |
| Alert Analytics | 가장 많이 반복되는 경고, 미확인/오경보, 시간대 | 경고 묶음 분석 (숨김 아님) | D2 |
| Trend Explorer | 동일단위 Trend·품질 갭·변경/운영 mode | 기간 전환, crosshair, export | D1 |
| Operations Health | 수집지연, gap, WAL, ML unavailable, log drop | 서비스 복구 안내 | D1 |
| Support Bundle | 고객 승인 범위·검열 결과·파일 크기 | 번들 export/review | D4 |

화면 기본 정보는 운영자에게 최소화하고, 상세 수치와 원인을 필요 시 드릴다운. 색상만으로 경고를 구분하지 않고 텍스트·아이콘·시간을 함께 표시.

## 8. 실제 고객 사용성 검증 과업(R0/P2)

1. 같은 경고 장면에서 **Aegis 없이** 원인 조사에 걸린 시간 vs **Aegis 사용** 시간을 관측한다. 비교 환경이 달라지면 결과에 표시한다.
2. 운영자가 경고와 PLC 원래 알람을 혼동하지 않는지 확인한다.
3. 가짜 센서 stale·mode switch에서 '문제 없음'으로 오해하는가 확인한다.
4. 클릭 3회 이내 warningId로 경고 전 10분 Trend와 관련 로그에 접근하는지 시험한다(초기 목표, 실제 증명 아님).
5. 고객 지원자가 동의 범위 밖의 자료를 받지 않는지 보안 과업을 시험한다.

## 9. 제품 출시 게이트

| Gate | 실제 개발 범위 | 수용 기준 |
|---|---|---|
| G-LEGAL/R0 | 권리 확인·고객 문제 가설 확인 | 판매 전 선행 확인, 민감자료 금지 |
| D0a | repo·계약·CI·structured log | 빌드/계약/Redaction PASS |
| D0b | DEG-02·EWMA·8 signal mock | 정상→이상 신호와 데이터 갭 재현 |
| D-demo | WPF Trend 1화면·warning/ACK | warningId/모델 버전/Trend 일치, 장비 명령 0 |
| D1 | 수집 Windows 서비스·SQLite raw/1m·gap & hysteresis | 재시작·백그라운드 24h·누락/DB 부하 실측 |
| D2 | UCI hydraulic data + ML 비교·alarm KPI | 평가 누수 방지·false alert/abstention 보고 |
| D3 | LLM 설명 + Incident/Log Explorer | 근거 ID 검사·프롬프트 공격 거부 |
| D4 | 설치/지원 번들/롤백 | 오프라인 Windows VM E2E·정보보호 시험 |
| P1 | 현장 *읽기 전용* 제한 연결 | 승인된 상한 내 OT 부하·인증·gap 검증 |
| P2 | SHADOW→ADVISORY | 운영자 평가·실제 라벨 가능성 및 비용 효익 |
| P3 | 판매 후보 | 업데이트·지원·보안·계약·제품 한계 공개 |

## 10. 신규 독립 실패 테스트

| ID | 자극 | 반드시 관찰할 결과 |
|---|---|---|
| ALM-01 | EWMA 3연속 초과 뒤 정상 복귀/짧은 재돌파 | 설정된 진입·해제 창과 재경보 정책 적용, 주요 경고 소실 없음 |
| ALM-02 | 동일 오류 50회 초당 발생 | 묶음 + suppressedCount 증가, 수집·감사 원본 유실 미표시 금지 |
| ALM-03 | PLC 알람과 ML 조기 경고가 동시에 입력 | 다른 타입으로 분리, PLC 알람 자동 ACK/숨김 0회 |
| ALM-04 | 운영자 ACK 후 센서 이상 지속 | ACK 기록과 원인 미해소를 구분, 정상 복귀로 오도 금지 |
| MLX-01 | UCI 447 60초 사이클 + profile 안정 flag | cycle 단위 분리, 현장 lead-time 수치 표기 0회 |
| MLX-02 | 레시피/Mode 변경 및 300초 누락 | INSUFFICIENT_DATA/DEGRADED, 잘못된 모드 경고 0회 |
| OBS-01 | 100개 OPC UA notification 중 queue overflow | GOOD 위장 금지, gap/overflow 집계·화면 표시 |
| OBS-02 | Modbus FC05/06/15/16/23 호출 시도 | 실제 패킷 전송 0회, 어댑터에서 거부 |
| OBS-03 | SQLite 오래 열린 reader + 8×1Hz writer | checkpoint/WAL/p95/누락 실측, 손실 구간 공개 |
| OBS-04 | warning 발생 이후 원시데이터 retention 만료 | evidence 상태 PARTIAL/EXPIRED, 존재하지 않는 snapshot 표시 금지 |
| SEC-01 | 인증서 신뢰목록 밖 OPC UA 연결 | 연결 거부, 비밀정보 없는 로그로 원인 진단 |
| OPS-01 | WPF 종료·Windows 재부팅·설치 rollback | 수집 재개·version 및 데이터 품질 표시, 거짓 정상 전환 금지 |

## 11. ATR/ADR(결정 로그)와 중단 기준

- **ADR-001:** .NET Worker vs Python Windows service packaging. 실제 설치/자동 시작/중단/메모리 기준으로 결정.
- **ADR-002:** SQLite (원시 저장 단일 writer) vs 외부 Historian. 측정 전 설계 변경 금지.
- **ADR-003:** EWMA vs CUSUM vs IsolationForest. 단순 모델의 false alarm/abstention/유용성 우선.
- **ADR-004:** OPC UA subscription vs Modbus poll. 장비 가용성 상한과 고객 승인 우선.
- **중단 조건:** 정규 모드/운전단계 정보가 없거나, 고객이 소스 데이터 보존을 허가하지 않고 재현이 불가능하면 조기 경보 상품 가치를 재정의. 읽기 허용이 없으면 P1 중단. 테스트되지 않은 성능/안전/ROI 주장 금지.

## 12. 외부 출처 및 적용 범위

출처의 제목·URL·주의사항은 `RESEARCH_EVIDENCE_v3_4.md`의 R01~R16을 참조한다. ISO/ISA/NIST 적용은 설계 참고이지 제3자 인증이 아니다.
