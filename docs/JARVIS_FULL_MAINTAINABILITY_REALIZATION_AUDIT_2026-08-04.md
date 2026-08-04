# JARVIS 전체 유지보수·실현 가능성 감사 보고서

감사일: 2026-08-04

감사 대상 source commit: `17333d9801629a985af719a3b621fe5b34de7ff2`

이 보고서의 발견 사항을 수정·재검증한 결과는
[유지보수·실현 가능성 보완 감사 보고서](JARVIS_MAINTAINABILITY_REALIZATION_REMEDIATION_REPORT_2026-08-04.md)에
기록했다.

감사 관점:

1. 제품 Goal을 이 Windows PC에서 실제로 구현할 수 있는가
2. 기능이 늘어나도 안전하게 변경·검증·복구할 수 있는 구조인가

## 1. 최종 판정

| 항목 | 판정 | 설명 |
|---|---|---|
| 로컬 실현 가능성 | **조건부 합격, 높음** | 이 PC의 Excel·Word·PowerPoint·한글과 PDF 소유 fixture에서 핵심 경로가 실제로 동작했다. |
| 자동 Goal 증거 | **통과** | 현재 source identity에 결속된 13개 probe와 1,206개 회귀가 통과했다. |
| 제품 Goal 최종 수용 | **미완료** | 화면 읽기, 음성 입력, 비숙련자 관찰이 수동 대기이며 외부 AI 실계정 테스트 3개도 건너뛰었다. |
| 유지보수성 | **보완 필요** | 경계 분리는 개선됐지만 품질 게이트의 사각지대, 넓은 runtime service locator, 거대 함수·모듈이 남았다. |
| CI·릴리스 준비 | **불합격** | 현 GitHub workflow는 clean checkout에서도 strict audit 및 Office 실앱 조건 때문에 안정적으로 통과할 수 없다. |

종합 평가는 **“개인 PC용 고기능 프로토타입으로는 실현 가능하지만, 지속 가능한
릴리스 후보로 부르기 전 P1 보완이 필요하다”**이다. 기능을 처음부터 다시 만들
이유는 없다. 현재 동작 증거를 보존하면서 자동 게이트의 진실성과 비밀정보 저장,
남은 수동 증거를 먼저 바로잡는 것이 가장 경제적이다.

## 2. 감사 범위와 실행 결과

### 2.1 소스 규모

| 영역 | 파일 수 | 줄 수 |
|---|---:|---:|
| `engine` | 187 | 54,386 |
| `gui` | 32 | 6,449 |
| `tests` | 137 | 32,056 |
| `verification` | 58 | 14,870 |
| `docs` | 46 | 8,679 |

검증 코드가 충분히 많다는 점은 장점이지만, 테스트 줄 수 자체가 유지보수성을
보증하지는 않는다. 어떤 위험 경계를 강제하는지가 더 중요하다.

### 2.2 이번 감사에서 직접 실행한 검사

| 검사 | 결과 |
|---|---|
| 전체 회귀 | 1,206개 통과, 3개 skip, 실패·오류 0 |
| 현재 Goal 보고서 source identity | commit·tree hash·dirty 상태 모두 일치 |
| Goal 판정 | `automated_pass_manual_pending` |
| Ruff | 통과 |
| Python compileall | 통과 |
| GUI JavaScript `node --check` | 통과 |
| `pip check` | 통과 |
| release security audit | 통과, 저장소 비밀정보 발견 0 |
| 격리 실패 주입 | 9/9 통과 |
| 유지보수 성능 | 모든 기준 통과 |
| strict maintenance audit | **실패: `outputs/` 경고 1건** |

성능 측정은 시작 사전점검 p95 1,199.898ms, parser 초기화 p95 33.074ms,
로컬 parse p95 0.189ms, 로컬 명령 p95 0.089ms, atomic JSON write p95
16.448ms였다. 현재 PC에서 대화 인식이 느리게 느껴진다면 로컬 parser보다
GUI 시작·외부 AI·COM 앱 활성화 구간을 우선 측정해야 한다.

## 3. 잘 구현된 부분

### 3.1 실제 앱과 PDF의 구조적 검증

- Excel·Word·PowerPoint·한글 소유 문서에서 준비→승인→실행→재읽기→복구를
  실제 COM 경로로 검증한다.
- 네 앱에서 각각 100회 안정성 검증이 통과했고 COM 참조 증가와 원본 변경을
  별도로 검사한다.
- PDF는 연결 파일 fingerprint, 페이지 근거, 승인 전 무쓰기, 분할·병합·회전,
  원본 불변과 제한된 Undo를 구현했다.
- 복합 Excel→Word/한글→PowerPoint 업무는 산출물을 다시 열고 원본 불변·단계
  후조건·실패 재개 상태를 검사한다.

이는 좌표 클릭 데모가 아니라 이 PC에서 실제 네이티브 앱을 다루는 구현이라는
강한 증거다.

### 3.2 실패 안전성과 개인정보 경계

- 승인 만료·취소, 대상 변경, COM busy, 저장 실패, 검증 실패, 위험 동적 코드의
  9개 격리 실패 주입이 모두 fail-closed로 끝났다.
- Goal·PDF·Office probe 보고서는 사용자 경로와 문서 원문을 저장하지 않는다.
- release security audit는 현재 Git 이력과 기본 데이터에서 배포 비밀정보를
  발견하지 않았다.
- 동적 Python은 shell·레지스트리·credential·재귀 삭제 패턴을 별도 preflight로
  검사한다.

### 3.3 이전 리팩터링의 실질적 효과

- `CommandParser`의 native parse 분리와 `ConfirmationRegistry` 도입으로 승인
  상태 소유자가 명확해졌다.
- 하위 manager가 `CommandParser`를 영구 보관하는 양방향 참조는 자동 검사로
  차단한다.
- `business_workflow.py`는 3,715줄 기준에서 2,477줄, `stage10.py`는 1,903줄
  기준에서 778줄로 줄었다.
- Ruff·compile·unittest·maintenance·Goal 명령이 한 workflow에 모여 있다.

방향은 옳다. 아래 문제들은 이 성과를 무효화하는 것이 아니라 다음 유지보수
단계에서 보강해야 할 부분이다.

## 4. 주요 발견 사항

### P1-1. GitHub 품질 workflow가 현재 구조에서는 신뢰할 수 있게 통과할 수 없다

근거:

- `.github/workflows/quality.yml`은 `windows-latest`에서 strict maintenance audit와
  전체 source-bound Goal refresh를 연속 실행한다.
- 저장소에는 수동 체크표 2개가 `outputs/` 아래 추적돼 있다.
- `maintenance_audit --strict`는 내용이 있는 `outputs/`를 경고로 만들고 strict
  모드에서는 종료 코드 1을 반환한다. 이번 감사에서도 그대로 재현됐다.
- Goal refresh는 Excel·Word·PowerPoint·한글 COM을 실제 실행한다. 현재 GitHub
  공식 Windows hosted image의 설치 소프트웨어 목록에는 Microsoft Office와
  한글이 없다. 따라서 실앱 probe는 hosted runner의 책임으로 둘 수 없다.
- 공식 runner image는 주 단위로 갱신되고 `windows-latest`가 가리키는 이미지도
  이동할 수 있다. [GitHub runner image 정책](https://github.com/actions/runner-images),
  [Windows 2025 설치 소프트웨어 목록](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-VS2026-Readme.md),
  [GitHub-hosted runner 문서](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)를
  기준으로 판단했다.

영향:

- CI가 빨간색이어도 소스 결함인지 환경 결함인지 구분되지 않는다.
- 반대로 로컬에서만 통과한 결과를 GitHub branch 보호 규칙이 강제하지 못한다.

수정:

1. hosted `portable-quality` job에는 compile, Ruff, JS syntax, unit/integration,
   격리 Windows 계약, secret audit를 둔다.
2. 실제 Office/HWP Goal은 `[self-hosted, windows, jarvis-office]` label의 별도
   보호된 runner에서만 실행한다.
3. tracked 수동 문서는 `tests/manual/` 또는 `docs/manual_tests/`로 옮기고
   `outputs/`는 생성물 전용으로 유지한다.
4. 두 job의 결과를 branch protection 필수 검사로 등록한다.

### P1-2. 유지보수 감사기가 한국어 Git 경로를 잘못 해석해 금지 파일을 놓친다

근거:

- `verification/maintenance_audit.py`의 `_git_paths()`는 `git ls-files`의 줄 단위
  출력을 그대로 사용한다.
- Git은 기본 `core.quotepath`에서 한국어 경로를 큰따옴표와 8진 escape로
  출력한다.
- 감사기에는 이 출력의 dequote 과정이 없다. 그 결과 실제로 추적 중인
  `outputs/...수동테스트...xlsx` 2개가 `repository_paths()`에서는 발견되지
  않았고 `audit_tracked_artifacts()`도 오류 0건을 반환했다.

영향:

- 한국어 이름의 로그·백업·비밀정보 파일도 같은 사각지대를 통과할 수 있다.
- `maintenance_audit success=True`가 전체 추적 파일을 실제로 검사했다는 뜻이
  아니다.

수정:

- `git -c core.quotepath=false ls-files -z`와
  `ls-files --others --exclude-standard -z`를 bytes/NUL 기준으로 파싱한다.
- 한국어·공백·따옴표·줄바꿈 문자가 포함된 경로 fixture를 추가한다.
- 금지 디렉터리의 한국어 파일이 반드시 error가 되는 회귀 테스트를 추가한다.

### P1-3. API 키가 사용자 JSON과 여러 백업에 평문으로 저장된다

근거:

- `ConfigManager.save_ai_config()`는 입력 키를 `ai_config["api_key"]`에 그대로
  넣는다.
- `DictionaryManager.save()`는 전체 `ai_config`를
  `%LOCALAPPDATA%/Jarvis/data/dictionaries.json`에 atomic JSON으로 저장한다.
- 같은 저장은 최대 5개 버전 백업을 만들 수 있다.
- GUI 응답은 `has_api_key`만 노출해 화면 경계는 안전하지만 저장 계층에는
  Windows DPAPI·Credential Manager·keyring 포트가 없다.
- release security audit는 Git 저장소를 검사하므로 로컬 사용자 데이터의 평문
  키를 잡지 않는다.

영향:

- 사용자 프로필 파일이나 백업을 읽을 수 있는 다른 프로세스가 API 키를 그대로
  획득할 수 있다.
- 키 교체 뒤 오래된 백업에 이전 키가 남을 수 있다.

수정:

1. `CredentialStore` Protocol을 만들고 Windows 구현은 Credential Manager 또는
   현재 사용자 범위 DPAPI를 사용한다.
2. `dictionaries.json`에는 provider·routing mode·credential ID만 저장한다.
3. 최초 실행 시 기존 평문 키를 보안 저장소로 이관한 뒤 JSON과 백업에서 제거한다.
4. migration 실패 시 평문을 다시 저장하지 말고 typed `environment_blocked`로
   안내한다.

### P1-4. 제품 Goal의 핵심 사람·실계정 증거가 아직 없다

현재 자동 판정은 정확히 `automated_pass_manual_pending`이다.

- 화면 읽기 프로그램: pending
- 실제 음성 입력: pending
- 비숙련 사용자 관찰: pending
- OpenAI/Gemini 실계정 smoke 3개: `JARVIS_LIVE_AI_TEST=1`이 없어 skip

외부 AI는 “처음 보는 작업을 해석하고 검증 성공을 로컬 스킬로 전환한다”는 Goal의
핵심 입구다. mock과 구조 검사가 충분해도 실제 인증·TLS·응답 schema·현재 모델
호환을 대신할 수 없다. 비숙련자 접근성도 DOM/ARIA 테스트만으로 완료할 수 없다.

수정:

- 별도 소액·제한 키로 live AI 3개를 승인 실행하고 키·응답 원문 없는 결과만
  source identity와 함께 기록한다.
- `docs/MANUAL_GOAL_EVIDENCE_RUNBOOK.md`의 화면 읽기·음성·20개 비숙련자 과제를
  수행하고 실제 완료 항목만 attest한다.

### P1-5. 최신 32개 commit이 원격 branch와 CI 증거 없이 로컬에만 있다

감사 시점에 `codex/pdf-capabilities`는 upstream이 없고 `origin/main`보다 32개
commit 앞서 있으며 같은 이름의 원격 branch도 없다. 현재 구현은 이 PC의 Git
객체와 로컬 백업에만 의존한다.

영향:

- 디스크 장애 시 최신 구현과 감사 증거를 잃을 수 있다.
- PR·CI·코드 검토가 최신 소스를 대상으로 실행되지 않는다.

수정:

- P1-1과 P1-2를 먼저 보완한 뒤 branch를 원격에 push하고 PR에서 hosted/self-hosted
  검사를 분리해 실행한다.

### P2-1. 99줄 함수 게이트가 안전 핵심 함수 42개를 검사하지 않는다

`tests/unit/test_maintainability_budgets.py`가 검사하는 파일 집합 안에서는 100줄
이상 함수가 0개다. 그러나 전체 `engine`을 AST로 검사하면 100줄 이상 함수가
42개이며 전부 현재 게이트 밖에 있다.

우선순위가 높은 예:

| 함수 | 줄 수 | 위험 |
|---|---:|---|
| `failure_triage.classify` | 343 | 실패 책임 분류 전체가 한 함수에 집중 |
| `dynamic_code_preflight.visit_Call` | 252 | 보안 판정 분기 변경 영향이 큼 |
| `confirmation_response_handler.resolve_selection` | 225 | 승인·정보 보완·재개 경계 혼합 |
| `action_executor.execute_plan` | 162 | 실행·검증·복구 결합 |
| `skill_executor.execute` | 160 | 학습 스킬 정책·실행 결합 |
| `command_pipeline.execute` | 156 | 전체 라우팅 순서 결합 |
| `dynamic_code_preflight.analyze` | 154 | 보안 분석·결과 조립 결합 |

함수 길이만으로 버그를 단정할 수는 없지만, 승인·보안·복구 함수가 테스트 사각에
있다는 점이 문제다. 전체 `engine`에 기본 99줄 기준을 적용하고 예외는 이유·소유자·
만료일이 있는 allowlist로 관리해야 한다.

### P2-2. `ParserRuntimeServices`는 작은 port가 아니라 34개 `Any` 필드의 service locator다

양방향 parser 참조는 제거됐지만 `engine/runtime_services.py`는 534줄이며 34개
협력자를 모두 `Any`로 받는다. 라우팅·동적 코드·학습·실패 진단의 실제 행동도
이 객체 안에 남아 있다. 하위 계층이 parser 자체를 받지는 않지만 필요한 기능보다
훨씬 넓은 권한을 받을 수 있고 정적 타입 검사가 경계 위반을 잡지 못한다.

수정:

- `ConfirmationPorts`, `LearnedReplayPorts`, `PdfTaskPorts`, `CommandRoutePorts`처럼
  소비자별 작은 `Protocol`을 정의한다.
- `runtime_services.py`는 조립만 담당하고 날짜·날씨·동적 코드·실패 진단 행동은
  기능 서비스로 이동한다.
- 하위 handler 테스트는 자신에게 허용된 port 외 속성 접근이 불가능한 fake를
  사용한다.

### P2-3. 거대 모듈 예산이 현재 크기로 ratchet되지 않았다

`maintenance_audit.py`의 module budget은 과거 크기를 상한으로 유지한다.

| 모듈 | 현재 | 예산 | 다시 늘어날 수 있는 여유 |
|---|---:|---:|---:|
| `business_workflow.py` | 2,477 | 3,715 | 1,238 |
| `stage10.py` | 778 | 1,903 | 1,125 |
| `controller.py` | 1,628 | 1,893 | 265 |
| `powerpoint_adapter.py` | 1,149 | 1,289 | 140 |
| `hwp_adapter.py` | 968 | 1,063 | 95 |

리팩터링 성과가 크게 역행해도 CI가 통과할 수 있다. 예산을 현재 줄 수 이하로
ratchet하고, 새 기능은 새 기능 서비스에만 추가하도록 import·line budget 계약을
고정해야 한다.

### P2-4. README·제약·변경 이력이 현재 구현과 충돌한다

- README는 `PDF-2 완료`와 PDF-3/4 실행 차단을 설명한다.
- `KNOWN_LIMITATIONS.md`도 PDF-3/4 미완료, VBA 실검 대기, HWP 보안 모듈 부재를
  현재 사실처럼 기록한다.
- 현재 clean source probe는 PDF-3/4, Excel VBA, HWP 생성·read-back을 모두
  통과했다.
- `CHANGELOG.md` 최신 항목도 PDF-2에서 멈췄고 앱 버전은 계속
  `1.1.0-rc.6`이다.

문서가 과장된 것보다 보수적으로 뒤처진 상태지만 사용자는 실제 지원 기능을
판단할 수 없고 개발자는 잘못된 제약을 유지할 수 있다. 기능 상태의 단일 manifest를
두고 README·Known limitations·Goal gate가 이를 참조하도록 해야 한다.

### P2-5. 로컬 생성물 정리 정책이 실행되지 않는다

현재 `outputs/`는 87개 파일, 약 42.04MiB다. 이 중 과거 prototype·감사·재테스트
작업 폴더가 대부분이며 여러 임시 `node_modules` 작업 흔적이 남아 있다.
`.venv` 57.72MiB는 개발 의존성이므로 정상 범주지만 `outputs/`는 보존 대상과
재생성 가능한 캐시가 섞여 있다.

수정:

- 수동 최종 산출물은 `artifacts/manual/`처럼 명시적 보존 위치에 둔다.
- 렌더 preview·작업용 node_modules·임시 report는 `tmp/`에서 만들고 성공·실패
  모두 정리한다.
- `cleanup_workspace.ps1`에 dry-run, 보존 목록, 기간/용량 기준을 추가해 정기적으로
  실행한다.

### P3-1. 정적 품질 게이트가 최소 규칙에 머문다

Ruff는 `E4`, `E7`, `E9`, `F`, `I`만 강제한다. 타입·복잡도·보안·예외 삼킴과
같은 유지보수 위험은 검사하지 않고 coverage 하한도 없다. 한 번에 규칙을 모두
켜기보다 안전 경계부터 `B`, `C90`, `S` 계열을 검토하고 `mypy` 또는 pyright를
새 Protocol 경계에 제한 적용하는 것이 현실적이다.

## 5. 제품 Goal별 실현 판정

| Goal 축 | 현재 실현도 | 감사 판정 |
|---|---|---|
| 자연어→행동 | 높음 | 1,000문장·PDF 120문장 통과. 외부 AI 실계정만 미확인 |
| 현재 문맥 | 높음 | 문서·선택·직전 검색 fingerprint 실검 통과. 뷰어 PDF 문맥은 선택 후속 |
| 안전한 네이티브 실행 | 높음 | 네 앱·VBA·PDF의 승인·read-back·Undo·실패 주입 통과 |
| 성공 작업 재사용 | 중상 | 승인형 구조 스킬 재생 통과. 임의 사용자 단계 조합은 제한적 |
| 여러 앱 업무 연결 | 높음 | Excel→Word/한글→PPT 세 경로 실검 통과 |
| 사용자 방식 학습 | 중상 | 반복 증거·승인·교체·서식 read-back 통과. 자유 의미 학습은 의도적 제한 |
| 실패 책임 분류 | 중상 | typed 분류·익명 incident 통과. 343줄 분류 함수가 유지보수 위험 |
| 비숙련자 접근성 | 미완료 | ARIA·키보드 자동 계약은 있으나 사람 시험 3종 대기 |
| PDF 확장 증거 | 높음 | MVP 자동 수용 완료. OCR·현재 viewer 연결은 선택 기능 |

기술적으로 이 PC에서 North Star의 핵심 흐름은 구현 가능하다. 남은 가장 큰
실현 위험은 “코드가 없어서”가 아니라 실제 사용자·외부 AI·다른 설치 조합의 증거가
없다는 점이다.

## 6. 권장 수정 순서

### 0단계 — 진실한 게이트 복구

1. Git 경로를 NUL·Unicode 안전 방식으로 파싱한다.
2. tracked 수동 문서를 `outputs/` 밖으로 옮긴다.
3. strict audit가 clean checkout에서 실제로 통과하는지 별도 fixture repo로 검증한다.
4. hosted 품질 CI와 self-hosted Office/HWP Goal CI를 분리한다.

### 1단계 — 비밀정보 저장 보완

1. `CredentialStore`와 Windows 보안 저장 구현을 추가한다.
2. 기존 API 키와 백업을 이관·삭제하는 schema migration을 만든다.
3. 실패·취소·재시작·백업 복원 시험을 추가한다.

### 2단계 — 유지보수 경계 축소

1. 42개 장함수 전체를 예산에 넣는다.
2. 먼저 실패 분류, 동적 코드, 승인 응답, skill 실행, pipeline을 서비스로 나눈다.
3. 34-field runtime locator를 소비자별 Protocol로 나눈다.
4. 모듈 line budget을 현재 값으로 ratchet한다.

### 3단계 — 문서와 릴리스 상태 정합화

1. README의 PDF 상태를 PDF-7 완료로 갱신한다.
2. VBA·HWP 제한은 “감사 당시 환경”과 “현재 probe 결과”를 분리해 기록한다.
3. CHANGELOG와 개발 버전을 다음 RC로 올린다.
4. 기능 manifest와 문서 상태 일치 테스트를 추가한다.

### 4단계 — 남은 제품 수용

1. 승인된 live AI smoke 3개를 실행한다.
2. 실제 화면 읽기·음성·비숙련자 관찰을 수행한다.
3. 발견된 문구·포커스·복구 문제만 보완하고 전체 Goal gate를 다시 실행한다.
4. 원격 branch에 push하고 두 종류 CI가 모두 통과한 commit을 릴리스 후보로 고정한다.

## 7. 완료 기준

다음 조건을 모두 만족하면 유지보수와 실현 관점에서 “수용 가능”으로 올릴 수 있다.

- clean checkout에서 portable CI가 통과한다.
- 보호된 Office/HWP runner에서 source-bound Goal probe가 통과한다.
- Unicode 경로를 포함한 모든 추적 파일이 secret/artifact audit 대상이 된다.
- API 키 평문이 현재 JSON과 버전 백업에 남지 않는다.
- 100줄 초과 안전 핵심 함수 42개가 0개이거나 만료일 있는 예외로 관리된다.
- runtime dependency가 소비자별 작은 Protocol로 제한된다.
- README·KNOWN_LIMITATIONS·CHANGELOG·버전이 현재 기능과 일치한다.
- live AI 3개와 화면 읽기·음성·비숙련자 수동 evidence가 등록된다.
- 최신 commit이 원격에 있고 필수 CI 결과가 연결된다.

## 8. 결론

JARVIS는 “만들 수 있는가” 단계는 이미 넘었다. 이 PC에서는 자연어→문맥→실앱
안전 실행→검증→복구→학습의 핵심 고리가 실제로 동작한다. 현재 위험은 기능 부족
보다 **검증 도구가 모든 파일과 실행 환경을 정직하게 반영하는가**, **API 키를
개인 에이전트 수준으로 보호하는가**, **사람 사용 증거가 있는가**에 집중돼 있다.

따라서 다음 개발은 새 기능 추가가 아니라 0~4단계의 수용 기반을 닫는 작업이어야
한다. 특히 P1-1~P1-3을 해결하기 전에는 자동 Goal 통과를 릴리스 준비 완료로
해석하면 안 된다.
