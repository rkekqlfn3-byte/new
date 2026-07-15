# JARVIS V1 보정 감사 보고서

- 갱신일: 2026-07-15 (KST)
- 대상: 현재 개발 소스, 보존 실행본, 직전 백업, R0~R4 변경
- 후보 버전: `1.1.0-rc.1`
- 현재 판정: **검증 진행 중 — 새 릴리스 교체 보류**

## 1. 현재 결론

초기 감사의 P1·P2 코드 지적은 R0~R4에서 모두 수정했거나 검증 가능한
형태로 정리했다. 실행 폴더와 개발 저장소를 분리했고, PDF 취약 의존성,
시스템 명령 결과 오보고, bare `except`, GUI 동적 삽입 경계, 핵심 대형 함수
복잡도를 조치했다.

다만 현재 소스의 자동 회귀와 실제 Excel·HWP·후보 EXE 검증은 사용자가 정한
순서에 따라 R6에서 마지막으로 실행한다. 따라서 지금은 코드 조치 완료를
“릴리스 완전 통과”로 판정하지 않으며, 기존 정상 실행본을 그대로 보존한다.

## 2. 절대 보존 기준선

### 현재 실행본

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\current\Jarvis\Jarvis.exe`
- SHA-256: `B237B5678C147988431CE93EAE66904A5A12474985DA6154B1C2D57C42FE8455`
- 배포 폴더: 120개 파일, 34,721,530바이트
- 빌드 소스 커밋: `a096e9303e6f339e1c9c57472183f5c6d901af39`

### 직전 백업

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\previous\Jarvis.zip`
- SHA-256: `D4F485386AC47AB0A9C2B4697462B5BB5FDA7D6121AE04E4C50AE1D6B4599116`
- 크기: 16,808,381바이트

R0에서 복사 전후 해시, 기존 EXE 기동, 사용자 데이터 33개 파일의 바이트
무변경을 확인한 뒤 개발 저장소의 중복 `dist`·`releases`를 제거했다.

## 3. 지적별 조치 상태

| ID | 초기 지적 | 현재 상태 | 최종 게이트 |
| --- | --- | --- | --- |
| P1-SEC-001 | `PyPDF2 3.0.1` 서비스 거부 취약점 | `pypdf 6.14.2`로 교체, 입력 상한과 회귀 추가 | R6 `pip-audit`, PDF 회귀 |
| P1-OPS-002 | 실행·개발 폴더 역할 충돌 | `JARVIS_RUNTIME` 분리, 개발 도구 복원 | R6 최종 정리 확인 |
| P1-DOC-003 | 문서와 실제 상태 불일치 | README·CHANGELOG·제한·RULEBOOK 갱신 | R6 결과 수치 반영 |
| P2-CODE-004 | 핵심 함수·모듈 과대 | route·executor·factory·service로 책임 분리 | R6 전체 회귀 |
| P2-CODE-005 | 종료 명령 실패도 성공 반환 | 인자 리스트 실행과 반환 코드 검증으로 교체 | R6 회귀 |
| P2-SEC-006 | GUI 동적 HTML 삽입 경계 | DOM 조립·숫자 정규화 및 악성 문자열 회귀 추가 | R6 JS·회귀 |

## 4. 구조 목표 결과

| 항목 | 조치 전 | 현재 |
| --- | ---: | ---: |
| `command_pipeline.execute` | 335줄 | 98줄 |
| `batch_executor.execute` | 399줄 | 39줄 |
| `parser.py` | 1,615줄 | 996줄 |

명령 파이프라인, AI batch, confirmation 생성, 학습 재실행을 실제 책임 단위로
분리했다. 기존 parser와 adapter 외부 호출 표면은 호환 façade로 유지했다.
Excel·HWP에서 COM과 무관한 상태 fingerprint, 범위 해석, 서식 비교, 문자열
치환 helper를 분리하고 Word·PowerPoint용 확장 계약을 문서화했다.

## 5. 테스트 상태

R0~R4에서는 회귀 테스트 코드를 복원·추가했지만 실행하지 않았다. 과거
420개 테스트 및 실제 Office 통과 이력은 보존 실행본의 참고 자료일 뿐 현재
소스의 검증 결과로 계산하지 않는다.

R6에서 다음을 한 번에 실행한다.

- Python·JavaScript·JSON·보안 정적 검사
- `pip check`, `pip-audit`, 비밀정보·개인 데이터 혼입 검사
- unit → integration → windows → 선택적 live 전체 회귀
- 실제 Excel·HWP 시나리오와 후보 EXE·동결 워커 검증
- 기존·격리 사용자 데이터 기동과 사전·사후 SHA-256 비교

실패가 한 건이라도 나오면 수정·재빌드 후 R6 전체를 처음부터 다시 실행한다.

## 6. 남은 릴리스 제한

- Word·PowerPoint 네이티브 편집은 이번 보정 범위에 포함하지 않았다.
- 외부 Gemini·OpenAI는 실제 키 사용 승인이 없으면 skip으로 기록한다.
- 현재 EXE는 Authenticode 미서명이다. 개인 로컬 사용은 SHA-256 기록 조건으로
  허용하지만 타인·인터넷 배포는 서명과 타임스탬프 전까지 보류한다.
- 악성 PDF 퍼징과 전문 침투 테스트는 이번 범위가 아니다.

## 7. 다음 판정

R5에서 릴리스 태그와 동일 commit의 깨끗한 후보 EXE를 만들고, R6 전체 게이트
통과 후에만 기존 current를 previous로 승격하고 후보를 새 current로
교체한다. 최종 EXE·ZIP SHA-256과 실제 테스트 수치는 그 시점에 이 보고서에
확정한다.
