# JARVIS V1 보정 최종 감사 보고서

- 최종 검증일: 2026-07-15 (KST)
- 릴리스: `1.1.0-rc.2`
- 릴리스 태그: `v1.1.0-rc.2`
- 빌드 commit: `ceb6c17d14f495d167a3c12417464c124bde0d90`
- 판정: **개인용 로컬 릴리스 통과 — 외부 배포 보류**

## 1. 최종 결론

초기 감사의 P1·P2 코드 지적을 모두 조치했고, R0~R6 전체 계획을 완료했다.
두 번째 R6 전체 검증에서 자동 회귀, 실제 Excel·한글, 후보 EXE, 의존성
보안, 배포 비밀정보, 기존 사용자 데이터 무변경을 모두 통과했다.

현재 실행 폴더는 RULEBOOK대로 current 1개와 previous 1개만 보존한다.
Word·PowerPoint 미구현, 외부 AI 실계정 skip, Authenticode 미서명은 제품
결함과 분리된 범위·배포 제한으로 기록한다.

## 2. 최종 실행본과 백업

### current

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\current\Jarvis\Jarvis.exe`
- 버전: `1.1.0-rc.2`
- SHA-256: `12116595E78A4C63760F982CB872E94367EE6865A3B6993F08CF3231EF8C274F`
- 배포 폴더: 120개 파일, 34,935,324바이트
- build identity commit: `ceb6c17d14f495d167a3c12417464c124bde0d90`
- `git_dirty`: `false`
- 릴리스 빌드 ZIP SHA-256: `52327A4E57F9C984264C75F854BEC479EC9FC73F3C3F44CE144552995A81B530`
- Authenticode: `NotSigned`

### previous

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\previous\Jarvis.zip`
- 내용: 교체 직전 current 실행본 120개 파일
- SHA-256: `25E236ABD5AFF4A47E1366D5F13B40F100F91A2CAFB581B676B4A88994E15E4F`
- ZIP 무결성 검사: 오류 없음

## 3. 감사 지적 종결

| ID | 초기 지적 | 최종 조치 | 판정 |
| --- | --- | --- | --- |
| P1-SEC-001 | `PyPDF2 3.0.1` 서비스 거부 취약점 | `pypdf 6.14.2`, 파일·페이지·문자 상한, 회귀·감사 | 종결 |
| P1-OPS-002 | 실행·개발 폴더 충돌 | `JARVIS_RUNTIME` 분리, 테스트·빌드 도구 복원 | 종결 |
| P1-DOC-003 | 문서와 실제 상태 불일치 | README·변경 기록·제한·RULEBOOK·감사 최신화 | 종결 |
| P2-CODE-004 | 핵심 함수·모듈 과대 | route·executor·factory·service·Office helper 분리 | 종결 |
| P2-CODE-005 | 종료 명령 실패도 성공 반환 | `shell=False`, 반환 코드·오류 확인 | 종결 |
| P2-SEC-006 | GUI 동적 HTML 삽입 경계 | DOM 조립·숫자 정규화·악성 문자열 회귀 | 종결 |

## 4. 구조 정리 결과

| 항목 | 조치 전 | 최종 |
| --- | ---: | ---: |
| `command_pipeline.execute` | 335줄 | 98줄 |
| `batch_executor.execute` | 399줄 | 39줄 |
| `parser.py` | 1,615줄 | 996줄 |

기존 parser·adapter 외부 호출 표면과 저장 스키마는 유지했다. Excel·HWP에서
COM과 무관한 상태 fingerprint, 범위 해석, 서식 비교, 문자열 치환 helper를
분리했고 Word·PowerPoint 확장 계약을 타입과 문서로 확정했다.

## 5. R6 최종 검증 결과

### 정적·보안·의존성

- Python compile: 통과
- JavaScript 17개 구문 검사: 통과
- 기본 데이터 JSON 3개: 통과
- runtime bare `except`, `os.system`, `shell=True`: 0건
- `pip check`: 정상
- `pip-audit`: 알려진 취약점 0건
- 소스·배포 폴더·릴리스 ZIP 비밀정보 감사: 발견 0건
- build commit과 tag commit: 일치

### 자동 회귀

- 전체: 435개
- 통과: 432개
- skip: 3개 — 실제 키 승인이 필요한 외부 AI 라이브 테스트
- 실패: 0개

### 실제 앱

- Excel 종합 시나리오: 20/20 통과
- Excel 저장·재열기·수식·COM 왕복: 통과
- 한글 핵심 시나리오: 8/8 통과
- 검증용 Office 프로세스·임시 문서 잔여물: 0건
- 기존에 열려 있던 한글 객체: 보존

### 후보 및 설치 EXE

- 시작 후 프로세스 생존·종료: 통과
- frozen worker, 대용량 stdout/stderr, 실패 캡처, timeout: 통과
- 덮어쓰기 정책, 재시도, 공유 잠금, 취소 복구: 통과
- 동적 코드 preflight, 학습 생성·재로드: 통과
- TLS 정상 인증서 허용·자체 서명 인증서 차단: 통과
- Excel COM 쓰기·저장·재열기: 통과
- 신규 격리 데이터 기본 파일·빈 개인정보: 통과
- 기존 사용자 데이터 33개 파일 사전·사후 바이트 동일: 통과

첫 R6에서 검증 probe 3개가 현재 confirmation 정책·결과 구조를 따르지 않아
중단됐다. 제품 결함이 아닌 검증 코드 불일치를 수정하고 `rc.2`로 새로
빌드한 뒤 R6 전체를 처음부터 재실행했으며 위 결과는 두 번째 실행 결과다.

## 6. 남은 범위·배포 제한

- Word·PowerPoint 네이티브 편집은 이번 보정 범위 밖이며 아직 미구현이다.
- 외부 Gemini·OpenAI 실계정 테스트 3개는 실제 키 사용 승인이 없어 skip했다.
- 실행 파일은 Authenticode 미서명이다. 개인 로컬 사용은 SHA-256 기록 조건으로
  승인하며, 타인 또는 인터넷 배포는 코드 서명과 타임스탬프 전까지 보류한다.
- 악성 PDF 퍼징과 전문 침투 테스트는 이번 감사 범위가 아니다.

## 7. 최종 판정

- 개인용 로컬 실행: **승인**
- 새 current 설치: **완료**
- P1·P2 감사 지적: **0건 잔존**
- Word·PowerPoint 개발 착수: **가능**
- 타인·인터넷 배포: **보류 — Authenticode 서명 필요**
