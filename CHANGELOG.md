# Changelog

## 1.1.0-rc.1 — 보정 릴리스 후보

### 보안과 결과 정확성

- `PyPDF2`를 `pypdf 6.14.2`로 교체하고 PDF 크기·페이지·추출 문자 상한 추가
- 시스템 종료 명령을 `subprocess.run(..., shell=False)`로 전환하고 실제
  반환 코드에 따라 성공·실패 보고
- runtime 소스의 bare `except`, 비의도적 `print()` 경로 정리
- 사전·학습 GUI의 동적 HTML 삽입을 안전한 DOM 조립과 숫자 검증으로 전환

### 구조와 재현성

- 실행 폴더와 개발 저장소를 분리하고 테스트·검증·빌드 도구 복원
- 명령 파이프라인을 대화·네이티브·학습·매크로·AI fallback route로 분리
- AI batch 실행을 dispatcher, action executor, 결과 aggregator로 분리
- confirmation 생성과 학습 행동 재실행을 parser 밖의 서비스로 분리
- Excel·HWP의 COM-free helper 및 Word·PowerPoint 확장 계약 추가

현재 소스의 전체 회귀, 실제 Excel·HWP, 후보 EXE 검증은 R6에서 마지막으로
실행하며 통과 전에는 기존 실행본을 교체하지 않습니다.

## 1.1.0 — 2026-07-15 이전 실행본

- HTML 정화와 로컬 마크다운 렌더러
- 동시 명령 busy 보호와 실행 진단
- 학습 네이티브 실행 경로 보강
- Excel·한글 네이티브 편집, 확인·검증·복구 기반

현재 `JARVIS_RUNTIME\current`의 보존 실행본은 커밋 `a096e93`에서 만든
이전 빌드이며 위 보정 변경을 포함하지 않습니다.

## 1.0 — 2026-07-14

- Windows 앱·파일·웹·미디어 기본 명령
- AI 질문·대화와 동적 행동 안전 검사
- 사용자 사전·매크로·세션 저장 기반
