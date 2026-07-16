# Prototype 1.0 2단계 편집 계약과 상태 머신

## 구현 목표

편집모드가 기존 명령모드로 흘러가거나 AI가 임의 코드를 만들어 실행하지
않도록 백엔드 경계를 먼저 만든다. 파일 연결과 실제 편집 세션 생성은 3단계에서
이 계약 위에 구현한다.

## 모드 권한

| 모드 | AI 응답 | 문서 읽기 | 문서 변경 | 컴퓨터 전체 작업 | 동적 코드 |
|---|---:|---:|---:|---:|---:|
| 대화 | 허용 | 차단 | 차단 | 차단 | 차단 |
| 질문 | 허용 | 허용 | 차단 | 차단 | 차단 |
| 명령 | 허용 | 허용 | 기존 기능 허용 | 허용 | 기존 안전검사 적용 |
| 편집 | 허용 | 허용 | 연결 문서만 허용 | 차단 | 차단 |

알 수 없는 mode는 명령모드로 간주하지 않고 백엔드에서 `blocked`로 반환한다.
질문·대화모드 AI가 `open_app` 같은 실행 작업을 반환해도 실제 실행하지 않는다.

기존 Excel·한글 명령은 회귀를 막기 위해 명령모드에 유지한다. 향후 편집모드
전환이 완료된 기능부터 연결 문서 전용 경계로 이동한다.

## 편집 요청 계약

API의 기존 인자를 유지하면서 마지막 `edit_context` 인자를 추가했다.

```json
{
  "mode": "edit",
  "text": "이 문단을 줄여줘",
  "edit_context": {
    "edit_session_id": "edit-session-1",
    "document_fingerprint": "64자리 SHA-256",
    "request_id": "선택값"
  }
}
```

다음 조건을 만족하지 않으면 편집 파이프라인에 진입하지 않는다.

- 1~10,000자의 유효한 요청 텍스트
- 별도의 `edit_session_id`
- 16~128자리 16진수 문서 fingerprint
- NUL 문자와 잘못된 식별자 없음
- 명령모드의 채팅 `session_id`를 편집 세션 ID로 대신 사용하지 않음

현재 2단계에서는 유효한 요청이라도 실제 편집 세션 핸들러가 연결되지 않았으면
“편집할 문서를 먼저 연결해주세요”로 차단한다. 이 요청이 일반 명령 분석이나
앱 실행으로 내려가는 폴백은 없다.

## 준비된 편집 작업

LLM이나 요청 텍스트가 COM·Python 코드를 직접 실행하지 않는다. 신뢰된 앱
어댑터가 지원 작업 목록 중 하나를 다음 구조로 만든다.

```json
{
  "schema_version": 1,
  "action_id": "action-1",
  "request_id": "request-1",
  "edit_session_id": "edit-session-1",
  "app_type": "word",
  "operation": "replace_text",
  "target": {"kind": "range", "start": 120, "end": 180},
  "arguments": {"text": "변경할 문장"},
  "preconditions": [
    {"kind": "text_digest", "value": "..."}
  ],
  "risk_level": "medium",
  "requires_approval": true,
  "verification_plan": {"method": "read_back"},
  "rollback_plan": {"strategy": "range_snapshot"},
  "context_fingerprint": "..."
}
```

안전 규칙:

- 모든 값은 JSON 직렬화 가능해야 하며 COM 객체는 저장할 수 없음
- 직렬화 크기는 작업당 최대 256KB
- `operation`은 어댑터의 `supported_operations`에 있어야 함
- 고위험 작업은 `requires_approval=true` 강제
- 검증 `method`와 복구 `strategy` 필수
- 준비 후 실행 직전에 fingerprint 재확인
- 실행 결과를 다시 읽어 검증하기 전에는 성공 처리하지 않음
- 실행 또는 검증 실패 시 어댑터 복구를 시도하고 결과를 상태에 기록

## 상태 머신

```text
DISCONNECTED
→ ATTACHING
→ READY
→ PREPARING
→ PREPARED
→ APPROVAL_REQUIRED ─┐
                    ├→ EXECUTING
PREPARED ────────────┘
→ VERIFYING
→ COMMITTED

문맥 변경: STALE_CONTEXT
실행 실패: FAILED 또는 ROLLED_BACK
```

허용되지 않는 상태 전이는 거부한다. 각 전이에는 증가하는 revision이 있으며,
호출자가 가진 revision이 오래됐으면 `state_conflict`로 차단한다. 상태 이력에는
문자열·ID·오류만 저장하고 COM 객체는 저장하지 않는다.

## 구현 위치

- `engine/edit_mode/contracts.py`: 요청·작업·결과·어댑터 계약
- `engine/edit_mode/permissions.py`: 모드별 백엔드 권한
- `engine/edit_mode/state_machine.py`: 상태와 revision 전이
- `engine/edit_mode/coordinator.py`: 준비·실행·검증·복구
- `engine/pipeline/edit_route.py`: 명령모드와 분리된 편집 요청 진입점

## 2단계 완료 기준

- 네 가지 mode 외 값은 백엔드 차단
- 편집 요청이 명령·AI 명령 폴백으로 내려가지 않음
- 질문·대화모드에서 외부 상태 변경 차단
- COM 객체와 비직렬화 값이 PreparedAction에 들어가지 않음
- 고위험 승인 강제
- 준비 후 문맥 변경 차단
- 실행 후 재조회 검증
- 검증 실패 시 복구 상태 기록
- 기존 질문·명령·Excel·한글 기능 회귀 없음

3단계에서는 `FileIntakeManager`와 `EditSessionManager`가 실제 세션을 생성하고
`edit_mode_handler`에 연결한다.
