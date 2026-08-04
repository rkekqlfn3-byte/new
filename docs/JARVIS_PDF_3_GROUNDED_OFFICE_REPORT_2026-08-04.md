# JARVIS PDF-3 페이지 근거화·Office 연결 완료 보고서

- 기준일: 2026-08-04 KST
- 작업 브랜치: `codex/pdf-capabilities`
- 범위: Python 소스, Eel UI, 자동 테스트
- EXE 빌드: 수행하지 않음

## 1. 판정

PDF-3의 소스 구현과 자동 회귀 검증을 완료했다.

연결한 PDF의 지정 페이지를 페이지 경계를 보존한 채 읽고, 검색·목차·고신뢰 표 추출은 로컬에서
처리한다. 요약·설명·보고서는 외부 AI 전송 전에 공급자, 페이지, 글자 수와 생성 파일 이름을
공개하고 사용자가 승인한 경우에만 실행한다. 승인 대기 중 연결이나 파일 fingerprint, 전송 계획,
표 구조, 출력 경로가 달라지면 실행하지 않는다.

## 2. 구현 내용

### 페이지 근거화

- 페이지별 최대 6,000자 청크와 SHA-256 텍스트 fingerprint
- 한 번에 15,000자를 자르는 기존 방식 제거
- 큰 문서는 최대 12,000자 단위 map/reduce 처리
- 모든 답변과 결과물에 `p.N` 페이지 인용 부착
- 텍스트 계층이 없는 선택 페이지는 `ocr_required`로 중단

### 로컬 작업

- PDF 문자열 검색과 페이지별 발췌
- 제목 후보 기반 목차
- 동일 페이지, 동일 열 수, 수치 열이 있는 고신뢰 표만 추출
- 열 수 불일치, 여러 표, 낮은 신뢰도는 자동 생성하지 않고 추가 설명 요청

### 외부 AI 승인

- 전송 공급자, 페이지 번호·개수, 글자 수, 배치 수를 내용 없이 계산
- API 키가 없으면 승인 카드 생성 전 중단
- 승인 전 AI 호출 0회
- 승인 뒤 동일 PDF·동일 페이지·동일 전송 계획을 재확인
- 공급자 응답의 허용 페이지 인용만 결과에 반영

### Office 결과물

- `analyze_pdf`
- `create_excel_from_pdf_table`
- `create_word_report_from_pdf`
- `create_hwp_report_from_pdf`
- `create_powerpoint_from_pdf`

위 단계는 `workflow_step_registry.py`의 내용 없는 allowlist recipe로 제한한다. Excel은 새 전용
인스턴스에서 표를 쓴 뒤 행·열·머리글·대표 셀을 다시 읽는다. Word와 PowerPoint는 새 파일을
저장한 뒤 OOXML 파일을 다시 열어 페이지 인용을 확인한다. 한글은 전용 격리 프로세스에서 저장한
파일을 다시 열어 제목과 페이지 인용을 확인한다. 중간 단계가 실패하면 이번 작업에서 이미 만든
결과물을 제거한다. 원본 PDF는 변경하지 않는다.

## 3. 개인정보와 저장 정책

- PDF 본문, 검색어, AI 답변은 진단 evidence에 저장하지 않는다.
- 승인 공개 데이터에는 문서 본문과 실제 출력 경로가 없다.
- PDF에서 파생된 채팅 답변은 현재 실행 중에는 표시하지만 대화 JSON에는 중립 문구로 대체한다.
- 세션 전용 답변은 이후 대화 요약이나 AI 문맥으로 자동 재전송하지 않는다.
- 결과 evidence는 페이지, 문자 수, fingerprint, 출력 형식, read-back 결과만 가진다.

## 4. 자동 검증 결과

| 검증 | 결과 |
|---|---:|
| PDF-3 신규·회귀 단위 테스트 | 통과 |
| 전체 unit | 546개 통과 |
| 전체 integration | 418개 통과 |
| 전체 unittest 발견 | 1,184개 통과, 조건부 3개 제외 |
| 실제 Excel·Word·PowerPoint owned-fixture probe | 통과 |
| Python compileall | 통과 |
| Ruff 전체 lint | 통과 |
| JavaScript `node --check` | 통과 |
| `git diff --check` | 통과 |
| 유지보수 strict 감사(생성물 분리) | 통과, 오류 0, 경고 0 |
| 현재 작업 폴더 감사 | 오류 0, 기존 ignored `outputs/` 정리 경고 1 |

유지보수 감사의 `outputs/` 경고는 Git에 포함되지 않은 기존 수동 테스트·감사 산출물이 작업 폴더에
남아 있다는 뜻이다. 소스 또는 배포물 오류가 아니며 사용자 산출물을 임의 삭제하지 않았다. 깨끗한
CI checkout에는 해당 디렉터리가 없어 strict gate가 동일 이유로 실패하지 않는다.

실제 Office probe에서는 Excel 2행×2열 표의 머리글과 대표 셀, Word 13개 문단의 재열기 인용,
PowerPoint 5장 슬라이드의 재열기 인용을 확인했다. 이 검증 과정에서 발견된 Excel 숫자 형식
정규화와 잠금 해제 순서 문제도 수정한 뒤 같은 probe를 재실행해 통과시켰다.

한글 owned-fixture probe는 사용자 문서를 건드리지 않는 격리 인스턴스에서 40초 안에 응답하지
않았다. 사용자 프로세스 보호와 테스트 프로세스 정리는 확인됐지만 실제 재열기 성공으로 간주하지
않고 `hwp_automation_responsiveness` 환경 차단으로 기록한다. Goal gate는 이 경우에도 다른 probe를
끝까지 수집하며, 한글을 통과로 표시하지 않는다.

## 5. 완료 조건 대조

| 계획 조건 | 판정 | 근거 |
|---|---:|---|
| 모든 답변·결과물에 페이지 기록 | 충족 | 허용 페이지 인용과 결과물 재열기 검사 |
| 원본 PDF 불변 | 충족 | read-only intake, 실행 전 fingerprint 재검증 |
| Word·PPT 재열기/read-back | 충족 | 실제 owned fixture를 OOXML로 다시 열어 인용 검사 |
| 한글 재열기/read-back | 환경 차단 | 구현·자동 테스트 완료, 실제 격리 Automation은 40초 응답 시간 초과 |
| Excel 표 구조·대표 셀 대조 | 충족 | 행·열·머리글·첫/마지막 대표 셀 검사 |
| 낮은 신뢰도 표의 자동 생성 금지 | 충족 | 0개·복수 후보 모두 clarification |
| 승인 전 외부 전송 금지 | 충족 | in-memory one-shot confirmation handler |
| 문서 파생 채팅의 영구 저장 금지 | 충족 | `session_only` UI persistence policy |

## 6. 남은 범위

- PDF-4: 분할·병합·회전과 임시 출력 rollback
- PDF-5 선택 기능: OCR provider
- PDF-6 선택 기능: Edge·Acrobat 현재 페이지·선택 영역 연결
- PDF-7: PDF 전용 대규모 문장·성능·실패 주입·수동 사용성 검증
- 화면 읽기, 음성 입력, 비숙련자 관찰은 제품 Goal의 수동 evidence로 계속 남아 있다.

최종 커밋 뒤 source identity가 고정되면 Goal probe를 다시 생성해야 한다. 커밋 전 probe는 소스
tree hash가 바뀌므로 의도적으로 최종 증거로 인정되지 않는다.
