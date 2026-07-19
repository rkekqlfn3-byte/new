# JARVIS 제품 Goal 적합성 감사 보고서

- 감사일: 2026-07-19 (KST)
- 감사 기준: [docs/JARVIS_PRODUCT_GOAL.md](JARVIS_PRODUCT_GOAL.md)의 North Star·8개 수용 축과
  [docs/PRODUCT_GOAL_ACCEPTANCE.md](PRODUCT_GOAL_ACCEPTANCE.md)의 수용 게이트
- 대상 소스: 브랜치 `feature/prototype-1.0-edit-mode`,
  commit `bde4b3437d6f9e75750a59673d2137720d2e566f` + 미커밋 verification 변경 2건 (`dirty`)
- 감사 방법: 자동 수용 게이트 실제 실행, 내장 전체 자동 회귀 결과 확인, 소유 문서
  probe 증거·신선도 대조, 정적 검사, 2026-07-17 독립 감사 시정 항목 재검, 소스 직접 확인
- 이 보고서는 게이트의 개인정보 경계를 따른다. 문서 경로·선택 내용·사용자 원문은
  포함하지 않는다.

## 1. 종합 판정

**자동 검증 범위에서 제품은 Goal 8개 축에 따라 구현되어 있으며, 게이트 판정은
`environment_blocked`(종료 코드 3)다.** 이 판정은 구현 결함이 아니라 이 PC에
한글 공식 Automation 보안 모듈이 등록되어 있지 않아 앱 간 업무 축의 한글 실제
probe가 설계대로 차단된 상태를 뜻한다. Goal 문서가 요구하는 대로 게이트는 이
상태를 성공으로 과장하지 않았고, 수동 수용 3항목(화면 읽기 프로그램·음성 입력·
비숙련자 관찰)이 끝나기 전 `accepted`로 자동 승격하지도 않았다.

| 판정 요소 | 결과 |
| --- | --- |
| 전체 자동 회귀 | 950개 중 947개 통과, 실패 0, 오류 0, skip 3(외부 AI live) |
| Goal 8축 자동 판정 | 7축 `passed`, 앱 간 업무 축만 `environment_blocked` |
| 소유 문서 probe | 10개 중 9개 `passed`, `workflow_hwp`만 환경 차단 |
| 수동 수용 3항목 | 전부 `pending` (수동 증거 JSON 미제출 — 정직한 상태) |
| 정적 검사 | `compileall` 오류 0, `pip check` 이상 없음 |
| 게이트 종료 코드 | 3 (`environment_blocked`, 문서화된 값과 일치) |

## 2. Goal 축별 자동 판정 (게이트 실행: 2026-07-19 11:24 KST)

| Goal 축 | 판정 | 필수 probe | 비고 |
| --- | --- | --- | --- |
| 1. 자연어를 행동으로 변환 | passed | (회귀) | |
| 2. 현재 문맥 이해 | passed | native_excel_hwp, native_word_powerpoint | |
| 3. 안전한 네이티브 실행 | passed | 위 2개 + four_app_stability, excel_vba, hwp_workflow_watchdog | |
| 4. 성공 작업 재사용 | passed | user_learning | |
| 5. 여러 앱 업무 연결 | **environment_blocked** | workflow_word, workflow_hwp, workflow_both | `workflow_hwp`만 차단 |
| 6. 사용자 방식 학습 | passed | user_learning | |
| 7. 실패 책임 분류와 대응 | passed | failure_diagnosis, hwp_workflow_watchdog | |
| 8. 비숙련자 접근성 | passed | (회귀) | 자동 범위 한정, 수동 3항목 별도 |

probe 증거 신선도: 가장 오래된 probe(four_app_stability, excel_vba,
failure_diagnosis)가 2026-07-17 생성으로 게이트의 7일 규칙 이내다. workflow_word와
user_learning은 감사 당일(2026-07-19) 재생성됐다.

`workflow_hwp` 차단의 구조화 진단: `component=hwp_automation_security_module`,
`retryable=true`, `automatic_install_attempted=false`, 공식 설치 가이드 URL 보존.
Goal 문서의 "환경 차단을 성공이나 일반 구현 실패로 뭉개지 않는다"는 계약을
정확히 지켰다.

## 3. 최신 기능 증거 확인

감사 당일 09:01에 재생성된 Stage 11 probe 보고서에서 게이트가 요구하는 필수 검사
전부(승인 선호 충돌 해결, 내용 없는 워크플로 스킬 저장·새 출력 계획·실제 재생,
보고서 전용·발표자료 전용 레시피, 번호 후보 해석, 최근 산출물 열기·포커스·편집
세션 인계와 후속 편집 read-back, 명시적 선택 범위 워크플로 등)가 `true`로
기록된 것을 확인했다. 미커밋 변경으로 게이트에 추가된
`presentation_only_workflow_skill_actual_replay_verified`도 실제 probe 증거가
존재하고 통과한다. `user_documents_modified=false`,
`paths_or_contents_reported=false`, 소유 프로세스 정리 검증도 성립한다.

## 4. 2026-07-17 독립 감사 시정 항목 재검

| 항목 | 재검 결과 |
| --- | --- |
| P1-3 전송 전 문맥 미동기화 | 종결 확인 — 공유 Promise·요청 fingerprint 계약 존재 |
| P1-4 승인 전 workflow 영구 저장 | 종결 확인 — 승인 계약·상태 v6 이관 검증이 probe 필수 검사에 포함 |
| P2-1 PPT 파일명 5장 고정 | 종결 확인 — `5장_요약` 하드코딩 소스에서 소멸 |
| P2-4 문서·버전 불일치 | 종결 확인 — 과거 감사 문서는 역사 기록으로 격리, 개인 경로 중립화 |
| **P1-2 1초 COM polling 제거** | **부분 재발 — 발견 사항 F2 참조** |

## 5. 발견 사항

### F1 (중) — 게이트 강화 변경 2건이 미커밋 상태

`verification/product_goal_acceptance.py`(발표자료 전용 재생 필수 검사 추가)와
`verification/prototype11_stage11_probe.py`(해당 probe 구현, +159줄)가 작업
트리에만 존재한다. 이번 게이트 보고서도 `dirty=true`로 남았다. 증거는 통과했지만
감사 결과를 재현 가능한 소스 신원에 고정할 수 없다. 이는 직전 독립 감사 P1-1
(소스 신원 미고정)과 같은 유형의 위험이 작게 반복된 것이다.

**권고**: 두 파일을 즉시 커밋하고 clean commit에서 게이트를 한 번 재실행해
`dirty=false` 보고서를 보존한다.

### F2 (중) — 편집 문맥 700ms 반복 조회와 rc.6 변경 기록의 불일치

CHANGELOG `1.1.0-rc.6`은 독립 감사 P1-2 시정으로 "편집 문맥의 1초 COM polling을
제거하고 연결·탭 진입·focus·수동 새로고침 등에서만 수행"한다고 기록했다. 그러나
이후 focus 복구·직접 수정 관찰·선택 overlay 기능(commit `34370b3`)에서
`gui/js/edit_mode.js`의 `syncEditContextMonitor`가 **700ms 주기** 타이머로
재도입됐고, 이 타이머는 편집 모드·문서 연결 상태에서 `get_edit_context` →
`EditContextManager.capture`(호출마다 새 COM 읽기, 캐시·조절 없음)를 반복
호출한다.

완화 요소는 있다: 페이지가 숨겨지면 건너뛰고, 공유 Promise로 중복 호출을 막고,
현재 Goal 문서도 "상태 polling은 사용자가 보고 있는 창을 바꾸지 않는다"고 폴링
존재를 전제한다. 직접 수정 관찰(5분 유예)과 선택 overlay는 주기 조회 없이
성립하기 어렵다. 따라서 이는 기능 결함이 아니라 **시정 기록과 현재 설계의 문서
불일치**이며, 원래 폴링(1초)보다 빨라진 주기가 의도된 결정인지 근거가 없다.

**권고**: (a) 모니터의 목적·주기·중단 조건을 KNOWN_LIMITATIONS 또는 Goal 문서에
의도된 설계로 명시하고 CHANGELOG rc.6 항목과의 관계를 정리하거나, (b) 직접 수정
관찰이 필요한 유예 구간에만 주기 조회를 한정하는 이벤트 기반 구조로 되돌린다.

### F3 (환경) — 한글 보안 모듈 부재로 앱 간 업무 축 차단

이 PC의 `HKCU\Software\HNC\HwpAutomation\Modules`에 공식 파일 접근 보안 모듈
등록이 없어 `workflow_hwp` probe가 실행 전 차단된다. JARVIS는 설계대로 자동
설치·레지스트리 변경을 하지 않는다. 사용자가 한컴 공식 가이드에 따라 모듈을
설치·등록해야 한글 실제 생성 증거를 갱신하고 이 축이 `passed`가 될 수 있다.

### F4 (수용 경계) — 수동 수용 3항목 미수행

NVDA 등 실제 화면 읽기 프로그램, 실제 음성 입력, 비숙련 사용자 관찰 시험의 수동
증거 JSON이 없다. 게이트는 이를 `pending`으로 정직하게 유지한다. 자동 증거가 모두
통과해도 이 3건 전에는 `accepted`가 될 수 없다.

### F5 (잔여 범위, 기록 확인)

KNOWN_LIMITATIONS에 이미 문서화된 상태 그대로다: Excel VBA 개체 모델 접근 보안
설정 대기, 외부 AI 실계정 live 테스트 3건 skip, 실행 파일 Authenticode 미서명
(현 개발 단계는 Python 소스 실행이라 영향 없음). 문서와 실제 상태의 불일치는
발견하지 못했다.

## 6. 결론

North Star("너네가 나를 배워, 너네 실수도 너네가 고쳐")의 핵심 계약 — 검증 가능한
네이티브 실행, 승인 경계, 검증된 성공만 재사용, 원문을 남기지 않는 선호 학습,
결정적 실패 분류, 환경 차단의 정직한 구분 — 이 자동 증거로 일관되게 성립한다.
Goal 문서의 "현재 구현 증거" 주장과 실제 probe·회귀 결과 사이에서 과장은 발견되지
않았고, 오히려 게이트가 성공을 보수적으로 세는 방향으로 동작한다.

현재 판정 `environment_blocked`는 제품 결함 0건 상태에서 환경 조치(F3)와 수동
수용(F4)만 남았다는 뜻이다. 코드 관점의 조치는 F1(커밋·clean 재실행)과
F2(문서 정합화 또는 구조 재조정) 두 건이다.

## 7. 시정 결과 (2026-07-19, 감사 당일)

- **F1 종결**: 게이트 필수 검사 추가와 Stage 11 probe 구현을 정리해 커밋했다
  (`7c0e3f0`). 새 필수 검사는 수용 게이트 문서에도 명시했다. 정리 내용은 중복된
  내용 없는 저장소 검사·template 재생 준비의 모듈 헬퍼 추출과 보고서 실행기
  대역 클래스의 함수 밖 이동이며, 정리한 코드로 probe를 재실행해 전체 검사
  통과와 소유 프로세스 정리를 재확인했다.
- **F2 종결(재문서화)**: 편집 문맥 0.7초 모니터를 의도된 설계로 확정하고 목적·
  경계·rc.6 폴링 제거와의 관계를 KNOWN_LIMITATIONS와 소스 주석에 기록했다
  (`266ca42`). 모니터 동작 자체는 바꾸지 않았다.
- **재검증**: 전체 회귀 unit 399·integration 345·windows 203 전부 통과, 실패 0.
  clean commit `266ca42`에서 게이트를 재실행해 `dirty=false`, 종료 코드 3,
  8축 판정 동일(7축 passed, 앱 간 업무 축 environment_blocked)을 확인했다.
- **잔여**: F3(한글 공식 Automation 보안 모듈 설치·등록)과 F4(수동 수용 3항목)는
  사용자 조치 항목으로 남는다.
