# Prototype 1.1 12단계 자가 진단·안전한 개선 제안

## 판정

명령 실행 실패를 익명화된 진단 사건으로 묶고, 실패 지점·원인 후보·재현 규격·
필수 테스트·수정 제안을 조회하는 12단계 진단 계층을 구현했다.

진단 계층은 코드를 자동 수정하지 않는다. 실행 중인 EXE를 바꾸지 않으며,
별도 승인 전에는 worktree 생성·테스트용 코드 변경·전체 회귀·새 EXE 빌드를
실행하지 않는다.

## 수집 흐름

```text
명령 실행 실패
→ 성공/취소/확인 대기/busy 제외
→ 명령·대상·오류 원문 제거
→ 구조 식별자와 해시만 저장
→ 같은 구조의 실패를 fingerprint로 그룹화
→ 원인 분류와 합성 fixture 재현 규격 생성
→ 수정·회귀 테스트 제안 생성
```

한 사건에는 다음 정보가 들어간다.

- 명령 해시와 길이, 요청 모드
- 해석된 action·operation·앱 유형
- 문서 확장자, 대상·문서·세션 해시
- 실패 단계, 오류 유형, 재시도 가능 여부
- 기대 상태와 실제 검증 상태
- 허용 목록으로 제한한 이벤트 순서
- JARVIS·Python·Windows·Excel·Word·PowerPoint·한글 설치 버전
- 원인 분류, 원인 후보, 권장 수정, 신뢰도
- 합성 fixture용 익명 재현 정보와 필수 회귀 테스트

Office 버전은 레지스트리와 실행 파일 버전 정보로만 읽는다. 버전 수집을 위해
사용자 Office 앱을 시작하거나 실행 중인 앱에 연결하지 않는다.

## 개인정보 경계

저장 경로는 다음과 같다.

```text
%LOCALAPPDATA%\Jarvis\data\diagnostic_incidents.json
```

최대 200개의 서로 다른 실패 그룹을 보존하고 같은 fingerprint의 재발은 발생
횟수와 마지막 시각만 갱신한다. 저장하지 않는 값은 다음과 같다.

- 사용자 명령 원문
- 파일·폴더 전체 경로와 계정 이름
- 문서 본문·표 데이터·VBA 코드
- 오류 메시지와 응답 문장 원문
- 선택 텍스트와 대상 이름 원문
- COM 객체

기존 `execution_diagnostics.json`도 신규 기록부터 같은 개인정보 경계를
적용한다. 이전 형식의 원문 기록과 `.bak`이 있으면 시작 시 구조 정보·해시만
남기는 형식으로 다시 저장한다.

## 원인 분류

현재 분류는 자유 문장 AI 추론이 아니라 오류 유형·상태·실패 단계·구조화된
오류 표식을 이용한 결정적 규칙이다.

| 분류 | 대표 조건 |
| --- | --- |
| `workflow_step_failure` | 워크플로·편집 실패 단계가 있음 |
| `verification_mismatch` | 실행 후 재조회 검증 불일치 |
| `context_changed` | 문서·선택 fingerprint 변경 |
| `permission_or_security` | 읽기 전용·보호·보안 센터·권한 차단 |
| `office_busy` | COM 호출 거절·Office busy |
| `timeout` | 제한 시간 초과 |
| `dependency_missing` | 모듈·COM 등록·앱 구성요소 없음 |
| `target_unavailable` | 문서·범위·UI 대상 없음 |
| `validation_block` | 입력·허용 목록·안전 정책 차단 |
| `execution_exception` | 나머지 구조화되지 않은 실행 예외 |

원인 후보는 조사 순서를 안내하는 가설이며 실제 근본 원인 확정으로 표시하지
않는다.

## 사용자 명령과 API

명령 모드에서 다음과 같이 요청할 수 있다.

```text
최근 오류 진단해줘
최근 실패 원인
자가진단
```

보고서는 어느 단계에서 실패했는지, 어떤 범주인지, 어떤 수정이 필요한지,
어떤 테스트가 통과해야 하는지를 보여준다. 실패 기록이 없을 때의 진단 요청은
새로운 실패 사건으로 저장하지 않는다.

로컬 Eel API:

- `get_diagnostic_incidents(limit, status)`
- `get_self_diagnostic_report(incident_id)`
- `get_diagnostic_health_summary()`
- `set_diagnostic_incident_status(incident_id, status)`
- `get_remediation_proposal(incident_id)`

사건 상태는 `new`, `acknowledged`, `resolved`, `dismissed` 중 하나다.

## 수정 제안의 승인 경계

진단 보고서가 만드는 개선 흐름은 제안일 뿐이다.

```text
오류 수집
→ 합성 fixture로 실패 재현 계획
→ 원인 후보 분석
→ 사용자 수정 승인
→ 현재 실행본과 분리한 Git worktree
→ 실패 재현 테스트를 먼저 작성
→ 최소 코드 수정
→ 관련 테스트와 전체 회귀
→ 사용자 빌드 승인
→ 새 EXE를 별도 경로에 빌드
→ 검증 실패 시 기존 current 실행본 유지
```

`automatic_execution_allowed`와 `running_binary_mutation_allowed`는 항상
`false`다. 사건 조회·상태 변경도 코드나 실행 파일을 바꾸지 않는다.

## 검증

자동 테스트는 다음을 확인한다.

- 성공·취소·확인 대기·busy를 실패 사건으로 만들지 않음
- 명령·경로·이메일·문서 내용이 사건과 실행 진단에 남지 않음
- 레거시 실행 진단과 백업의 원문 제거
- 동일 구조 실패 그룹화와 보존 한도
- 10개 원인 범주의 결정적 분류
- 워크플로 실패 단계와 재시도 정보 보존
- 자연어 진단과 조회·상태·수정 제안 API
- 빈 진단 요청이 가짜 실패를 만들지 않음
- 자동 수정·worktree·빌드·실행 EXE 변경 금지

실패 주입 검증 명령:

```powershell
python -m verification.prototype11_stage12_probe
```

2026-07-17 합성 워크플로 실패를 두 번 주입한 검증에서 중복 그룹화, 실패 단계,
원인 후보, 합성 재현 규격, Office 버전 키, 원문 부재, 분리 worktree·테스트 우선·
전체 회귀·별도 빌드 승인 정책, 무자동수정, 실행 바이너리 불변이 모두 통과했다.
사용자 문서와 Office 앱은 사용하지 않았다.

같은 날 전체 자동 회귀는 614개 중 611개 통과, 외부 AI 자격 증명이 필요한
3개 skip, 실패 0개였다.

## 현재 제한

- 원인 분석은 결정적 후보 분류이며 스택 추적 전체나 문서 내용을 보관하지
  않으므로 모든 근본 원인을 자동 확정할 수 없다.
- 실제 코드 수정·worktree·전체 회귀·EXE 빌드는 사용자가 별도로 승인한 다음
  단계다.
- Office 설치 버전은 등록 정보가 없는 포터블·가상화 설치에서 `null`일 수 있다.
- 서로 다른 예외가 같은 구조 식별자를 가지면 한 실패 그룹으로 묶일 수 있다.
