# Aegis Insight v3.4 — 조사 근거와 기술 선택 기록

**기준일:** 2026-10-16. 기존 v3.3의 Trend·Logs·ML 조기탐지·상용 경계 유지. **외부 근거**, **현 단계 설계 가설**, **실제 검증 결과**는 구분한다. 외부 링크의 내용은 설계 아이디어에 사용했으며 실제 라이선스/버전은 개발 착수 시 재확인한다. 확인한 웹 자료의 결과를 프로그램이 실제 수행했다고 주장하지 않는다.

## 1. 조사로 추가된 핵심 판단

1. **알람 관리가 없는 ML은 운영자에게 새로운 소음이 될 수 있다.** ML의 조기 경고를 PLC 안전 알람과 구분하고, 경고의 지속·해제·중복·원인 관련성을 추적한다 [R01, R02, R03].
2. **현장 장비와 더 가까운 실험 데이터 확보:** UCI Hydraulic 447은 실제 유압 테스트 벤치로 압력·유량·온도를 포함한다. 단, 라벨은 60초 운전 사이클 단위의 조건이며 field run-to-failure/RUL 자료가 아니다 [R04, R05].
3. **읽기 전용도 영향이 0이라는 뜻이 아니다.** polling·subscription 부하, 인증서 검증, 데이터 누락과 불확실성을 모두 계측해야 한다 [R07~R10].
4. **경고 원인 추적성을 우선한다.** 로그량을 늘리는 것보다 경고 시점의 원시/집계·모드·모델·품질·사용자 ACK를 하나의 evidence package로 묶는다 [R11, R12, R15].
5. **첫 데모는 더 커지지 않는다.** DEG-02/EWMA, WPF Trend 1화면, 경고·ACK가 데모 컷라인. 고급 알람 KPI, 외부 데이터 비교, OPC 연동은 D1/D2/P1 이후다.

## 2. 기존 v3.3 대비 채택·보류·제외

| 기능/기술 | 결정 | 판단 근거 | 적용 Gate |
| --- | --- | --- | --- |
| Warning vs Alarm 계층 | 채택 | 안전 알람과 ML advisory 혼동 금지 | D0b/D1 |
| 지속·해제 hysteresis 및 재경보 cool-down | 채택 | 빈번한 깜빡임·오경보 억제 | D1 |
| Alert Episode·관련 신호/사건 묶음 | 채택 | warningId로 시간축·원인 파악 | D1 |
| Evidence packet 재현 정보 | 채택 | 모델+품질+설정+시간창 재구성 | D1/D2 |
| 외부 UCI Hydraulic 447 | 채택: 평가용 | 실험 장비 데이터로 ML 파이프라인 점검 | D2 |
| C-MAPSS | 보조 | 대상 도메인 차이; 벤치마크 비교만 | D2 optional |
| OPC UA 인증서·sampling overflow | 채택 | OT 승인 후 필수 | P1 |
| InfluxDB/TimescaleDB | 측정 전 보류 | SQLite 병목 증거가 먼저 | D1 재평가 |
| Grafana/OTel Collector 서버 | 최초 릴리스 제외 | 오프라인/설치/지원 부담 | 대형 고객 요청 후 |
| 자동 원인 확정·자가 PLC 조치 | Insight 제외 | 증거 불충분, 제품 읽기 전용 | Assist 별도 |

## 3. 고객 요구 검증 질문 (실제 인터뷰 전 가설)

- 현장에서 가장 늦게 파악하는 문제는 유량·압력·전류 변화 중 무엇이며, **알람 전 히스토리**가 남아 있는가?
- 장비 가동 모드/레시피와 목표값이 바뀌는 시점의 데이터·알람을 식별할 수 있는가?
- 운영자가 하루에 처리하는 경고 건수, 무시하는 경고 비중, 허용 가능한 추가 경고 건수는?
- 현장 PC가 인터넷 없이 운영되는가? 원격 접속/로그 내보내기는 고객 승인·암호화가 가능한가?
- 공급 PLC·SCADA에 읽기 접근 시 허용하는 폴링/구독 수, 서버 CPU/대역폭/재연결 정책은?
- 장애 대응 시 실제로 필요한 화면: 이벤트 first-out, 10분 전 trend, 비교 기간, 모델 근거, CSV 내보내기 중 무엇인가?

**검증 원칙:** 5~8명 정도의 역할별 인터뷰를 초기 가설로 계획하되, 접근 가능성이 없으면 공개 의견은 요구사항 가설로 유지. 고객 이메일·회사 내부 로그·현직 고객 명단 활용 금지.

## 4. 출처·적용 근거 레지스터

| ID | 출처 유형 | 기관/자료 | 설계에 적용 | 주의 |
| --- | --- | --- | --- | --- |
| R01 | Industry standard | ISA alarm management lifecycle | 경고 우선순위·기록·변경관리·교육·감사 | 적용방향; 적합성 인증 아님 |
| R02 | Industry guidance | ISA alarm suppression and alarm flood | 반복·연쇄 알람과 억제 수단은 신중히 구분 | 고위험 억제 자동화 금지 |
| R03 | User opinion | Reddit r/PLC SCADA UX experiences | 현장에선 단순한 UI, 알람 이력, 장면에 맞는 Trend 요구 | 일부 익명 경험, 일반화 금지 |
| R04 | Experimental dataset | UCI hydraulic condition monitoring (ID 447) | 유압 시험장치 다중센서, 60초 주기 2205 cycle, 압력·유량·온도 | 고장까지 남은 시간의 연속 실측 아님 |
| R05 | Data license | UCI dataset CC BY 4.0 | 공개 데이터 사용/수정 시 출처 표시 조건 | 현장 제품 모델 학습 데이터 사용은 별도 검토 |
| R06 | Dataset comparator | NASA PCoE data repository | C-MAPSS는 엔진 열화 시뮬레이션, 대상 도메인 차이 | 대상 설비 조기 진단 성과로 홍보 금지 |
| R07 | OT security | NIST SP 800-82 Rev.3 | 운영 설비의 가용성·성능·민감도를 고려한 IT/OT 보안 | 실연결 전 서면 승인 및 부하 기준 필요 |
| R08 | OPC spec | OPC UA MonitoredItem / sampling / overflow | revised interval·queue overflow·통지 누락 반영 | 초기 Mock만; 라이브 P1 |
| R09 | OPC security | OPC UA certificate trust list | 신뢰하지 않는 인증서 연결 거부 | 자동 모두-신뢰 정책 금지 |
| R10 | Modbus spec | Modbus function codes | read FC01~04 vs write FC05,06,15,16 | FC23도 read/write 혼합이므로 차단 |
| R11 | Storage | SQLite WAL design | single writer·checkpoint·long-lived reader 주의 | 용량/지연 실제 측정 후 대안 판단 |
| R12 | Observability | OpenTelemetry Logs spec | traceId 기반 로그 연관 관계 | Collector 서버 도입 필수 아님 |
| R13 | Windows runtime | Microsoft .NET Worker as Windows service | 서비스 자동 시작·종료·Event Log 지원 | 고객 Windows 이미지·권한 검증 |
| R14 | ML API | scikit-learn IsolationForest | raw decision/score 확률 아님 | percentile/calibration version 노출 |
| R15 | Time-series architecture | InfluxDB downsample patterns | 집계는 별도 보존층, 늦은 데이터/보존 이전 처리 | InfluxDB 서버 즉시 도입하지 않음 |
| R16 | Secure product | CISA Secure-by-Design | 제품 공급자 보안 책임·공급망 점검 | 인증 또는 적합성 보증 아님 |

- **[R01]** ISA alarm management lifecycle: https://www.isa.org/intech-home/2018/march-april/features/alarm-management-life-cycle
- **[R02]** ISA alarm suppression and alarm flood: https://www.isa.org/intech-home/2017/september-october/features/from-managing-to-optimizing-alarms
- **[R03]** Reddit r/PLC SCADA UX experiences: https://www.reddit.com/r/PLC/comments/x2mx4a/whats_that_extra_thing_you_do_to_make_your_scada/
- **[R04]** UCI hydraulic condition monitoring (ID 447): https://archive.ics.uci.edu/dataset/447/condition+monitoring+of+hydraulic+systems
- **[R05]** UCI dataset CC BY 4.0: https://archive.ics.uci.edu/dataset/447/condition+monitoring+of+hydraulic+systems
- **[R06]** NASA PCoE data repository: https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/
- **[R07]** NIST SP 800-82 Rev.3: https://www.nist.gov/publications/guide-operational-technology-ot-security
- **[R08]** OPC UA MonitoredItem / sampling / overflow: https://reference.opcfoundation.org/specs/OPC-10000-4/5.13
- **[R09]** OPC UA certificate trust list: https://reference.opcfoundation.org/specs/OPC-10000-4/6.1
- **[R10]** Modbus function codes: https://www.modbus.org/introduction-to-modbus
- **[R11]** SQLite WAL design: https://www.sqlite.org/wal.html
- **[R12]** OpenTelemetry Logs spec: https://opentelemetry.io/docs/specs/otel/logs/
- **[R13]** Microsoft .NET Worker as Windows service: https://learn.microsoft.com/ko-kr/dotnet/core/extensions/windows-service
- **[R14]** scikit-learn IsolationForest: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- **[R15]** InfluxDB downsample patterns: https://docs.influxdata.com/influxdb/v2/process-data/common-tasks/downsample-data/
- **[R16]** CISA Secure-by-Design: https://www.cisa.gov/resources-tools/resources/secure-demand-guide
