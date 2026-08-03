# JARVIS 아키텍처 계약

- 상태: 활성
- 적용 기준: Python 소스
- 최종 갱신: 2026-08-03
- 상위 문서: `JARVIS_MAINTENANCE_MASTER_PLAN_2026-07-29.md`

이 문서는 모듈을 어디에서 호출해야 하는지, 상태를 누가 소유하는지, 실행 결과가 어떤 형식이어야 하는지를 코드 수준에서 고정한다.

## 1. 호출 방향

```text
UI / command API
    -> CommandParser
        -> ParserRuntimeServices (명시적 단기 포트)
            -> CommandPipeline
            -> parsing / local analyzer
            -> confirmation registry
            -> skill services
            -> native app router
                -> application adapter
```

- 상위 계층은 하위 계층의 공개 인터페이스만 호출한다.
- 하위 계층은 `CommandParser`를 인자로 받거나 영구 참조하지 않는다.
- `CommandParser`는 공개 경계에서 현재 협력자만 복사한
  `ParserRuntimeServices`를 만들며, 이 객체에는 parser/owner 또는 파서의 bound
  method를 넣지 않는다.
- adapter는 UI와 대화 기록을 직접 변경하지 않는다.

## 2. 공개 인터페이스

| 패키지 | 공개 진입점 | 책임 |
|---|---|---|
| `engine.parser` | `CommandParser` | 서비스 조립, 명령 실행 진입점 |
| `engine.runtime_services` | `ParserRuntimeServices` | 하위 실행 계층에 필요한 명시적 단기 의존성 포트 |
| `engine.pipeline` | `CommandPipeline` | 처리 단계와 라우팅 순서 |
| `engine.confirmation` | `ConfirmationRegistry` | 승인 상태 생성·보관·응답·재개 |
| `engine.app_actions` | `AppCommandRouter`, `AppActionRegistry`, adapter contracts | 실제 앱 작업 준비·실행·검증 |
| `engine.edit_mode` | `EditModeController` 및 edit contracts | 문서 연결, 세션, 선택 문맥, 연속 편집 |
| `engine.skills` | `SkillExecutor`, `SkillExecutionServices` 및 학습 서비스 | 스킬 선택·승인·실행·검증·기록 |
| `engine.execution_result` | result factory, normalizer, contract validator | 모든 실행 경계의 표준 결과 |

패키지 밖에서는 가능한 한 각 패키지의 `__init__.py`에 선언된 이름을 사용한다. 내부 구현 클래스가 공개 API로 필요해지면 먼저 `__all__`과 계약 테스트를 갱신한다.

## 3. 상태 소유권

| 상태 | 단일 소유자 | 접근 방식 |
|---|---|---|
| 승인 대기 항목 | `ConfirmationRegistry.pending` | registry의 queue/resolve API |
| 승인 응답 해석 | `ConfirmationRegistry.responses` | registry를 통한 처리 |
| 편집 세션 | `EditModeController.session_manager` | edit controller API |
| 현재 편집 문맥 | `EditModeController.context_manager` | edit controller API |
| 학습 검토 대기 목록 | `SkillLearningService.pending_macros` | parser compatibility property 또는 서비스 API |
| 스킬 실행 의존성 | `SkillExecutionServices` | 명시적 필드 주입 |
| 실행 취소·진단 | `ExecutionController` | controller API |

동일한 mutable 상태를 파서와 하위 서비스가 각각 복제해서 보관하면 안 된다. 호환성 property가 있더라도 실제 값은 지정된 소유자에게 전달되어야 한다.

## 4. 금지 계약

- `CommandParser`에 `_parse_native_*` 메서드를 추가하지 않는다.
- 조립되는 매니저 생성자에 `parser`, `_parser`, `owner`를 받지 않는다.
- 조립되는 매니저가 `self.parser`, `self._parser`, `self.owner`를 저장하지 않는다.
- pipeline·confirmation·AI action·learned replay 경계에 `CommandParser` 인스턴스를
  전달하지 않는다. `ParserRuntimeServices`만 전달한다.
- `engine/parser.py`를 1,000줄보다 크게 만들지 않는다.
- 승인 상태를 confirmation 계층 밖에서 별도로 생성·보관하지 않는다.
- 실행 성공을 사용자 메시지의 문자열 검색만으로 판단하지 않는다.

이 규칙은 `tests/unit/test_parser_composition.py`에서 자동 검사한다.

## 5. 실행 결과 계약

모든 실행 경계의 결과는 `engine.execution_result`의 factory 또는 `normalize_execution_result`를 사용한다.

필수 필드는 다음과 같다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `success` | `bool` | 작업의 최종 성공 여부 |
| `message` | `str` | 사용자에게 안전하게 표시할 메시지 |
| `action` | `str` | 수행하거나 시도한 행동 |
| `target` | `str` 또는 `None` | 실제 작업 대상 |
| `verified` | `bool` | 사후조건을 확인했는지 여부 |
| `status` | `str` | success, failed, blocked, confirmation_required 등 상태 |
| `error_type` | 허용된 문자열 또는 `None` | 표준 실패 유형 |
| `failed_step` | `int` 또는 `None` | 복합 작업 실패 단계 |
| `retryable` | `bool` | 안전한 재시도 가능 여부 |
| `data` | `dict` | 구조화된 추가 정보 |

추가 규칙:

- 성공 결과의 `error_type`은 항상 `None`이다.
- 확인·추가설명 대기 결과는 실패가 아니므로 `error_type`이 없고 `verified=False`다.
- 실행 완료와 검증 완료는 다르다. 사후조건을 읽어 확인하지 않았으면 `verified=False`다.
- 구형 문자열·mapping 결과는 오직 호환 경계에서만 정규화한다.
- `response`는 UI 전환을 위한 호환 필드이며 신규 코드는 `message`를 사용한다.

계약 검증에는 `is_execution_result()` 또는 `execution_result_contract_errors()`를 사용한다.

## 6. 변경 승인 기준

아래 변경은 이 문서와 계약 테스트를 같은 작업에서 갱신해야 한다.

- 공개 패키지 진입점 추가·삭제
- 상태 소유자 변경
- 실행 결과 필드나 오류 유형 변경
- 파서에서 새 하위 매니저 조립
- confirmation, edit session, skill lifecycle 변경
- 기존 금지 규칙의 예외 도입

예외는 임시 호환 기간, 호출자, 제거 조건을 기록하지 않으면 허용하지 않는다.
