# JARVIS Goal Phase 2 구현·검증 보고서

작성일: 2026-07-23

범위: 구조화된 되물어보기

## 결과

정보가 빠진 요청을 위험 승인과 구분해 질문하고, 사용자가 보완한 문장을 같은
세션·실행 흐름에서 다시 해석하도록 구현했다. 보완 답변 전에는 외부 상태를
변경하지 않으며 활성 문서나 시트가 바뀐 오래된 답은 폐기한다.

## 고정 사유 코드

`missing_target`, `missing_range`, `missing_destination`,
`missing_sort_key`, `missing_filter_condition`, `multiple_possible_intents`,
`ambiguous_reference`, `multiple_documents`, `multiple_sheets`,
`context_mismatch`, `unsafe_default`만 허용한다.

## 적용 범위

- Excel 합계·평균: 범위와 결과 셀 누락 질문.
- Excel 필터: 열·조건 누락 질문.
- Excel 정렬: 기준 열·방향 누락 질문.
- Excel 중복 머리글과 표시 방식 선택.
- 한글 찾기·바꾸기 범위 선택.
- UI Automation 다중 후보 선택.

Word·PowerPoint는 현재 구조화 어댑터가 선택 Range/Shape를 직접 검증하며 후보
목록을 만들 수 없는 상태에서는 기존처럼 안전 차단한다. 임의 후보 선택은 하지
않는다.

## 안전 계약

- 정보 보완 선택지는 실행 승인 또는 위험 선택지로 표시할 수 없다.
- 추천 선택은 설명·포커스만 바꾸며 후속 쓰기 승인을 생략하지 않는다.
- 질문은 session ID, execution ID, request ID로 격리되고 한 번만 소비된다.
- 자유 문장 답변은 기존 실행 슬롯을 재개한 뒤 전체 로컬 라우팅과 안전 판단을
  다시 거친다.
- 통합문서·시트의 내용 없는 문맥 지문이 달라지면 `context_changed`로 중단한다.

## 검증

- 전체 회귀: 971건 실행, 968건 통과, 외부 AI 3건 skip, 실패·오류 0.
- 성능·파서 정확도·편집 라우팅 집중 검사: 18건 통과.
- 합계 보완 후 동일 실행 ID 완료, 평균 `AVERAGE` 수식, 필터·정렬 사유 코드,
  시트 변경 후 무변경 차단을 자동 검사했다.
