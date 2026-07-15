# Changelog

## 1.1.0-rc.3 — 단일 실행형 재부팅 보정

- PyInstaller one-file 실행본이 재부팅할 때 이전 `_MEI` 임시 경로를 재사용하지 않도록
  새 압축 해제 환경을 요청한다.
- 친구 전달용 단일 EXE에는 빈 기본 기억·빈 설정만 포함하고 대화 세션을 포함하지 않는다.

## 1.1.0-rc.2 — 보정 릴리스 후보

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
- 동결 EXE 검증 probe를 현재 confirmation 정책과 실행 결과 구조에 맞게 갱신

### 최종 검증

- 자동 테스트 435개: 432개 통과, 외부 AI 3개 skip, 실패 0개
- `pip-audit` 알려진 취약점 0건
- 실제 Excel 20/20, 한글 8/8 통과
- 후보 EXE 기동, frozen worker, timeout·취소·재시도·잠금, TLS, Excel COM 통과
- 기존 사용자 데이터 33개 파일 사전·사후 바이트 동일
- 태그 `v1.1.0-rc.2`, 빌드 commit `ceb6c17d14f495d167a3c12417464c124bde0d90`

## 1.1.0 — 2026-07-15 이전 실행본

- HTML 정화와 로컬 마크다운 렌더러
- 동시 명령 busy 보호와 실행 진단
- 학습 네이티브 실행 경로 보강
- Excel·한글 네이티브 편집, 확인·검증·복구 기반

이 빌드는 R6 교체 전 current였으며, 현재는 `JARVIS_RUNTIME\previous`에
직전 정상 백업으로 보존돼 있습니다.

## 1.0 — 2026-07-14

- Windows 앱·파일·웹·미디어 기본 명령
- AI 질문·대화와 동적 행동 안전 검사
- 사용자 사전·매크로·세션 저장 기반
