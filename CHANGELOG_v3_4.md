# Aegis v3.3 → v3.4 주요 변경

- 외부 출처 16개 매핑: ISA 알람 관리, UCI Hydraulic 실험 데이터, OPC UA sampling/overflow/TrustList, Modbus FC 읽기 허용목록, SQLite WAL, OpenTelemetry, OT 보안.
- 경고·알람 분리, 지속·해제/hysteresis 및 episode clustering, 고객 오경보 KPI를 후속 단계 요구사항으로 추가.
- `EvidencePacket`의 버전·근거·품질·보존 한계를 모델/Trend와 연결.
- UCI 실험 데이터와 가상/현장 실측을 다르게 등급화. RUL/lead-time 오용 방지.
- OT read-only의 CPU/네트워크 영향, OPC 통지 overflow와 Modbus FC23 차단 등 명문화.
- D-demo 컷라인 유지, D1/D2/P1에 개선 사항 배치. 외부 참고 문서의 표준/성능 인증은 주장하지 않음.
