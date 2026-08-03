# JARVIS PDF-1 구조화 판독·검색 완료 보고서

- 완료일: 2026-08-04 KST
- 작업 브랜치: `codex/pdf-capabilities`
- 범위: Python 소스 및 자동 테스트
- EXE 빌드: 수행하지 않음

## 1. 결과

기존의 `PDF 전체 → 단일 문자열` 판독 경로를 페이지 단위 구조화 서비스로 분리했다. 기존 `engine.document_reader.extract_text()` 호출자는 계속 문자열을 받을 수 있어 기존 기능과의 호환성을 유지한다.

새 코드에서는 다음 정보를 사용할 수 있다.

- 문서 fingerprint, 페이지 수, 파일 크기, 암호화 상태, 문서 유형
- 요청한 페이지 번호와 각 페이지의 텍스트
- 페이지별 문자 수, 텍스트 fingerprint, 추출 방식, 경고 식별자
- 검색 결과의 페이지 번호, 시작·종료 offset, 주변 문맥
- 본문·검색어·실제 경로가 제거된 persistent evidence 표현

## 2. 페이지 선택

다음 입력을 하나의 정렬·중복 제거된 1-base 페이지 목록으로 정규화한다.

```text
전체: None, all, *
단일: 3
목록: 1, 3, 5
범위: 2-5, 2~5
혼합: 1, 3-5, 8
```

0 이하, 문서 범위 초과, 내림차순 범위, 빈 목록, 실수 페이지 번호는 실행 전에 차단한다.

## 3. 문서 유형과 스캔 판정

전체 페이지를 읽었을 때 다음과 같이 분류한다.

- `text`: 모든 페이지에 텍스트 계층이 있음
- `scanned`: 모든 페이지의 텍스트 계층이 비어 있음
- `mixed`: 텍스트 페이지와 이미지 전용 페이지가 섞여 있음
- `unknown`: 일부 페이지만 읽어 문서 전체를 확정할 수 없음

이미지 전용 PDF는 더 이상 설명 없는 빈 결과로만 취급하지 않는다. 페이지에는 `no_text_layer`, 문서에는 `ocr_required` 경고가 붙는다. OCR 실행은 PDF-5 범위이므로 아직 수행하지 않는다.

## 4. typed failure

PDF 판독 실패는 다음과 같은 content-free 코드로 구분된다.

- dependency unavailable
- file not found / unsafe path / I/O error
- file size / page count / page text 제한 초과
- 빈 문서
- 잘못된 페이지 선택
- 암호 필요
- 손상된 문서
- content stream 확장 크기 초과
- 지원하지 않는 content filter
- 작업 중 원본 변경
- 사용자 취소 / 시간초과
- 페이지 추출 실패
- 검색어 오류

암호화 PDF는 `needs_input`, 의존성 부재는 `environment_blocked`, 사용자 취소는 `cancelled`, 나머지 안전·형식 오류는 `validation_error`로 분류한다. evidence에는 예외 원문이나 파일 경로를 기록하지 않는다.

## 5. 안전 제한

- 로컬 절대 `.pdf` 파일만 판독
- 심볼릭 링크 입력 차단
- 기본 파일 크기 100MB 제한
- 기본 페이지 수 2,000페이지 제한
- 기본 전체 추출 텍스트 500만 자 제한
- 페이지 텍스트 100만 자 제한
- 페이지 content stream 해제 크기 20MB 제한
- 파일 SHA-256을 chunk 단위로 계산
- hash 전후와 판독 후 파일 크기·수정 시각을 재확인
- 페이지 사이 취소·시간 제한 확인
- uncompressed 및 단일 `FlateDecode` stream을 사전 검사
- 알 수 없는 filter chain은 추측하지 않고 차단

`FlateDecode`는 제한 크기까지만 압축을 풀어 실제 확장 크기를 확인하므로, 압축 파일 자체가 작아도 해제 후 제한을 넘는 content stream은 `extract_text()` 전에 차단한다.

## 6. 검색

검색은 정규식이 아닌 literal 검색이며 기본적으로 대소문자를 구분하지 않는다.

결과에는 다음이 포함된다.

- 근거 페이지 번호
- 페이지 안의 시작·종료 offset
- 최대 500자의 주변 문맥
- 결과 제한 도달 여부

persistent evidence에는 검색어와 문맥 원문 대신 문자 수와 SHA-256 fingerprint만 저장한다.

## 7. 소유 fixture

PDF-0 fixture에 다음을 추가했다.

- 텍스트와 이미지 페이지가 섞인 mixed PDF
- 정상 `FlateDecode` PDF
- 압축 해제 시 크게 확장되는 PDF
- 지원하지 않는 filter PDF
- 0페이지 PDF

모든 fixture는 테스트 임시 폴더에서 생성·제거되며 사용자 문서를 읽지 않는다.

## 8. 검증 결과

| 검사 | 결과 |
|---|---:|
| PDF-1 관련 표적 테스트 | 51개 통과 |
| 전체 자동 테스트 | 1,107개 통과, 조건부 3개 건너뜀 |
| Python compileall | 통과 |
| Ruff 전체 lint | 통과 |
| 신규 PDF 파일 Ruff format | 통과 |
| 유지보수 함수 99줄 예산 | 통과 |
| `pip check` | 통과 |
| `git diff --check` | 통과 |
| 유지보수 strict 감사 | 오류 0, 기존 `outputs/` 경고 1 |

PDF-0 완료 시점의 1,080개 테스트를 모두 유지하고 PDF-1 테스트 27개가 추가되었다.

## 9. 아직 남은 제한

- 자연어 PDF 명령과 연결 UI는 아직 연결하지 않음
- 일부 페이지만 읽으면 문서 전체 유형은 `unknown`
- 암호 입력과 복호화는 아직 지원하지 않음
- OCR은 아직 지원하지 않음
- 복잡한 다중 content filter chain은 typed failure로 차단
- 표·열·레이아웃의 의미 구조는 아직 보장하지 않음
- 시간 제한은 페이지 사이에 확인하며, 하나의 pypdf 호출을 강제 종료하는 프로세스 격리는 아직 없음
- 검색 결과는 아직 사용자 응답의 근거 페이지 형식으로 렌더링되지 않음

다음 단계 PDF-2에서는 PDF 선택·연결 상태와 자연어 대상 해석을 연결한다.
