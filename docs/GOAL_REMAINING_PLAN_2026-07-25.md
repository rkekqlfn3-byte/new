# JARVIS Goal 잔여 작업 계획서

- 기준일: 2026-07-25 (KST)
- 기준 브랜치: `feature/prototype-1.0-edit-mode`
- 기준 커밋: `ddc7239ade4847a6b93c76b352b97b816f1d4465`
- 마지막 게이트 판정: `environment_blocked` (2026-07-23 05:58 생성)
- 실행 기준: Python 소스만 사용한다. 별도 지시 전에는 EXE를 만들지 않는다.
- 상위 기준: [Goal 달성 마스터 계획](JARVIS_GOAL_COMPLETION_MASTER_PLAN_2026-07-23.md),
  [제품 Goal 수용 게이트](PRODUCT_GOAL_ACCEPTANCE.md)

이 문서는 마스터 계획의 대체가 아니라, 2026-07-25 시점에서 **실제로 남아 있는
것만** 추린 실행 계획서다.

## 1. 현재 위치

| Phase | 범위 | 상태 |
|---|---|---|
| 0 | 기준선 고정 | 완료 |
| 1 | 사용자용 실행 설명 계층 | 코드·자동검증 완료, GUI 수동 확인만 Phase 7과 병합 |
| 2 | 구조화된 되물어보기 | 완료 |
| 3 | Undo UI 연결 | 완료 (실화면 확인은 Phase 7) |
| 4 | Excel 안정화 | A군 자동검증 완료, **100문장 수동 시험 미실시**, B군 미착수 |
| 5 | 1,000문장 배터리 | 자동 배터리 통과(1000/1000), **실앱·사용자 재시험 미실시** |
| 6 | 한글 공식 보안 모듈 | **사용자 환경 조치 대기** (미설치 확인됨) |
| 7 | 수동 수용 3종 | GUI 사전 점검만 완료, **3종 모두 pending** |
| 8 | 최종 clean 수용 게이트 | 미실행 |

자동 회귀는 986건 실행 / 실패·오류 0 / 외부 AI 3건 skip이다. Goal 8축 중 7축
`passed`, `cross_app_workflow`만 `environment_blocked`다.

**즉, 코드 작업은 사실상 끝났고 남은 것은 증거·환경·사람 세 종류다.**

## 2. 이번 점검에서 새로 확인한 차단 요소

`verification/product_goal_acceptance.py`의 probe 신선도 기준은 168시간(7일)이다.
2026-07-25 13:12 기준으로 기존 증거는 다음처럼 만료된다.

| probe | 생성 시각 | 만료 시각 | 현재 |
|---|---|---|---|
| `four_app_stability` | 07-17 11:31 | 07-24 11:31 | **만료** |
| `excel_vba` | 07-17 11:32 | 07-24 11:32 | **만료** |
| `failure_diagnosis` | 07-17 11:33 | 07-24 11:33 | **만료** |
| `native_excel_hwp` | 07-18 19:31 | 07-25 19:31 | 오늘 만료 |
| `native_word_powerpoint` | 07-18 19:50 | 07-25 19:50 | 오늘 만료 |
| `workflow_both` | 07-18 18:55 | 07-25 18:55 | 오늘 만료 |
| `hwp_workflow_watchdog` | 07-18 22:08 | 07-25 22:08 | 오늘 만료 |
| `workflow_hwp` | 07-18 23:08 | 07-25 23:08 | 오늘 만료(환경 차단 상태) |
| `workflow_word` | 07-19 08:56 | 07-26 08:56 | 내일 만료 |
| `user_learning` | 07-19 11:37 | 07-26 11:37 | 내일 만료 |
| `utterance_acceptance` | 07-23 05:57 | 07-30 05:57 | 유효 |

결론 두 가지다.

1. 지금 게이트를 그대로 실행하면 `environment_blocked` 이전에 `automated_failed`
   사유(`probe_report_stale`)가 3건 추가된다.
2. 실앱 probe는 **최종 판정과 같은 주에 한 번에 몰아서** 다시 만들어야 한다.
   미리 만들어 두고 사람 수용을 몇 주 뒤에 하면 다시 만료된다.

또 하나: `refresh_probes()`는 probe가 하나라도 0이 아닌 종료 코드를 내면 즉시
`RuntimeError`로 중단한다. 한글 보안 모듈이 없는 상태에서 `--refresh-probes`를
쓰면 `prototype11_stage10_probe --report-format hwp`에서 멈추고 그 뒤의
`both`·Stage 11·Stage 12 증거가 갱신되지 않는다. **한글 환경이 해결되기 전까지는
전체 refresh를 쓰지 말고 개별 probe를 순서대로 실행한다.**

## 3. Goal까지 남은 작업 (5개 묶음)

### R1. Excel 100문장 수동 시험 (Phase 4·5 잔여)

- 도구: `docs/manual_tests/JARVIS_100문장_수동테스트_체크표.xlsx`,
  [직접 시험 문장 100개](PROTOTYPE1_MANUAL_TEST_100.md)
- 실행: `jarvis_start`(Python 소스)로 JARVIS 소유 임시 Excel 문서에 100문장을
  순서대로 입력하고 체크표에 판정만 기록한다.
- 기록 항목: 대상 정확성, 승인 요구 여부, read-back 성공, Undo 노출 정확성,
  실패 분류 적절성.
- 완료 조건: 중대 오대상 0건, 무승인 쓰기 0건, `verified=false`를 성공으로
  표시한 사례 0건. 발견 결함은 수정 → 관련 회귀 → 해당 문장 재시험까지 끝낸다.
- 금지: 사용자 실제 문서·실행 중 Office 인스턴스 사용. 체크표에 문서 내용·경로
  기록.
- 예상: 0.5~1일 (결함 수정 시 +α)

### R2. 실앱 probe 증거 재생성 (신선도 회복)

- 대상: 2절 표에서 만료·임박 상태인 probe 전부.
- 실행(한글 환경 미해결 상태에서는 개별 실행):

  ```powershell
  python -m verification.prototype1_stage5_probe
  python -m verification.prototype1_stage6_probe
  python -m verification.prototype1_stage8_probe --timeout 600
  python -m verification.prototype11_stage9_probe --timeout 600
  python -m verification.prototype11_hwp_watchdog_probe --timeout 600
  python -m verification.prototype11_stage10_probe --report-format word --output verification/prototype11_stage10_word_report.json --timeout 900
  python -m verification.prototype11_stage10_probe --report-format both --output verification/prototype11_stage10_both_report.json --timeout 900
  python -m verification.prototype11_stage11_probe --timeout 900
  python -m verification.prototype11_stage12_probe
  ```

- 사전 조건: 사용자 Excel·Word·한글·PowerPoint 프로세스를 모두 종료한다.
  probe는 소유 인스턴스가 아니면 수용하지 않는다.
- 완료 조건: 각 보고서 `success=true`, 소유 프로세스 정리 확인, 사용자 문서
  변경 0.
- 주의: 이 작업은 **R4(사람 수용) 직전이나 직후 7일 안**에 해야 의미가 있다.
- 예상: 0.5일 (Stage 8의 앱별 100회 안정성 때문에 수 시간 소요 가능)

### R3. 한글 공식 Automation 보안 모듈 (Phase 6) — 사용자 조치

현재 `HKCU\Software\HNC\HwpAutomation\Modules`에 실제 파일이 존재하는 공식 등록
이름이 없다. JARVIS는 설치·등록·레지스트리 변경을 자동으로 하지 않는다.

2026-07-19에 사용자는 이 모듈 설치를 **보류**하기로 결정했다. 주 용도인 한글
문서 편집(연결·선택 교체·서식·사용자 저장)은 모듈 없이 동작하고, 막히는 것은
워크플로의 한글 보고서 자동 생성뿐이기 때문이다. 다만 **Prototype 수용 도장을
찍으려면 이 결정을 다시 봐야 한다.** 아래 두 가지 때문이다.

1. `accepted` 판정은 `cross_app_workflow` 축을 요구하고, 이 축의 필수 probe는
   `workflow_word`, `workflow_hwp`, `workflow_both` 셋이다.
2. `workflow_both`는 현재 `passed`이지만 2026-07-18 18:55 생성분이며, 한글 보안
   모듈 사전 검사가 추가되기 전 결과다. `both` 순서에는 `create_hwp_report`가
   포함되므로 R2에서 재실행하면 **함께 실패한다.**

즉 `workflow_hwp` 하나만 제외해도 `accepted`에 도달하지 못한다.

경로 A — 해결하고 수용:

1. 한컴 공식 Automation 안내(https://developer.hancom.com/hwpautomation)에 따라
   파일 접근 보안 모듈을 사용자가 직접 설치·등록한다.
2. JARVIS를 다시 시작한다.
3. `python -m verification.prototype11_stage10_probe --report-format hwp --output verification/prototype11_stage10_hwp_report.json --timeout 900`
   와 `--report-format both`를 다시 실행한다.
4. 생성·read-back·부분 파일 부재·소유 프로세스 정리를 확인한다.
5. 이후에는 `--refresh-probes` 전체 실행이 가능해진다.

경로 B — 축을 Word·PowerPoint 범위로 재정의:

- `cross_app_workflow`의 필수 probe에서 `workflow_hwp`를 제거하고,
  `workflow_both`를 한글이 없는 조합으로 교체하는 코드 변경을 별도 커밋으로
  남긴다. **두 probe를 모두 다뤄야 하며 `workflow_hwp` 하나만으로는 부족하다.**
- `KNOWN_LIMITATIONS.md`에 제외 범위·사유·재개 조건을 적는다.
- 태그 설명에 "한글 보고서 생성 제외"를 명시해 무엇을 빼고 받은 도장인지
  남긴다.
- 우회·자동 설정 변경으로 통과시키지 않는다.

**이 둘 중 하나를 정하기 전에는 `accepted`가 나올 수 없다.** 경로 A는 수용
기준을 낮추지 않고 코드 변경도 없으므로 권장한다.

- 예상: 경로 A는 사용자 환경 준비 시간 + probe 0.5일, 경로 B는 0.5~1일.

### R4. 수동 수용 3종 (Phase 7) — 사람이 해야 함

Phase 7 사전 점검에서 GUI 연결·ARIA·포커스 복귀는 이미 확인했다. 남은 것은
자동화가 대신할 수 없는 3개다.

1. **화면 읽기 프로그램(NVDA 등)**
   - 명령 입력 → 모드 전환 → 확인 카드 → 오류 → 완료 → Undo 흐름 수행.
   - 기록: 낭독 순서, 중복 낭독, 포커스 손실, 버튼 이름.
   - 사전 점검에서 미판정으로 남긴 **Tab 순서**를 실제 브라우저에서 재확인한다.
2. **실제 음성 입력**
   - 날짜·날씨·앱 열기·Excel 편집·확인 응답·Undo 대표 문장 수행.
   - 오인식 문장이 자동 실행되지 않고 수정·재질문이 가능한지 확인.
3. **비숙련 사용자 관찰**
   - 대표 20문장을 도움 없이 수행, 막힌 항목만 100문장으로 확장.
   - 측정: 첫 성공 시간, 질문 횟수, 잘못된 실행, 도움 요청, 포커스 혼란,
     오류 안내 이해 여부.
   - 함께 확인할 Phase 1 잔여 4항목: 카드 1장 갱신, 승인 카드와 상태 카드
     겹침 없음, 작은 창에서 가로 스크롤 없음, 상태 변경 1회 낭독.

기록 방법:

```powershell
copy verification\product_goal_manual_acceptance.example.json verification\product_goal_manual_acceptance.json
```

실제로 끝낸 항목만 `passed`와 수행 시각으로 바꾼다. 메모·원문·문서 내용은 이
JSON과 Git에 넣지 않는다.

- 완료 조건: 3항목 모두 `passed`. 발견 결함은 수정 → 관련 회귀 → 해당 수동
  시나리오 재시험까지 끝낸다.
- 예상: 1~3일 (사용자 일정 의존)

### R5. 최종 clean 수용 게이트 (Phase 8)

순서를 지킨다.

1. CRLF·문서·버전·테스트 등록(`tests/test_runner.py`) 정합성 검사.
2. 작업 트리 clean 확인 (`source.dirty=false`가 필수).
3. 전체 자동 회귀 실행, 실패·오류 0.
4. R2의 실앱 probe 증거가 7일 이내인지 재확인.
5. R4의 수동 증거 JSON 연결.
6. 게이트 실행:

   ```powershell
   python -m verification.product_goal_acceptance --manual-evidence verification/product_goal_manual_acceptance.json
   ```

7. `overall_status=accepted`, 종료 코드 0, 보고서 commit = HEAD 확인.
8. `Prototype Goal accepted` 태그와 `verification/create_source_archive.ps1`
   소스 백업 생성, 원격 `main`·개발 브랜치·태그 교차 확인.

- 예상: 1일

## 4. 이월 결정 대상 (Goal 필수 아님)

Phase 4 B군 4종 — 중복 제거, 행·열 삭제, 시트 생성·이름 변경, 열 너비·행 높이.
마스터 계획대로 **다음 주기로 이월을 권장한다.** 지금 착수하면 A군 재검증과
1,000문장 재실행이 다시 필요해지고, 만료 7일 시계 때문에 실앱 probe를 또 새로
만들어야 한다. 차트·조건부 서식·피벗 확장은 이번 완료 조건에서 제외된 상태를
유지한다.

## 5. 권장 실행 순서

R3(한글 환경)은 사용자 환경 작업이므로 다른 작업과 병행할 수 있다. 나머지는
만료 시계 때문에 **뭉쳐서** 진행해야 한다.

```text
[병행 시작] R3 한글 보안 모듈 설치 또는 범위 제외 결정
     │
1일차 R1 Excel 100문장 수동 시험 → 결함 수정 → 관련 회귀
     │
2일차 R4 수동 수용 3종 (화면 읽기 → 음성 → 비숙련자 관찰)
     │       결함 발견 시 수정 → 회귀 → 해당 시나리오만 재시험
     │
3일차 R2 실앱 probe 전량 재생성 (사용자 Office 종료 상태)
     │
3일차 R5 전체 회귀 → 게이트 실행 → accepted → 태그·아카이브
```

핵심 규칙: **R2와 R5는 같은 날 붙인다.** probe를 먼저 만들고 사람 수용을 나중에
하면 168시간 안에 다시 만료된다.

## 6. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 한글 보안 모듈 미설치 지속 | `accepted` 불가 | R3 경로 B(명시적 범위 제외) 결정 |
| 실앱 probe 만료 재발 | 게이트 `automated_failed` | R2를 R5와 같은 날 실행 |
| 수동 시험에서 P0 발견 | 신규 작업 전면 중단 | 원인 재현 → 수정 → 전체 관련 회귀 우선 |
| Stage 8 100회 안정성 장시간 | 하루 일정 초과 | `--timeout` 상향, 다른 Office 작업과 시간 분리 |
| B군 착수로 A군 재검증 발생 | 수용 지연 | 이월 유지 |

## 7. 최종 판정 체크리스트

- [ ] Excel 100문장 수동 시험: 중대 오대상 0, 무승인 쓰기 0
- [ ] 실앱 probe 11종 전부 7일 이내 `success=true`
- [ ] `workflow_hwp` = `passed` 또는 문서로 명시된 범위 제외
- [ ] `screen_reader` = `passed`
- [ ] `voice_input` = `passed`
- [ ] `novice_user_observation` = `passed`
- [ ] 전체 자동 회귀 실패·오류 0
- [ ] `source.dirty=false`, 보고서 commit = 태그 commit
- [ ] `overall_status=accepted`, CLI 종료 코드 0
- [ ] `Prototype Goal accepted` 태그와 소스 아카이브 생성

## 8. 지금 필요한 사용자 결정

1. 한글 workflow를 **해결(R3-A)** 할 것인가, **범위에서 제외(R3-B)** 할 것인가.
2. Phase 4 B군 4종을 이번 수용에 포함할 것인가, 다음 주기로 이월할 것인가.
   (권장: 이월)
3. 수동 수용 3종을 언제 수행할 것인가. 이 날짜가 R2·R5 일정을 결정한다.
