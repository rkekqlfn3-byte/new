# JARVIS Goal Phase 5 1,000문장 자동 수용 중간 보고서

작성일: 2026-07-23

상태: 자동 해석 배터리 완료, 소유 문서 실앱·사용자 재시험 대기

## 자동 배터리 구성

| 유형 | 건수 | 검증 내용 |
|---|---:|---|
| 직접 명령 | 400 | 앱과 행동의 정확한 구조화 |
| 정보가 빠진 명령 | 200 | 대상을 추측 실행하지 않고 보완 필요 판정 |
| 문맥·선택 참조 | 150 | 같은 표현을 서로 다른 Excel 선택 범위에 결합 |
| 오타·구어체 | 100 | 격리된 앱 사전의 제한적 오타 보정 |
| 복합 요청 | 100 | 두 앱 행동의 순서와 대상 보존 |
| 위험·승인·Undo | 50 | 셸 차단, 문맥 없는 쓰기·Undo 실행 차단 |

## 결과

- 총 1,000건 중 1,000건 기대 판정 통과.
- crash 0건, wrong action 0건.
- 위험 문장 안전 차단 50/50.
- 무승인 쓰기 0건, 잘못된 앱·문서·범위 실행 0건.
- 외부 AI 호출 0건, 실제 앱 실행 0건, 사용자 데이터 접근 0건.
- 동일 문장 10회 반복 결과가 같고 AI 호출 수가 증가하지 않음.
- 실패 보고 계약은 문장·문서 내용·경로를 저장하지 않고 케이스 ID와 고정 분류만
  저장함.

## 구현 위치

- 배터리: `verification/utterance_acceptance_battery.py`
- 자동 회귀: `tests/unit/test_utterance_acceptance_battery.py`
- 제품 Goal 연결: `verification/product_goal_acceptance.py`
- 생성 증거: `verification/utterance_acceptance_report.json`(Git 제외)

## 남은 Phase 5

- 기존 100문장 사용자 재시험에서 중대 오대상·무승인 쓰기 0건 확인.
- JARVIS 소유 임시 Excel·Word·한글·PowerPoint 문서 probe의 최신 증거 갱신.
- 한글 공식 보안 모듈이 준비된 뒤 HWP 포함 업무 흐름 재검증.

자동 배터리는 실제 사용자의 화면 읽기·음성 입력·비숙련자 관찰을 대신하지 않는다.
따라서 현재 상태를 Prototype Goal 최종 수용으로 표시하지 않는다.
