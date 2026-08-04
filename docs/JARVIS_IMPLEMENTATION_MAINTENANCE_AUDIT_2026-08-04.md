# JARVIS 구현·유지보수 감사 보고서

- 감사일: 2026-08-04
- 대상 커밋: `a6f084c` (branch `codex/pdf-capabilities`)
- 범위: `engine/`, `gui/`, `verification/`, `tests/`, CI 게이트, 문서 체계
- 방법: 프로젝트 게이트 실측 실행 + 독립적 소스 분석(AST·의존 그래프·중복 탐지)
- 성격: 읽기 전용 감사. 소스는 변경하지 않았다.

---

## 1. 요약

프로젝트는 **자체 방어 체계가 예외적으로 잘 갖춰져 있다.** 게이트 5종을 실제로
돌려본 결과 전부 통과했고, 아키텍처 계약이 문서가 아니라 테스트로 강제되고
있으며, 계층 방향 위반이 실측상 0건이다. 이 수준의 self-enforcement를 갖춘
10만 줄 규모 개인 프로젝트는 드물다.

동시에 **구조적 결함이 하나 있다.** 게이트가 측정하는 축(함수 길이, 모듈 길이,
계층 방향, 비밀값)은 잘 지켜지지만, **게이트가 측정하지 않는 축인 "같은 개념의
중복 정의"가 방치되어 이미 사용자 표면에서 동작 불일치를 만들고 있다.** 한국어
어휘 테이블이 최대 16곳에 복제되어 있고, 복제본끼리 값이 다르다. 이것은
스타일 문제가 아니라 제품 Goal 기준 1·3에 직접 위배되는 동작 결함이다.

| 축 | 평가 | 근거 |
|---|---|---|
| 게이트·CI 신뢰도 | **우수** | 5종 전부 실측 통과, 1,216 테스트 63초 |
| 아키텍처 계층 준수 | **우수** | parser 역참조 0건(합성 루트 제외), 계약 테스트로 강제 |
| 보안 경계 | **양호** | TLS 검증 강제, DPAPI 자격증명, GUI 허용목록 sanitizer |
| **개념 단일 정의** | **미흡** | 정렬 어휘 7곳·확인 취소 어휘 16곳 복제, 드리프트 확인 |
| 테스트 실행 범위 | **보통** | Python 강함, GUI JS 3,443줄은 실행 테스트 0 |
| 코드 자기설명성 | **보통** | 반환 타입 33%, 파라미터 타입 17%, docstring 15% |

---

## 2. 실측 검증 결과

전부 이 감사에서 직접 실행했다.

| 게이트 | 결과 |
|---|---|
| `ruff check .` | **All checks passed** |
| `python -m unittest -q` | **OK** — 1,216 tests, 3 skipped, 62.9s |
| `verification.maintenance_audit --strict` | **success=True, errors=0, warnings=1** |
| `verification.release_security_audit` | **passed** — findings=0, 히스토리 스캔 포함 |
| `tests/test_runner.py` 등록 일치 | **128/128 일치**, 누락·유령 0 |

유일한 warning은 `outputs/` 워크스페이스 디렉터리 존재이며, 해당 경로는
`.gitignore`에 있어 추적 오염은 없다. 위생 경고 수준이다.

---

## 3. 확인된 강점 (근거 포함)

### 3.1 아키텍처 계약이 실제로 강제된다
`docs/JARVIS_ARCHITECTURE_CONTRACT.md`의 "하위 계층은 `CommandParser`를 영구
참조하지 않는다"는 금지 규칙이 실측상 지켜진다. `engine/` 전체에서
`from engine.parser`를 하는 모듈은 합성 루트인 [core.py:11](engine/core.py:11)
뿐이다. 게다가 [maintenance_audit.py](verification/maintenance_audit.py)의
`retained_parent_reference_paths()`는 **속성 이름이 아니라 객체 identity를 BFS로
추적**해 bound method·`functools.partial`·클로저 셀까지 뒤진다. 이름을 바꿔
우회하는 것이 불가능한 설계다.

### 3.2 예산이 만료일을 가진다
[maintainability_budgets.py](verification/maintainability_budgets.py)의 43개
장문 함수 예외는 전부 `owner`, `reason`, `expires="2026-11-30"`를 갖는다.
allow-list가 영구 사각지대가 되지 않도록 설계했다는 점이 중요하다.

### 3.3 보안 경계
- TLS: [tls.py:21](engine/network/tls.py:21) `verify_mode = CERT_REQUIRED` 강제,
  인증서 실패를 별도 사용자 메시지로 분류
- 자격증명: [credential_protection.py:22](engine/security/credential_protection.py:22)
  Windows DPAPI(`CryptProtectData`) 사용, 평문 저장 없음
- GUI: [renderer.js:25](gui/js/chat/renderer.js:25) `sanitizeRenderedHtml()`이
  허용 태그 목록 + 전체 속성 제거 + `href` 스킴 검사 방식. 블랙리스트가 아닌
  화이트리스트라는 점이 옳다. marked는 CDN이 아닌 로컬 vendor 번들.

### 3.4 Win32 best-effort 패턴이 검증으로 닫혀 있다
[action_executor.py:388-455](engine/action_executor.py:388)의 포그라운드 전환은
`except Exception: pass`가 7번 나오지만, 각 시도 후 `GetForegroundWindow() == hwnd`로
실제 상태를 다시 읽고, 끝내 실패하면 `ActionPlanVerificationError`를 던진다.
"조용히 실패하고 진행"이 아니라 "조용히 시도하고 시끄럽게 검증"이다. 정당하다.

---

## 4. 발견 사항

### [H-1] 같은 개념이 여러 곳에 정의되어 값이 이미 어긋났다 — 정렬 어휘

한국어 정렬 어휘가 **7곳**에 독립적으로 정의되어 있고, 각각 인정하는 표현이 다르다.

| 위치 | 인정 표현 |
|---|---|
| [stage5.py:312](engine/edit_mode/stage5.py:312) | 왼쪽·가운데·중앙·오른쪽 |
| [stage5.py:439](engine/edit_mode/stage5.py:439) | + 양쪽 |
| [stage6.py:136](engine/edit_mode/stage6.py:136), [stage6.py:249](engine/edit_mode/stage6.py:249) | + 양쪽 |
| [office_command_parser.py:193](engine/parsing/office_command_parser.py:193) | + 좌측·우측 |
| [office_command_parser.py:524](engine/parsing/office_command_parser.py:524) | + 좌측·우측·양쪽·배분 |
| [excel_adapter.py:586](engine/app_actions/excel_adapter.py:586) | 왼쪽/왼쪽 정렬/좌측/가운데/가운데 정렬/중앙/중앙 정렬/오른쪽/오른쪽 정렬/우측 |
| [word_adapter.py:242](engine/app_actions/word_adapter.py:242) | 왼쪽·가운데·중앙·오른쪽·양쪽 **만** |
| [powerpoint_adapter.py:531](engine/app_actions/powerpoint_adapter.py:531) | 동일 5개 |
| [hwp_adapter.py:457](engine/app_actions/hwp_adapter.py:457) | 12개 (가장 완전) |

실측 확인:

```
'좌측 정렬해줘'   → stage5/stage6 정규식 전부 미매칭
'우측으로 정렬'   → stage5/stage6 정규식 전부 미매칭
'배분 정렬해줘'   → stage5/stage6 정규식 전부 미매칭
'왼쪽 정렬해줘'   → 전부 매칭
```

**사용자에게 보이는 결과:** 편집 모드에서 "좌측 정렬해줘"는 인식되지 않지만,
같은 표현이 Excel app_command 경로에서는 동작한다. 한글은 "배분"을 받지만
Word·PowerPoint 어댑터는 `AppActionBlocked`를 던진다. 동일 의도가 앱과 경로에
따라 성공/실패가 갈린다.

이것은 제품 Goal 기준 1 **"대표 표현과 변형 표현을 이해한다"**에 대한 직접적인
반례다. `template_matcher.py:7`은 "좌측"·"우측"을 알려진 어휘로 등록해 두고 있어,
의도는 지원인데 구현이 따라오지 못한 상태다.

**권고:** `engine/vocabulary/alignment.py`(가칭) 단일 모듈에 정규 값과 별칭
테이블을 두고, 7곳 전부가 이를 참조하게 한다. 앱별 차이(Word는 `WD_ALIGNMENTS`
숫자 매핑, PowerPoint는 1-based)는 정규 값 → 앱 상수 매핑 테이블로 분리한다.

---

### [H-2] 확인(승인) 취소 어휘가 16곳에 복제되고 드리프트했다 — 안전 경로

취소 별칭 목록이 6개 모듈 16곳에 하드코딩되어 있고 내용이 다르다.

| 변형 | 등장 위치 |
|---|---|
| `["아니","아니요","그만","하지마"]` | factory 221/316/384/528/604, registry 124/185, decision_engine 186 |
| `["아니","아니요","그만","취소"]` | registry 233/270/347 |
| `["취소","그만","하지마","아니요"]` | factory 69, controller 1372 |
| `["아니","아니요","그만","하지마","취소"]` | factory 454 |
| `["아니","아니오","아니요","틀렸어","취소","버려","ㄴㄴ","ㄴ","하지마"]` | [command_pipeline.py:21](engine/pipeline/command_pipeline.py:21) |

매칭은 관대하지 않다. [pending_confirmation_manager.py:268](engine/managers/pending_confirmation_manager.py:268)의
`resolve_text()`는 정규화 후 **정확히 별칭 집합에 포함될 때만** 매칭한다.

**결과:**
- `"하지마"`는 Excel 명확화 확인([factory:384](engine/confirmation/confirmation_factory.py:384))에서는
  취소되지만, PDF 외부 전송 확인([registry:233](engine/confirmation/confirmation_registry.py:233))에서는
  **취소되지 않는다.**
- `"아니오"`(표준 맞춤법), `"ㄴㄴ"`, `"버려"`는 pipeline 경로에서만 인식되고
  어떤 confirmation 옵션에서도 취소로 매칭되지 않는다.

PDF 외부 전송은 **문서 내용이 외부 AI로 나가는 승인**이다. 사용자가 습관적으로
쓰던 취소어가 하필 이 확인에서만 먹히지 않는 것은 안전 관점에서 가장 나쁜
드리프트 방향이다.

**권고:** `CANCEL_ALIASES` / `AFFIRM_ALIASES` 상수를 한 곳(예:
`engine/confirmation/vocabulary.py`)에 정의하고 전 지점이 참조한다. 추가로
`pending_confirmation_manager`가 `cancel: True` 옵션에는 표준 취소 별칭을
자동 병합하도록 하면, 옵션 정의자가 빠뜨려도 안전 기본값이 보장된다.

---

### [M-1] LLM provider 전송 계층 중복 + 복원력 비대칭

[gemini_provider.py:205](engine/llm/gemini_provider.py:205)는 `max_retries = 3`으로
503·429를 재시도한다. [openai_provider.py:229](engine/llm/openai_provider.py:229)는
**재시도가 없다.** HTTPError를 그대로 사용자 메시지 문자열로 반환한다.

두 함수는 각각 239줄·222줄이며 budgets 파일 스스로 동일한 사유를 기록해 두었다
("Transport, streaming, and response mapping need separation"). 즉 중복이 인지되어
있으나 미해결이고, 그 사이에 동작 비대칭이 자랐다. OpenAI를 쓰는 사용자는 rate
limit에서 바로 실패를 본다.

**권고:** 재시도·타임아웃·TLS 분류·스트리밍 파싱을 `engine/llm/transport.py`로
추출하고, provider는 요청 본문 구성과 응답 매핑만 담당하게 한다. 두 함수의
budgets 예외 461줄이 함께 해소된다.

---

### [M-2] 모듈 예산이 "현재값 동결"이라 축소 압력이 없다

[maintenance_audit.py:27-38](verification/maintenance_audit.py:27)의
`DEFAULT_MODULE_LINE_BUDGETS`는 11개 모듈을 **정확히 현재 줄 수**로 고정한다
(`business_workflow.py: 2477`, `excel_adapter.py: 2349`, …). 함수 예외 43개도
마찬가지로 현재 크기에 동결되어 있다.

이 설계는 **악화는 확실히 막지만 개선을 유도하지 않는다.** 총 초과분은
2,206줄이고, 최대 항목은 343줄(`DeterministicFailureClassifier.classify`),
252줄(`_RiskVisitor.visit_Call`), 248줄(`edit_success_message`)이다.
2026-11-30 만료일이 다가오면 43건이 한꺼번에 압력으로 돌아온다.

**권고:** 만료일 일괄 지정 대신 소유자별 분기 배분으로 바꾸고, 예산을 "현재값"이
아니라 "현재값 - N"으로 갱신하는 하향 래칫을 도입한다. 상위 5개(1,290줄)만
처리해도 총 초과분의 58%가 사라진다.

---

### [M-3] GUI JavaScript 3,443줄에 실행되는 테스트가 0이다

CI의 JS 검증은 `node --check`(문법)뿐이다.
[test_gui_markdown_safety.py](tests/unit/test_gui_markdown_safety.py)는 스스로
"pin the sanitization patterns … in the same text-inspection style"이라고 밝힌
**소스 텍스트 검사**이며, `sanitizeRenderedHtml()`을 실제로 호출하지 않는다.

즉 sanitizer의 **로직 결함**(mutation XSS, `querySelectorAll` 순회 중 DOM 변형에
따른 노드 누락, 중첩 치환 순서)은 현재 어떤 게이트로도 잡히지 않는다.
[renderer.js:29](gui/js/chat/renderer.js:29)에 `if (!template.content.contains(el)) continue;`
방어가 있는 것으로 보아 작성자도 이 위험을 인지하고 있었다.

부수적으로 escape 헬퍼도 2곳에 중복 정의되어 있다
([renderer.js:3](gui/js/chat/renderer.js:3), [macros.js:9](gui/js/dictionaries/macros.js:9)).

**권고:** 개발 의존성에 `node:test` + `jsdom` 수준의 최소 구성을 추가해
`sanitizeRenderedHtml()`에 XSS 페이로드 배터리(스크립트 태그, `onerror`,
`javascript:` href, SVG/MathML mutation, 중첩 `<template>`)를 실행 검증한다.
새 런타임 의존성 없이 devDependency로만 가능하다.

---

### [M-4] 커버리지 측정 게이트가 없다

`pyproject.toml`, `requirements-dev.in`, CI 어디에도 coverage 도구가 없다.
1,216 테스트·4,561 assertion은 양적으로 충분해 보이지만, **어느 코드가 한 번도
실행되지 않는지 아무도 모른다.** 특히 43개 장문 함수의 분기(343줄 classify의
분류 규칙들)가 실제로 다 밟히는지 확인할 수단이 없다.

**권고:** `coverage`를 dev-lock에 추가하고 게이트 없이 리포트만 먼저 수집한다.
수치를 본 뒤 하한선을 정하는 순서가 안전하다(처음부터 임계값을 걸면 형식적
테스트를 유발한다).

---

### [L-1] 타입 주석·docstring 밀도가 낮다

AST 실측:

| 지표 | 값 |
|---|---|
| 반환 타입 주석 | 692 / 2,112 (**33%**) |
| 파라미터 타입 주석 | 678 / 3,924 (**17%**) |
| docstring | 373 / 2,410 (**15%**) |

신규 모듈(`engine/pdf/`, `engine/storage/`, `verification/`)은 잘 붙어 있고
레거시 코어(adapters, stage5/6/10)가 비어 있는 전형적 패턴이다. 298개 클래스와
2,112개 함수 규모에서 파라미터 타입 17%는 리팩터링 시 안전망이 얇다는 뜻이다.

**권고:** 전면 도입 대신 **경계 우선** — `contracts.py`, `__init__.py`의 `__all__`
노출 함수, adapter의 `prepare`/`execute`/`undo` 시그니처부터 채운다. ruff에
`ANN201`(공개 함수 반환 주석)만 선택적으로 켜는 것도 방법이다.

### [L-2] 무음 예외 103곳 중 로깅되는 것은 24곳

`except Exception:` 316건 중 103건이 즉시 `pass`/`return None`으로 끝난다.
4장에서 밝혔듯 다수는 검증으로 닫힌 정당한 Win32/COM best-effort다. 다만
**어느 시도가 실패했는지 기록이 남지 않아** 현장 진단 시 재현이 어렵다.
`logger.debug(..., exc_info=True)` 한 줄이면 비용 없이 해결된다.
최다 밀집: `native_bridge.py`(7건), `business_workflow.py`(5건).

### [L-3] 문서 스프롤

`docs/` 50개 파일 468KB + 루트 마크다운 163KB. `CHANGELOG.md` 단독 78KB(1,051줄).
같은 주제의 감사 보고서가 병렬로 존재한다(`JARVIS_FINAL_GOAL_MAINTAINABILITY_AUDIT_2026-08-03`,
`JARVIS_FULL_MAINTAINABILITY_REALIZATION_AUDIT_2026-08-04`,
`JARVIS_GOAL_MAINTAINABILITY_REMEDIATION_AUDIT_2026-08-04`,
`JARVIS_MAINTAINABILITY_REALIZATION_REMEDIATION_REPORT_2026-08-04` — 하루 사이 3건).
어느 것이 현재 유효한 판단인지 파일명만으로 알 수 없다.

**권고:** 완료된 단계별 보고서를 `docs/archive/`로 옮기고, 활성 문서
(`JARVIS_PRODUCT_GOAL`, `JARVIS_ARCHITECTURE_CONTRACT`, `KNOWN_LIMITATIONS`,
최신 감사 1건)만 루트 `docs/`에 남긴다. 아카이브에는 상태 헤더를 붙인다.

### [L-4] `outputs/` 워크스페이스 잔여물

strict 감사의 유일한 warning. `.gitignore`에 있으므로 추적 오염은 없고,
`verification/cleanup_workspace.ps1`이 이미 존재한다. 정리 실행만 하면 된다.

---

## 5. 지표 요약

| 항목 | 값 |
|---|---|
| 추적 파일 | 496 |
| Python 총 줄 수 | 102,326 |
| `engine/` | 189 파일 / 54,773줄 / 298 클래스 / 2,112 함수 |
| `tests/` | 137 파일 / 32,250줄 / 1,213 테스트 / 4,561 assertion |
| `verification/` | 63 파일 / 15,145줄 |
| `gui/js` (vendor 제외) | 3,443줄 / **실행 테스트 0** |
| 100줄 이상 함수 | 43개 (예산 초과 합계 2,206줄) |
| 최대 모듈 | `business_workflow.py` 2,477줄 |
| 계층 위반 (parser 역참조) | **0건** |
| 순환 회피용 함수 내 import | 37건 (17건은 `runtime_services.py`, 의도적 포트) |
| TODO / FIXME / HACK | **0건** |
| 한국어 문자열 리터럴 | 4,677개 (고유 3,499) — 중앙 카탈로그 없음 |
| 런타임 의존성 | 6개 직접 / 21개 lock, 전부 pin |

---

## 6. 권고 실행 순서

| 순위 | 항목 | 근거 | 규모 |
|---|---|---|---|
| 1 | **[H-2]** 확인 취소/승인 어휘 단일화 + `cancel:True` 자동 병합 | 안전 승인 경로의 동작 불일치, PDF 외부 전송 포함 | 소 |
| 2 | **[H-1]** 정렬 어휘 단일 모듈화 | Goal 기준 1 반례, 사용자 표면 실패 | 중 |
| 3 | **[M-3]** GUI sanitizer 실행 테스트 도입 | 보안 로직에 실행 검증 부재 | 소 |
| 4 | **[M-1]** LLM transport 추출 (재시도 대칭화) | OpenAI rate limit 즉시 실패 + 461줄 예산 해소 | 중 |
| 5 | **[M-4]** coverage 리포트 수집(게이트 없이) | 사각지대 가시화가 이후 판단의 전제 | 소 |
| 6 | **[M-2]** 예산 하향 래칫 + 만료일 분산 | 11월 일괄 만료 폭탄 회피 | 중 |
| 7 | **[L-1]** 공개 경계 타입 주석 | 리팩터링 안전망 | 중 |
| 8 | **[L-3]** 문서 아카이브 정리 | 유효 문서 식별 비용 | 소 |

1~3번은 각각 독립적이고 국소적이며, 기존 게이트를 깨지 않고 적용 가능하다.

---

## 7. 총평

이 코드베이스의 유지보수 체계는 **"측정하는 것은 지킨다"**는 원칙 위에 잘 서
있다. 문제는 측정 대상이 전부 *양적 지표*(줄 수, 참조 방향, 비밀값 패턴)라는
점이다. H-1·H-2는 어떤 게이트도 위반하지 않으면서 사용자에게 실제 실패를
만들어낸다 — 43개 장문 함수보다 이쪽이 제품에 더 해롭다.

다음 래칫은 크기가 아니라 **"같은 개념이 두 곳에 정의되어 있지 않은가"**를
측정해야 한다. 정렬 별칭·취소 별칭·escape 헬퍼가 모두 같은 패턴이라는 점이
이를 뒷받침한다. `maintenance_audit.py`에 어휘 상수 중복 검사기를 추가하는
것이 가장 비용 대비 효과가 큰 다음 한 걸음이다.
