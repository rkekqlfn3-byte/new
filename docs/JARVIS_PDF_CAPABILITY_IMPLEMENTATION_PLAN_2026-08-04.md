# JARVIS PDF 기능 추가 구현 계획서

- 작성일: 2026-08-04 KST
- 대상: JARVIS Python 소스 개발 브랜치
- EXE 빌드: 범위 제외
- 권장 판정: **추가한다. 단, PDF 편집기보다 구조적 자료 입력·변환 기능을 우선한다.**

## 1. 목적

PDF 기능의 목적은 Acrobat 메뉴를 대신 눌러 주는 것이 아니다. 사용자가 PDF의
페이지·문장·표를 직접 찾지 않아도 자연어로 내용을 이해하고, 필요한 정보를
Excel·Word·한글·PowerPoint 업무로 연결하도록 만드는 것이다.

대표 목표 문장:

```text
이 PDF 요약해줘.
계약 기간이 적힌 페이지를 찾아줘.
3페이지 표를 Excel로 정리해줘.
이 자료로 Word 보고서와 발표자료를 만들어줘.
2~5페이지만 새 PDF로 저장해줘.
이 PDF 두 개를 순서대로 합쳐줘.
거꾸로 된 4페이지만 돌려줘.
```

PDF는 현재 Prototype Goal 수용의 필수 조건은 아니다. 다만 실제 사용자 업무에서
Office 문서의 주요 입력 자료이므로 Goal 확장 기능으로는 우선순위가 높다.

## 2. 현재 구현 상태

이미 존재하는 기반:

- `engine/document_reader.py`
  - `pypdf` 기반 전체 텍스트 추출
  - 100MB, 2,000페이지, 500만 자 안전 제한
- `engine/ai_actions/basic_action_executor.py`
  - `read_and_analyze`를 통한 파일 읽기·AI 질문
- `engine/app_actions/hwp_adapter.py`
  - 한글 문서를 PDF로 저장
  - PDF 헤더·페이지·텍스트 재읽기 검증과 실패 시 정리
- `requirements-lock.txt`
  - `pypdf==6.14.2` 고정

현재 공백:

- PDF별 페이지 문맥과 검색 결과가 구조화되어 있지 않다.
- 읽기 결과가 최대 15,000자로 잘려 AI에 전달되며 페이지 출처가 없다.
- PDF는 연결 문서·현재 문서 문맥에 포함되지 않는다.
- 스캔 PDF를 구분하거나 OCR 환경 차단을 설명하지 못한다.
- PDF→Excel/Word/한글/PPT 업무 단계가 등록되어 있지 않다.
- 분할·병합·회전의 승인·검증·복구 계약이 없다.
- 표 추출 성공 여부와 신뢰도를 검증하지 않는다.

## 3. 범위 결정

### 3.1 1차 필수 범위

1. 로컬 PDF 선택·드롭·정확한 파일 신원 연결
2. 문서 정보와 페이지별 텍스트 읽기
3. 페이지 범위 요약·질문·키워드 검색
4. 답변에 근거 페이지 표시
5. 텍스트 기반 PDF와 스캔 PDF 구분
6. PDF 내용을 Word·한글·PowerPoint 초안으로 연결
7. 신뢰도 높은 표만 Excel로 추출하고 낮으면 사용자에게 확인 요청
8. 새 파일 방식의 분할·병합·회전
9. 승인 → 실행 → 재열기 검증 → 실패 정리

### 3.2 후속 범위

- 선택 페이지 삭제와 순서 변경
- 워터마크·페이지 번호
- 양식 필드 읽기·작성
- 주석 목록 읽기·내보내기
- 암호 입력을 통한 사용자 소유 암호화 PDF 처리
- 현재 Acrobat·Edge 뷰어의 페이지와 선택 영역 연결

### 3.3 이번에 제외

- 좌표 클릭 기반 Acrobat 자동화
- PDF 본문을 원본 위에서 Word처럼 직접 편집
- 전자서명·인증서·법적 서명
- DRM·권한 제한 우회
- 사용자 승인 없는 원본 덮어쓰기
- OCR 모델 자동 설치 또는 외부 OCR 서비스로 자동 전송

## 4. 설계 원칙

### 4.1 PDF는 앱이 아니라 문서 소스다

PDF는 Acrobat·Edge·Chrome 등 여러 뷰어로 열릴 수 있다. 따라서 `PdfAdapter`를
특정 앱 COM 어댑터처럼 만들지 않고 다음 두 경계로 나눈다.

```text
PdfDocumentService       읽기·검색·페이지 문맥·텍스트 블록
PreparedFileAction       분할·병합·회전처럼 파일을 새로 만드는 작업
```

읽기 요청은 `QUESTION`, 변환 요청은 `COMMAND` 경로를 사용한다. 새로운 UI 모드는
추가하지 않는다.

### 4.2 기존 Office 계약을 억지로 재사용하지 않는다

현재 `PreparedAction`은 `workbook_name`, `sheet` 등 Office 중심 필드를 가진다.
PDF에 빈 가짜 값을 넣지 않고 범용 파일 작업 계약을 신설한다.

```text
PreparedFileAction
- operation
- source_fingerprints
- output_path
- page_selection
- current_state
- expected_state
- destructive
- reversible
- verification_plan
- rollback_plan
```

### 4.3 원본 불변이 기본이다

- 모든 PDF 변환은 기본적으로 새 파일을 만든다.
- 출력 경로가 존재하면 자동 덮어쓰지 않는다.
- 명시적 덮어쓰기도 별도 승인과 백업이 필요하다.
- 임시 파일 생성 → PDF 재열기 검증 → 원자적 출력 이동 순서를 지킨다.
- 원본의 fingerprint가 준비 시점과 실행 시점에 같아야 한다.

### 4.4 페이지 출처를 잃지 않는다

추출 텍스트는 단일 문자열이 아니라 페이지별 구조로 유지한다.

```text
PdfPageText
- page_number
- text
- extraction_method
- character_count
- text_fingerprint
- warnings
```

요약·질문 결과에는 `근거 페이지: 3, 7~8`처럼 출처를 표시한다. 문서 원문과 실제
경로는 로그·학습 스킬·실패 보고서에 저장하지 않는다.

## 5. 목표 아키텍처

```text
자연어 입력
  ↓
PdfIntentParser
  ├─ 읽기·검색·요약 → PdfDocumentService
  ├─ 앱 산출물 연결 → PdfWorkflowService
  └─ 분할·병합·회전 → FileActionRouter
                              ↓
                       PreparedFileAction
                              ↓
                  승인 → 실행 → 재열기 검증 → 복구
```

권장 파일 구성:

```text
engine/pdf/
  contracts.py
  reader.py
  extraction.py
  search.py
  context.py
  intent_parser.py
  workflow_service.py
  transformation_service.py
  verification.py
  ocr.py

engine/file_actions/
  contracts.py
  router.py
  registry.py
  output_policy.py
```

각 함수는 99줄 이하 유지보수 계약을 처음부터 적용한다.

## 6. 단계별 구현

### PDF-0. 기준선과 계약 고정

작업:

- `feature/pdf-capabilities` 계열 작업 브랜치 사용
- 텍스트형·스캔형·암호화·손상·회전·표 포함 소유 fixture 생성기 추가
- `PdfDocumentSnapshot`, `PdfPageText`, `PdfExtractionResult` 계약 추가
- `PreparedFileAction`과 출력 경로 정책 추가
- 기존 `document_reader._read_pdf` 동작을 characterization test로 고정

완료 조건:

- 기존 1,051개 테스트 유지
- 새 계약은 JSON 직렬화 가능하며 파일 내용·실제 경로를 영구 상태에 남기지 않음
- 사용자 PDF를 테스트 fixture로 사용하지 않음

### PDF-1. 구조적 읽기·검색

작업:

- `document_reader.py`의 PDF 부분을 `engine/pdf/reader.py`로 분리
- 문서 페이지 수, 암호화 여부, 메타데이터의 안전한 일부만 반환
- 전체 문서·명시 페이지 범위·단일 페이지 추출 지원
- 한국어 키워드 검색과 앞뒤 문맥 반환
- 페이지별 텍스트 fingerprint와 문서 fingerprint 생성
- 취소 토큰·페이지별 content stream 크기·시간 제한 적용
- 빈 텍스트 페이지 비율로 `text`, `scanned`, `mixed` 분류

안전 보완:

- pypdf 공식 문서가 설명하듯 압축 해제된 content stream은 큰 메모리를 사용할 수
  있으므로 파일 크기뿐 아니라 페이지 stream 크기도 실행 전에 제한한다.
- 암호화 PDF는 암호를 추측하지 않고 `needs_input`으로 분류한다.
- 손상 PDF는 일반 문자열 오류가 아니라 구조화된 `validation_error`를 반환한다.

완료 조건:

- `이 PDF 3~5페이지만 요약해줘`가 지정 범위 밖을 읽지 않음
- 검색 결과가 페이지 번호·짧은 문맥을 포함함
- 0페이지, 초대형 stream, 손상, 암호화, 취소가 fail-closed

### PDF-2. 연결 문서와 자연어 라우팅

작업:

- PDF 파일 선택·드롭 전용 `PdfIntakeManager` 추가
- 기존 Office `FileIntakeManager`에는 `.pdf`를 억지로 추가하지 않음
- 연결 UI에 `PDF · 읽기 전용 · N페이지` 표시
- 다음 결정적 의도 파서 추가
  - 요약, 특정 페이지 설명, 검색, 페이지 수, 목차
  - 표 추출, 보고서 만들기
  - 분할, 병합, 회전
- `이거`, `여기`, `앞 페이지`, `방금 찾은 부분`을 연결 PDF 세션에 결속
- 명시적 파일 연결 전에는 현재 PDF를 추측하지 않음

완료 조건:

- PDF가 Edge에 보인다는 이유만으로 임의 파일을 선택하지 않음
- 같은 이름의 PDF가 여러 개면 정확한 파일을 한 번 질문함
- JARVIS 창 focus 때문에 현재 뷰어 문맥을 잃어도 연결 파일 fingerprint는 유지됨

### PDF-3. 요약·질문과 Office 연결

구현 상태: **2026-08-04 소스 구현 및 자동 회귀 검증 완료.** 실제 Office 환경의 최종 수동 확인과
source-bound Goal probe 갱신은 최종 커밋 뒤 수행한다.

작업:

- 페이지 단위 chunking과 근거 페이지 결합
- 한 번에 15,000자를 잘라 보내는 현재 방식을 제거
- 외부 AI 사용 시 전송 범위와 페이지 수를 미리 표시
- 읽은 내용을 다음 워크플로 단계로 연결
  - `analyze_pdf`
  - `create_excel_from_pdf_table`
  - `create_word_report_from_pdf`
  - `create_hwp_report_from_pdf`
  - `create_powerpoint_from_pdf`
- `workflow_step_registry.py`에 내용 없는 허용 단계와 후조건 등록
- 산출물은 기존 Office 어댑터로 생성하고 read-back 검증

표 추출 정책:

- PDF에는 표라는 의미 구조가 없는 경우가 많으므로 행·열을 확실히 복원한 경우만
  Excel 자동 생성을 허용한다.
- 열 수 불일치, 병합 셀 추정, 페이지 경계 표는 미리보기를 보여 주고 질문한다.
- 신뢰도가 낮으면 텍스트 목록만 제안하고 “표를 만들었다”고 주장하지 않는다.

완료 조건:

- 모든 답변·산출물에 근거 페이지가 기록됨
- 원본 PDF 불변
- Word·한글·PPT 산출물 재열기 검증
- Excel 표는 행·열 수, 머리글, 대표 셀을 원본 추출 결과와 대조

### PDF-4. 분할·병합·회전

작업:

- `extract_pages`, `merge_documents`, `rotate_pages` 허용 작업 추가
- 페이지 범위 정규화와 중복·역순 정책 명시
- 출력 이름 제안과 승인 카드 제공
- 원본 fingerprint 재확인
- 임시 출력 생성 후 다음을 검증
  - `%PDF-` 형식
  - 예상 페이지 수와 순서
  - 회전값
  - 가능한 페이지의 텍스트 fingerprint
  - 모든 원본 불변
- 실패 임시 파일 제거
- JARVIS가 만든 출력이 이후 변하지 않은 경우에만 Undo로 삭제

완료 조건:

- 승인 전 출력 파일 0개
- 원본 덮어쓰기 0건
- 잘못된 페이지 번호는 실행 전 차단
- 병합 입력 순서와 실제 출력 순서 일치
- 중간 실패 후 불완전 PDF 0개

### PDF-5. OCR capability

OCR은 별도 선택 기능으로 구현한다.

작업:

- `PdfOcrProvider` Protocol 추가
- `available`, `supported_languages`, `recognize_page`, `confidence` 계약 정의
- 스캔형 페이지에서만 OCR 제안
- 페이지 렌더러와 OCR 엔진의 성능·한국어 품질·배포 조건·라이선스 spike 수행
- 로컬 OCR 불가 시 `environment_blocked`와 설치·준비 안내
- 사용자가 승인하지 않으면 클라우드 OCR로 전송하지 않음

환경 결정:

- Windows AI OCR은 공식 문서 기준 NPU/Copilot+ 환경 의존성이 있으므로 필수
  런타임으로 가정하지 않는다.
- 기존 `Windows.Media.Ocr`도 데스크톱 package identity 제약이 있으므로 현재의
  일반 Python 소스 실행에서 자동으로 사용 가능하다고 가정하지 않는다.
- 따라서 OCR 공급자는 capability probe 뒤 선택하며 PDF-1~4를 막지 않는다.

완료 조건:

- OCR 미지원 PC에서도 텍스트 PDF 기능 전체 정상
- image-only PDF가 빈 문서로 오판되지 않고 OCR 필요 상태를 설명
- OCR 결과에는 페이지·언어·신뢰도와 `ocr` 출처 표시
- 낮은 신뢰도의 표·숫자를 자동으로 업무 산출물에 확정하지 않음

### PDF-6. 현재 뷰어 문맥 연결

1차 필수 기능이 안정된 뒤 진행한다.

작업:

- Acrobat Reader·Edge PDF viewer별 capability 탐색
- UI Automation에서 확인 가능한 경우에만 파일 신원·현재 페이지·선택 텍스트 읽기
- 로컬 경로 또는 다운로드 신원을 증명하지 못하면 연결하지 않음
- 현재 페이지 읽기는 read-only이며 뷰어 좌표 클릭을 사용하지 않음

완료 조건:

- JARVIS 때문에 뷰어가 뒤로 가도 명시적 “현재 PDF 연결” 요청 시 다시 앞으로 표시
- 다른 PDF로 전환되면 fingerprint 불일치로 중단
- 뷰어별 지원 차이는 typed capability로 안내

### PDF-7. 검증·Goal 편입

자동 테스트:

- 계약·parser·페이지 범위·검색 단위 테스트
- 텍스트·혼합·빈 페이지·회전·암호화·손상·초대형 fixture
- 메모리·timeout·취소·경로 교체 실패 주입
- 분할·병합·회전 prepare/approve/execute/read-back/rollback
- PDF→Excel/Word/한글/PPT owned-fixture workflow
- 로그·incident·학습 저장소에 경로·원문 부재 검사
- Ruff·maintenance 함수 99줄 게이트

수동 테스트:

- PDF 전용으로 의미가 다른 문장 120개 작성
- 동일 표현의 파일명·숫자 교체로 사례 수를 채우지 않음
- 텍스트형·스캔형·표·혼합 언어·긴 문서·오류 복구 포함
- 화면 읽기 프로그램에서 페이지 근거·승인 카드·오류 안내 확인

Goal 연결:

- 자연어: PDF 120문장 정확성
- 문맥: 연결 파일·페이지·직전 검색
- 안전 실행: 원본 불변·승인·재읽기·복구
- 업무 연결: PDF→Office 산출물
- 실패 대응: 암호화·스캔·OCR 미지원·손상·환경 차단 분류
- 접근성: 페이지와 오류를 기술 용어 없이 안내

완료 조건:

- 기존 전체 회귀 100% 유지
- PDF 신규 자동·실앱 probe 모두 현재 source identity에 결속
- 사용자 문서 변경 0
- Goal 보고서에 `pdf_document_workflow` 축 증거 추가

## 7. 권장 구현 순서와 예상 규모

| 순서 | 단계 | 예상 |
|---|---|---:|
| 1 | PDF-0 계약·fixture | 0.5~1일 |
| 2 | PDF-1 구조적 읽기·검색 | 1~2일 |
| 3 | PDF-2 연결·자연어 라우팅 | 1~2일 |
| 4 | PDF-3 Office 연결 | 2~3일 |
| 5 | PDF-4 분할·병합·회전 | 1~2일 |
| 6 | PDF-7 전체 검증 | 1~2일 |
| 선택 | PDF-5 OCR | 2~4일 + 환경 준비 |
| 선택 | PDF-6 뷰어 문맥 | 1~3일 |

권장 MVP는 PDF-0~4와 PDF-7이다. OCR·현재 뷰어 연결은 기본 PDF 기능의 완료를
막지 않는 후속 단계로 둔다.

## 8. 수용 기준

PDF MVP는 다음을 모두 만족할 때 완료다.

1. 사용자가 명시적으로 연결한 로컬 PDF만 읽는다.
2. 페이지 범위 질문이 범위 밖 내용을 사용하지 않는다.
3. 답변·검색·산출물에 근거 페이지가 있다.
4. 스캔 PDF를 텍스트 없는 정상 PDF와 구분해 설명한다.
5. PDF→Office 산출물을 실제 앱에서 다시 읽어 검증한다.
6. 분할·병합·회전은 승인 전 아무 파일도 만들지 않는다.
7. 원본 PDF를 수정하지 않는다.
8. 중간 실패 시 불완전 출력과 임시 파일이 남지 않는다.
9. 암호화·손상·초대형·OCR 미지원이 올바른 책임 분류로 이어진다.
10. 경로·본문·검색어가 로그·스킬·incident에 저장되지 않는다.
11. 전체 기존 테스트와 신규 PDF 테스트가 모두 통과한다.
12. Python 소스 기준으로만 개발하며 EXE를 생성하지 않는다.

## 9. 주요 위험과 대응

| 위험 | 대응 |
|---|---|
| PDF 텍스트 순서가 화면과 다름 | 페이지·layout extraction 비교, 불확실 표시 |
| 표 구조 오인식 | 신뢰도·미리보기·대표 셀 검증, 낮으면 자동 생성 금지 |
| 스캔 PDF 빈 결과 | text/scanned/mixed 분류 후 OCR 제안 |
| 압축 stream 메모리 폭증 | 페이지 stream 사전 제한·timeout·취소 |
| 암호·DRM 우회 위험 | `needs_input` 또는 `blocked`, 우회 금지 |
| 원본 손상 | 새 파일 기본, fingerprint 재확인, 원자적 출력 |
| 외부 AI 개인정보 전송 | 전송 페이지 표시·승인·원문 비저장 |
| OCR 환경 편차 | Provider + capability probe, 기본 기능과 분리 |
| Acrobat·Edge UI 차이 | 뷰어 연결은 후속, 정확 신원 없으면 차단 |

## 10. 참고한 공식 자료

- pypdf 텍스트 추출과 한계:
  https://pypdf.readthedocs.io/en/stable/user/extract-text.html
- pypdf 병합·페이지 선택:
  https://pypdf.readthedocs.io/en/stable/user/merging-pdfs.html
- pypdf 회전·변환:
  https://pypdf.readthedocs.io/en/stable/user/cropping-and-transforming.html
- Windows AI OCR 환경:
  https://learn.microsoft.com/en-us/windows/ai/apis/text-recognition

## 11. 최종 권고

PDF 기능은 추가한다. 다만 처음부터 Acrobat 전체 기능을 따라가지 않는다.

```text
1차: 읽기·검색·페이지 근거
2차: PDF→Office 업무 연결
3차: 새 파일 분할·병합·회전
4차: 선택 OCR·현재 뷰어 문맥
```

이 순서가 JARVIS의 핵심 Goal인 자연어, 현재 문맥, 네이티브 안전, 여러 앱 연결,
실패 분류를 가장 적은 위험으로 확장한다.
