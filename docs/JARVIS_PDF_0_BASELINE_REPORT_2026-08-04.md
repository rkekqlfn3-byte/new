# JARVIS PDF-0 기준선 완료 보고서

- 완료일: 2026-08-04 KST
- 작업 브랜치: `codex/pdf-capabilities`
- 범위: Python 소스와 자동 테스트만 포함
- EXE 빌드: 수행하지 않음

## 1. 완료 내용

PDF 기능을 실제 명령에 연결하기 전에 필요한 계약과 안전 경계를 먼저 고정했다.

### 구조화된 PDF 읽기 계약

- `PdfDocumentSnapshot`: 실제 경로와 본문을 제외한 문서 식별 정보와 구조 메타데이터
- `PdfPageText`: 페이지 번호, 임시 본문, 추출 방식, 문자 수, 본문 fingerprint, 경고 식별자
- `PdfExtractionResult`: 요청 페이지와 추출 페이지가 정확히 일치하는 페이지 단위 결과
- runtime 직렬화와 persistent evidence 직렬화를 분리
- persistent evidence에는 페이지 본문과 실제 파일 경로를 기록하지 않음

### 준비된 파일 작업 계약

- `PreparedFileAction`: `extract_pages`, `merge_documents`, `rotate_pages`의 공통 준비 계약
- 모든 PDF 파일 변경 작업은 사용자 승인을 필수로 요구
- 원본 fingerprint, 현재 상태, 예상 상태, 검증 계획, 복구 계획을 명시
- persistent evidence에는 출력 경로와 상태 원문 대신 fingerprint만 기록

### 출력 경로 안전 정책

- 로컬 절대 경로와 `.pdf` 확장자만 허용
- 원본 파일과 동일한 출력 경로 차단
- 존재하지 않는 상위 폴더와 심볼릭 링크 출력 차단
- 기존 출력은 명시적인 덮어쓰기 요청이 없으면 차단
- 덮어쓰기 승인이 있더라도 백업 필요 상태를 강제
- 사용 가능한 새 출력 이름을 파일 생성 없이 제안

### 소유 fixture

테스트 실행 중 임시 폴더 아래에 다음 PDF를 직접 생성하고 종료 시 제거한다.

- 2페이지 텍스트 PDF
- 이미지 전용 스캔 PDF
- 암호화 PDF
- 손상 PDF
- 90도 회전 PDF
- 표 모양 텍스트 PDF

사용자 PDF나 실제 업무 문서를 자동 테스트 입력으로 사용하지 않는다.

## 2. 기존 판독 동작 기준선

기존 `engine.document_reader.extract_text`의 PDF 동작을 characterization test로 고정했다.

- 텍스트 PDF: 전체 페이지 텍스트 추출
- 이미지 전용 PDF: 빈 문자열 반환
- 회전 PDF: 텍스트 추출
- 표 PDF: 토큰은 추출하지만 표 구조를 보장하지 않음
- 암호화 PDF: 파일 읽기 실패 응답
- 손상 PDF: 파일 읽기 실패 응답

이 테스트는 현재 동작을 옳다고 확정하는 테스트가 아니라, PDF-1에서 구조화된 오류와 페이지 단위 판독으로 변경할 때 회귀와 의도된 변경을 구분하기 위한 기준선이다.

## 3. 검증 결과

| 검사 | 결과 |
|---|---:|
| 표적 PDF·파일 작업·유지보수 테스트 | 30개 통과 |
| 전체 자동 테스트 | 1,080개 통과, 조건부 3개 건너뜀 |
| Python compileall | 통과 |
| Ruff 전체 lint 검사 | 통과 |
| 신규 PDF 파일 Ruff format 검사 | 통과 |
| `pip check` | 통과 |
| `git diff --check` | 통과 |
| 유지보수 strict 감사 | 오류 0, 기존 `outputs/` 경고 1 |

전체 테스트는 기존 1,051개를 유지했고 PDF-0 테스트 29개가 추가되었다.

## 4. 유지보수 경계

- `engine/pdf/*.py`와 `engine/file_actions/*.py`를 99줄 함수 예산 검사 대상에 등록했다.
- 새 테스트를 표준 unit·integration test runner 그룹에 등록했다.
- PDF 계약과 파일 변경 계약을 별도 패키지로 분리해 기존 `CommandParser`나 Office adapter에 책임을 추가하지 않았다.

## 5. 현재 가능한 것과 아직 불가능한 것

이번 단계는 안전한 기반을 고정한 단계다. 아직 자연어 PDF 명령이 실행되지는 않는다.

현재 가능한 것:

- PDF 읽기 결과를 페이지 단위 계약으로 표현할 준비
- PDF 파일 변경을 승인 전 준비 상태로 표현할 준비
- 출력 경로의 기본 안전 판정
- 소유 fixture 기반의 반복 가능한 회귀 테스트

아직 불가능한 것:

- `이 PDF 요약해줘` 같은 자연어 명령 라우팅
- 페이지 범위 추출·검색과 근거 페이지 표시
- PDF와 Excel·Word·한글·PowerPoint 워크플로 연결
- 실제 PDF 분할·병합·회전 실행과 read-back 검증
- OCR과 현재 PDF 뷰어 문맥 연결

다음 단계는 PDF-1 구조화된 읽기·검색 구현이다.
