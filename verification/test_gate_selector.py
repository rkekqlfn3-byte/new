"""Recommend deterministic validation gates from a set of changed paths."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class TestGate:
    gate_id: str
    command: str
    reason: str
    manual: bool = False


BASE_GATES = (
    TestGate(
        "maintenance_audit",
        "python -m verification.maintenance_audit",
        "구조·비밀정보·대용량 파일 유지보수 계약을 확인합니다.",
    ),
    TestGate("diff_check", "git diff --check", "공백과 줄바꿈 오류를 확인합니다."),
)


def _normalized(path):
    return PurePosixPath(str(path).replace("\\", "/").lstrip("./")).as_posix()


def changed_paths(project_root: Path):
    """Return tracked changes and visible untracked files without reading content."""
    commands = (
        ["git", "diff", "--name-only", "--relative", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    paths = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            continue
        paths.update(_normalized(line) for line in result.stdout.splitlines() if line)
    return tuple(sorted(paths))


def select_test_gates(paths):
    """Return an ordered, de-duplicated gate list for changed repository paths."""
    normalized = tuple(sorted({_normalized(path) for path in paths if str(path).strip()}))
    gates = {gate.gate_id: gate for gate in BASE_GATES}

    def add(gate_id, command, reason, manual=False):
        gates.setdefault(gate_id, TestGate(gate_id, command, reason, manual))

    runtime = any(path.startswith(("engine/", "jarvis_")) for path in normalized)
    tests_or_verification = any(
        path.startswith(("tests/", "verification/")) for path in normalized
    )
    docs_only = bool(normalized) and all(
        path.startswith("docs/") or path in {"README.md", ".gitignore", ".gitattributes"}
        for path in normalized
    )

    if runtime:
        add("compile", "python -m compileall -q engine verification tests", "Python import·문법을 검사합니다.")
        add("full_tests", "python -m unittest -q", "런타임 변경의 전체 회귀를 확인합니다.")
        add("startup", "python -m engine.startup_check", "실행 환경과 필수 import를 확인합니다.")
        add("performance", "python -m verification.maintenance_performance", "시작·파서·로컬 실행·저장 성능과 소스 크기 회귀를 확인합니다.")
    elif tests_or_verification:
        add("compile", "python -m compileall -q verification tests", "검증 코드의 문법을 검사합니다.")
        add("full_tests", "python -m unittest -q", "검증 도구 변경이 테스트 목록을 깨지 않았는지 확인합니다.")
    elif docs_only:
        return tuple(gates.values())

    if any(path.startswith(("engine/parser.py", "engine/parsing/", "engine/pipeline/", "engine/local_commands/", "engine/builtins.py")) for path in normalized):
        add("parser_contract", "python -m unittest -q tests.unit.test_parser_composition tests.integration.test_parser_accuracy tests.integration.test_command_analysis", "파서 구성과 라우팅 계약을 확인합니다.")
        add("utterance_battery", "python -m verification.utterance_acceptance_battery", "1,000문장 자연어 회귀를 확인합니다.")

    if any(path.startswith(("engine/confirmation/", "engine/security/", "engine/execution_result.py", "engine/execution_runtime.py")) for path in normalized):
        add("safety_contract", "python -m unittest -q tests.integration.test_confirmation_flow tests.integration.test_dynamic_code_preflight tests.integration.test_execution_result tests.integration.test_run_policy", "확인·보안·실행 결과 정책을 확인합니다.")
        add("self_diagnosis", "python -m verification.prototype11_stage12_probe", "안전한 실패 수집과 진단을 확인합니다.")
        add("failure_injection", "python -m verification.maintenance_failure_injection", "확인 만료·대상 변경·저장 실패·COM busy 안전성을 주입 검증합니다.")

    if any(path.startswith(("engine/storage/", "engine/app_actions/com_lifecycle.py")) for path in normalized):
        add("failure_injection", "python -m verification.maintenance_failure_injection", "저장 실패와 COM 수명주기 장애의 fail-closed 동작을 확인합니다.")

    if any(path.startswith("engine/edit_mode/") for path in normalized):
        add("edit_contract", "python -m tests.test_runner unit", "편집 세션·문맥·선택 계약을 확인합니다.")
        add("edit_integration", "python -m unittest -q tests.integration.test_edit_mode_routing tests.integration.test_edit_session_api tests.integration.test_stage7_edit_flow", "편집 흐름 통합 회귀를 확인합니다.")
        add("edit_windows", "python -m unittest -q tests.windows.test_ui_automation tests.windows.test_windows_integration", "Windows 문서 연결 경계를 확인합니다.")
        add("edit_manual", "manual: 실제 Office에서 연결·포커스·선택·미저장 문서를 확인", "실제 앱 상태는 수동 확인이 필요합니다.", True)

    if any(path.startswith("engine/app_actions/") for path in normalized):
        add("native_windows", "python -m tests.test_runner windows", "네이티브 앱 adapter와 COM 경계를 확인합니다.")
        add("failure_injection", "python -m verification.maintenance_failure_injection", "대상 변경·COM busy·검증 실패를 주입합니다.")
        add("native_manual", "manual: 변경된 Office 앱에서 준비·실행·재조회·검증 확인", "실제 설치 앱의 동작을 확인해야 합니다.", True)

    if any(path.startswith(("engine/skills/", "engine/learning/")) for path in normalized):
        add("skill_contract", "python -m unittest -q tests.integration.test_skill_services tests.integration.test_skill_executor tests.integration.test_learning_loop tests.integration.test_learning_review", "학습·재사용·스킬 실행 계약을 확인합니다.")
        add("learning_probe", "python -m verification.prototype11_stage11_probe", "사용자 학습 수용성을 확인합니다.")

    if any(path.startswith(("web/", "engine/api/")) for path in normalized):
        add("ui_contract", "python -m unittest -q tests.unit.test_gui_markdown_safety tests.unit.test_ui_persistence_refresh", "UI 메시지와 세션 갱신 계약을 확인합니다.")

    if any(path in {"requirements.txt", "Jarvis.spec", "build_dist.bat", ".gitignore", ".gitattributes"} or path.startswith("default_data/") for path in normalized):
        add("release_security", "python -m unittest -q tests.windows.test_release_security", "의존성·기본 데이터·배포 입력의 보안을 확인합니다.")

    return tuple(gates.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="변경된 저장소 상대 경로")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    paths = tuple(args.paths) or changed_paths(root)
    gates = select_test_gates(paths)
    payload = {
        "changed_paths": list(paths),
        "gates": [asdict(gate) for gate in gates],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"changed_paths={len(paths)}")
        for gate in gates:
            kind = "MANUAL" if gate.manual else "AUTO"
            print(f"[{kind}] {gate.gate_id}: {gate.command}")
            print(f"       {gate.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
