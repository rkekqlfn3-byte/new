# Office 어댑터 확장 계약

> Prototype 1.0 편집모드의 신규 Word·PowerPoint 어댑터는
> `engine.edit_mode.contracts.EditAdapter`와 `EditPreparedAction`을 기준으로
> 구현한다. 아래 계약은 기존 Excel·HWP 명령 어댑터의 단계적 호환 경계다.

Word·PowerPoint를 추가할 때 지켜야 할 최소 경계다. 현재 Excel·HWP 공개
인터페이스와 저장 스키마는 바꾸지 않으며, 단계적으로 이 계약에 맞춘다.

```text
prepare(request) -> PreparedAction
execute(prepared_action) -> serializable result
verify(prepared_action, result) -> bool
rollback(prepared_action) -> bool
fingerprint(context) -> SHA-256 string
```

- `prepare`는 활성 문서 상태를 읽되 외부 상태를 변경하지 않는다.
- `PreparedAction`에는 COM 객체를 넣지 않고 JSON 직렬화 가능한 값만 넣는다.
- `execute`는 준비 시점 fingerprint가 현재 문맥과 같은지 먼저 확인한다.
- `verify`가 성공하기 전에는 사용자에게 성공으로 보고하지 않는다.
- 변경 도중 실패하면 가능한 작업만 `rollback`하고, 실패도 검증 결과에 남긴다.
- `fingerprint`는 정렬된 직렬화 상태의 SHA-256 대문자 16진수로 통일한다.

현재 런타임 registry가 요구하는 호환 표면은
`NativeAppAdapter.prepare(operation, params)`와
`NativeAppAdapter.execute(prepared_action)`다. 새 전체 계약으로 전환하는 동안
Excel·HWP의 COM prepare·execute·rollback 흐름은 각 기존 façade에 유지한다.
