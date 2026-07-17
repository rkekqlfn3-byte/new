# JARVIS 독립 코드 감사 후속 수정 계획서

작성일: 2026-07-17
검토 기준: 독립 감사 문서와 현재 `feature/prototype-1.0-edit-mode` 작업 트리
문서 성격: 구현 전 계획서 — 이 문서 작성 단계에서는 제품 코드를 변경하지 않음

## 1. 검토 결론

독립 감사의 핵심 결론은 대체로 타당하다. 현재 코드에는 즉시 사용자 문서를
손상시키는 확정적 치명 결함은 확인되지 않았지만, 다음 네 항목은 동결 전에 반드시
수정해야 한다.

1. 편집 화면의 1초 COM polling 제거
2. 편집 명령 전 문맥 갱신 완료와 요청 문맥 fingerprint 고정
3. 워크플로 미리보기 취소 시 영구 상태가 남는 구조 제거
4. 감사 대상 소스의 commit·버전·검증 증거를 하나의 신원으로 고정

PPT 파일명, Word 보고서의 절대 경로, 연결 직후 placeholder, VBA 실행 결과 표현도
같은 수정 묶음에서 처리한다. 11단계는 이번 동결에서 암묵적 행동 학습까지 확대하지
않고 **명시적 사용자 선호 학습 v1**로 정확히 표현하는 방식을 권장한다.

현재 작업 트리 기준으로 감사 당시와 달라진 사실도 있다.

- 작업 트리는 여전히 clean하지 않다: HEAD `5a60d43`, 수정 29개·미추적 60개
- `APP_VERSION`은 여전히 `1.1.0-rc.5`
- 현재 Windows 재검: 단위 164/164, 통합 269/269 통과
- 직전 전체 회귀: 623개 중 620개 통과, 외부 AI 3개 skip, 실패 0개
- Stage 12 JSON 보고서는 현재 생성돼 있다.
- PowerPoint 안정화 보완 후 실앱 누적 500/500을 통과했다.

따라서 Stage 12 보고서 부재와 PowerPoint 간헐 프록시 오류는 새 기능 수정 대상이
아니다. 다만 최종 동결 commit에 대응하는 보고서를 다시 만들고 보존해야 한다.

## 2. 감사 항목별 최신 판정

| ID | 최신 판정 | 현재 근거 | 계획 반영 |
|---|---|---|---|
| P1-1 소스 신원 미고정 | 유효·동결 차단 | HEAD는 3단계이고 4~12단계가 미커밋 | 마지막 단계에서 clean commit·버전·tag 고정 |
| P1-2 1초 COM polling | 유효·필수 | `gui/js/edit_mode.js`에 `setInterval(..., 1000)` 존재 | 이벤트 기반 갱신으로 교체 |
| P1-3 전송 전 문맥 미동기화 | 유효·필수 | 전송 전에 refresh를 await하지 않고 context fingerprint도 보내지 않음 | 요청 계약과 UI 전송 경계 보강 |
| P1-4 승인 전 workflow 저장 | 유효·필수 | `prepare()`가 `approval_required` JSON을 즉시 저장 | 승인 시점 최초 저장 구조로 변경 |
| P1-5 학습 범위 과장 | 유효·범위 결정 필요 | `record_evidence()` 호출은 명시적 선호 분석 경로에 한정 | 문서·UI 명칭을 v1 범위에 맞춤 |
| P2-1 PPT 파일명 5장 고정 | 유효 | 실제 `slide_count`와 무관하게 `5장_요약` 사용 | 동적 장수 파일명 적용 |
| P2-2 Word 절대 경로 노출 | 유효 | Word 본문은 `source_files` 값을 그대로 출력 | 기본 basename만 출력 |
| P2-3 placeholder 갱신 누락 | 유효 | `renderEditSession()`이 표시 갱신 함수를 호출하지 않음 | 연결·해제 직후 즉시 갱신 |
| P2-4 문서·버전 불일치 | 유효 | README 시점별 수치 혼재, rc.5 및 개인 PC 경로 존재 | 문서 계층·버전·경로 정리 |
| P2-5 Stage 12 보고서 부재 | 현재 해소·보존 필요 | `prototype11_stage12_report.json` 존재하나 ignore 대상 | 최종 commit 기준 재생성·요약 manifest 보존 |
| P2-6 VBA 완료 표현 | 부분 유효 | 메시지는 ‘반환’을 말하지만 `verified=true`, `macro_completed=true`는 범위가 모호 | 호출 완료와 업무 결과 검증을 분리 표기 |
| P2-7 소스 ZIP 정리 | 유효 | 작업 폴더에 `.venv`, build, dist 존재 | clean commit의 `git archive`만 사용 |

감사에서 Linux 경로 해석으로 실패했던 통합 테스트는 현재 Windows에서 269/269가
통과했다. 별도 제품 결함 수정 항목으로 올리지 않고, 최종 Windows 회귀 결과에
근거를 남긴다.

## 3. 권장 구현 순서

### 1차 — 편집 문맥 동기화와 COM 호출 축소

대상: P1-2, P1-3, P2-3
우선순위: 최상
예상 변경 파일:

- `gui/js/edit_mode.js`
- `gui/js/chat/controller.js`
- `gui/index.html`
- `engine/edit_mode/contracts.py`
- `engine/edit_mode/coordinator.py`
- `engine/edit_mode/controller.py`
- `engine/api/command_api.py`
- 관련 GUI·계약·통합 테스트

구현 원칙:

1. `window.setInterval(refreshEditContext, 1000)`을 완전히 제거한다.
2. 문맥 갱신은 문서 연결, 편집 탭 진입, 창 focus, 사용자 새로고침, 편집 명령
   전, 편집 또는 승인 처리 완료 후에만 수행한다.
3. 편집 문맥 영역에 별도 수동 새로고침 버튼을 추가한다.
4. 현재 boolean in-flight 플래그 대신 하나의 공유 Promise를 사용한다. 동시에
   여러 갱신 요청이 와도 기존 Promise를 반환해 전송 경로가 완료를 기다릴 수 있게
   한다.
5. edit 모드 전송 직전에 `await refreshEditContext({ required: true })`를 호출한다.
   세션 없음, 문맥 조회 실패, 세션 교체가 발생하면 `parse_command`를 호출하지 않는다.
6. UI가 받은 `context_fingerprint`를 `edit_context`와 `EditRequest`에 포함한다.
7. coordinator는 새로 캡처한 문맥이 요청 시점 fingerprint와 다르면 미리보기를
   만들지 않고 `stale_context`로 차단한다. 승인 시점의 기존 재검증도 유지한다.
8. 미리보기 다시 작성 시에도 최초 준비 문맥 fingerprint를 보존해 선택이 바뀐
   상태에서 다른 대상의 미리보기를 만들지 않는다.
9. `renderEditSession()`은 연결·해제 양쪽에서 placeholder와 버튼 상태를 즉시
   갱신한다.

필수 테스트:

- 편집 탭을 60초 유휴 상태로 둬도 주기적 `get_edit_context` 호출이 0회일 것
- 전송 Promise가 문맥 조회 완료 전에 `parse_command`를 호출하지 않을 것
- 문맥 조회 실패 시 명령이 백엔드로 전송되지 않을 것
- 표시 문맥과 요청 fingerprint가 같을 것
- refresh 직후 선택이 바뀌면 prepare 단계에서 차단될 것
- 연결·해제 직후 placeholder가 즉시 바뀔 것
- 기존 승인 시점 선택 변경 차단 테스트가 계속 통과할 것

### 2차 — 워크플로 상태 수명주기와 산출물 개인정보

대상: P1-4, P2-1, P2-2
우선순위: 최상
예상 변경 파일:

- `engine/workflows/business_workflow.py`
- `engine/edit_mode/stage10.py`
- `engine/edit_mode/controller.py`
- `engine/confirmation/confirmation_response_handler.py`
- Stage 10 단위·통합 테스트와 검증 probe

권장 설계:

1. `WorkflowExecutor.prepare()`를 비영구 plan 생성과 승인 후 state 시작으로 분리한다.
2. 새 워크플로의 preview plan은 COM-free JSON으로 confirmation payload에만 둔다.
3. 사용자가 승인하면 source fingerprint, 출력 폴더, 출력 파일 충돌을 다시 검사한
   후에만 최초 workflow JSON을 원자적으로 저장한다.
4. 미리보기를 취소하거나 확인이 만료되면 workflow JSON은 한 개도 생기지 않는다.
5. 실패 워크플로 재개 미리보기의 취소는 기존 `failed` 상태를 그대로 보존한다.
6. `latest_for_source()`는 명시적으로 재개 가능한 `failed`와 crash 복구용
   `running`만 선택한다. `approval_required`, `cancelled`, `completed`는 제외한다.
7. 기존 schema의 오래된 `approval_required` 파일은 백업 후 안전하게 정리하는
   일회성 migration 또는 retention 정책을 추가한다.
8. PowerPoint 파일명은 실제 장수를 반영해
   `*_JARVIS_{slide_count}장_요약.pptx`로 만든다.
9. Word 보고서 본문의 원본 파일 표시는 기본적으로 `Path(item).name`만 사용한다.
   전체 경로가 정말 필요한 경우에는 별도의 명시적 옵션과 미리보기 승인을 요구한다.

필수 테스트:

- 새 워크플로 preview와 취소 뒤 저장 폴더에 JSON이 없을 것
- 승인 직전에 원본 또는 출력 경로가 바뀌면 저장·실행되지 않을 것
- 재개 preview 취소 뒤 기존 실패 상태가 다시 선택될 것
- legacy `approval_required`가 재개 대상으로 선택되지 않을 것
- 여러 상태가 있을 때 가장 최근의 재개 가능한 상태만 선택할 것
- 3장·5장·7장 결과의 파일명과 실제 슬라이드 수가 일치할 것
- Word 보고서에 원본 절대 경로와 Windows 사용자명이 없을 것
- 기존 파일 비덮어쓰기와 실패 단계 재개가 계속 통과할 것

### 3차 — 학습 범위와 VBA 결과 의미 정리

대상: P1-5, P2-6
우선순위: 높음
예상 변경 파일:

- `engine/edit_mode/stage11.py`
- `engine/app_actions/excel_vba_adapter.py`
- `engine/edit_mode/stage5.py`
- `README.md`, Stage 9·11 문서, GUI 안내 문구
- Stage 9·11 테스트

11단계 권장 범위:

- 이번 동결에서는 현재 구현과 일치하도록 이름을 **명시적 사용자 선호 학습 v1**로
  고친다.
- 지원 범위는 “항상 7장”, “문체는 간결하게”처럼 허용 목록으로 분석 가능한
  명시적 문장, 3회 증거, 사용자 승인 후 활성화로 한정한다.
- “너무 길어”, 미리보기 재작성, 사용자의 직접 수정 결과 차이를 자동 학습 증거로
  바꾸는 기능은 후속 단계로 분리한다.
- 후속 행동 학습을 구현할 때도 원문 저장 금지, 자동 확정 금지, 3회 이상 반복,
  후보 설명, 사용자 승인, 되돌리기 정책을 유지한다.

VBA 결과 표현:

- `Application.Run()` 반환은 `invocation_completed=true`로 표현한다.
- `business_result_verified`는 사용자가 셀·파일 등 후조건을 지정해 별도 검증한
  경우에만 true가 될 수 있게 한다.
- 기존 `verified`가 무엇을 검증했는지 `verification_scope=invocation_return`으로
  명시한다.
- UI 메시지는 “COM 오류 없이 프로시저 호출이 반환됨”과 “업무 결과 확인 안 됨”을
  분리해 보여준다.

필수 테스트:

- 명시적 선호가 아닌 일반 교정 명령은 학습 증거로 저장되지 않을 것
- 세 번째 동일 증거도 사용자 승인 전에는 활성화되지 않을 것
- VBA 호출 성공만으로 업무 결과 검증이 true가 되지 않을 것
- 사용자 지정 후조건이 있는 경우에만 결과 검증 상태가 별도로 기록될 것

### 4차 — 문서·버전·감사 증거·소스 패키지 정리

대상: P1-1, P2-4, P2-5, P2-7
우선순위: 동결 필수
예상 변경 파일:

- `README.md`, `CHANGELOG.md`, `KNOWN_LIMITATIONS.md`, `RULEBOOK.md`
- `engine/version.py`
- `docs/history/`와 `docs/validation/`
- `.gitattributes` 또는 전용 source archive 스크립트

실행 계획:

1. README 첫 화면을 현재 제품 개요, Prototype 1.0 1~8단계, Prototype 1.1
   9~12단계, 최신 검증 결과 순으로 다시 구성한다.
2. 단계별 과거 테스트 수치는 역사 기록으로 두되 최신 전체 수치와 섞이지 않게
   날짜·대상 commit을 붙인다.
3. rc.2 기준 `AUDIT_REPORT.md` 등 과거 보고서는 `docs/history/`로 이동하고 대상
   버전과 commit을 문서 첫 부분에 표시한다.
4. 개인 PC 절대 경로는 `%USERPROFILE%`, `<JARVIS_RUNTIME>` 같은 중립 표현 또는
   설정값으로 바꾼다.
5. Stage 12를 포함한 raw JSON 보고서는 계속 ignore할 수 있지만, 최종 commit,
   실행 명령, pass/skip/fail, 개인정보 검사, raw report SHA-256을 담은 정제된
   validation manifest는 버전 관리한다.
6. 현재 앱 계보상 권장 동결 버전은 `v1.1.0-rc.6`이다. Prototype 1.0은 편집모드
   1~8단계의 milestone 이름으로 유지하고, 앱 버전과 혼동되는 별도
   `prototype-1.0` tag는 만들지 않는다.
7. 소스 공유본은 clean commit에서 `git archive`로만 만든다. `.venv`, `.git`,
   build, dist, `__pycache__`, 로그, 사용자 데이터, raw probe 보고서는 포함하지
   않는다.
8. archive를 풀어 compileall과 비COM 테스트를 다시 실행하고 파일 목록과 SHA-256을
   manifest에 기록한다.

버전 또는 tag 이름을 다르게 정하려면 이 단계 시작 전에 한 번만 결정한다. 코드,
README, CHANGELOG, validation manifest, tag의 버전 문자열은 반드시 동일해야 한다.

## 4. 전체 검증 계획

### 자동 검증

```powershell
git diff --check
python -m compileall -q engine verification tests
python -m tests.test_runner unit -q
python -m tests.test_runner integration -q
python -m tests.test_runner windows -q
python -m tests.test_runner all -q
```

테스트 총수는 새 테스트 추가로 623개보다 늘어나므로 기존 숫자를 완료 기준으로
고정하지 않는다. 완료 기준은 발견된 테스트 전부 통과, 외부 자격 증명 테스트만
사유가 명시된 skip, 실패 0개다.

### Windows 실앱 검증

1. Stage 8 네 앱 전체 probe: Excel·한글·Word·PowerPoint 각 100회
2. PowerPoint 단독 100회 추가: Shape 선택, 이동, 크기 변경, Undo 포함
3. 60초 UI 유휴 상태에서 문맥 COM polling이 발생하지 않는지 계측
4. 편집 명령 직전 선택 변경과 문맥 조회 실패가 fail-closed 되는지 확인
5. Stage 10 승인 전 무산출, 취소 후 무상태, 실패 재개, 3·5·7장 파일명 검증
6. Stage 11 명시적 선호 3회·승인·재시작 유지와 일반 교정 미학습 검증
7. Stage 12 개인정보 제거와 자동 코드·바이너리 변경 부재 검증
8. 사용자 Office 프로세스와 JARVIS 소유 프로세스 분리 및 종료 후 잔류 0 확인

Stage 9 실제 VBA 쓰기·복원·실행은 Excel AccessVBOM 설정을 사용자가 직접 허용한
환경에서만 수행한다. JARVIS가 보안 설정을 변경하거나 우회하는 작업은 계획에
포함하지 않는다. 설정이 허용되지 않으면 `blocked_vba_trust`를 정상 안전 차단으로
기록하고, 9단계 실 VBA 검증은 미완료로 표시한다.

### 개인정보·패키지 검증

- Word·PPT와 validation manifest에 사용자 절대 경로, 문서 본문, 명령 원문이
  없는지 검색
- API 키·토큰·개인 사용자명 형태의 비밀값 검색
- source archive에 제외 디렉터리와 raw report가 없는지 파일 목록 검사
- archive 해제본의 commit·version manifest와 SHA-256 일치 확인

## 5. 권장 commit 단위

작업 트리 89개 변경을 한 번에 동결하지 않는다. 다음 경계로 검토 가능한 commit을
만드는 것이 좋다.

1. `편집 문맥 갱신을 이벤트 기반으로 전환`
2. `워크플로 승인 전 무상태와 출력 개인정보 경계 보강`
3. `명시적 선호 학습·VBA 결과 의미와 문서 정리`
4. `전체 회귀 및 Windows probe 증거 갱신`
5. `버전 승격과 소스 동결`

각 commit 직전에 관련 targeted 테스트를 실행하고, 4번에서 전체 회귀와 실앱
probe를 실행한다. 최종 tag는 clean working tree에서만 만든다.

## 6. 위험과 롤백

- `EditRequest`에 context fingerprint를 추가하면 기존 테스트 fixture와 재작성
  경로가 영향을 받는다. 계약 schema를 올리고 모든 생성 경로를 한 번에 갱신한다.
- workflow 저장 schema 변경 전 기존 JSON을 백업한다. 완료·실패 기록은 보존하고
  실행되지 않은 legacy `approval_required`만 retention 대상으로 삼는다.
- 새 동적 PPT 파일명은 기존 파일을 덮어쓰지 않는다. 충돌 시 현재 고유 이름 생성
  규칙을 그대로 적용한다.
- 문서 경로 마스킹은 내부 원본 검증용 `source_path`까지 제거하지 않는다. 외부
  산출물과 보고 메시지에서만 basename을 사용한다.
- 각 단계에서 전체 회귀가 깨지면 해당 단계 commit만 되돌릴 수 있게 다른 단계와
  섞지 않는다.

## 7. 최종 완료 기준

다음 조건을 모두 만족해야 동결 가능으로 판정한다.

- 유휴 상태의 1초 COM polling이 완전히 제거됨
- UI 표시 문맥, 요청 문맥, prepare 문맥, 승인 문맥이 fingerprint로 연결됨
- 문맥 조회 실패와 선택 변경이 외부 수정 전에 차단됨
- 새 workflow 미리보기·취소가 영구 JSON을 만들지 않음
- 재개 명령이 취소·승인 대기 상태를 선택하지 않음
- 3·5·7장 PPT 파일명과 실제 장수가 일치함
- Word 보고서에 사용자 절대 경로가 없음
- 11단계가 명시적 선호 학습 v1로 정확히 설명됨
- VBA 호출 완료와 업무 결과 검증이 구분됨
- 자동 회귀 실패 0, Windows 대표 probe 통과, Stage 9 차단 여부 명시
- 사용자 문서·보안 설정 변경 없음, 소유 프로세스 잔류 0
- README·CHANGELOG·APP_VERSION·validation manifest·tag 버전 일치
- clean commit에서 생성한 정리된 source archive와 SHA-256 존재

이 기준을 만족한 뒤에만 실사용 알파 또는 배포 EXE 빌드 단계로 넘어간다. EXE
빌드와 배포본 교체는 이 수정 계획의 소스 동결 이후 별도 승인 작업이다.
