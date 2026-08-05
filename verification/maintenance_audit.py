"""Read-only architecture, secret, artifact, and workspace-size audit."""

from __future__ import annotations

import argparse
import ast
import functools
import inspect
import json
import os
import re
import subprocess
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from verification.maintainability_budgets import (
    LONG_FUNCTION_EXCEPTIONS,
    MAX_FUNCTION_LINES,
)
from verification.release_security_audit import GENERIC_SECRET_PATTERNS

DEFAULT_MAX_PARSER_LINES = 1000
DEFAULT_LARGE_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_MODULE_LINE_BUDGETS = {
    "engine/workflows/business_workflow.py": 2477,
    "engine/edit_mode/controller.py": 1628,
    "engine/edit_mode/native_bridge.py": 1123,
    "engine/action_executor.py": 1101,
    "engine/edit_mode/stage11.py": 1026,
    "engine/app_actions/excel_vba_adapter.py": 1007,
    "engine/edit_mode/stage5.py": 1303,
    "engine/app_actions/excel_adapter.py": 942,
    "engine/edit_mode/stage10.py": 778,
    "engine/app_actions/powerpoint_adapter.py": 530,
    "engine/app_actions/word_adapter.py": 329,
    "engine/app_actions/hwp_adapter.py": 348,
}

# Adapters own the COM lifecycle and the shared context; every user-visible
# action belongs in engine/app_actions/operations/<app>/.  Budgets alone would
# not stop an operation from being added back to an adapter, because a new
# branch can always be paid for by shrinking something else.
OPERATION_OWNING_ADAPTERS = (
    "engine/app_actions/excel_adapter.py",
    "engine/app_actions/hwp_adapter.py",
    "engine/app_actions/powerpoint_adapter.py",
    "engine/app_actions/word_adapter.py",
)
ADAPTER_OPERATION_METHOD_PREFIXES = ("_prepare_", "_execute_")

# Korean wording users actually speak belongs in engine/vocabulary/. These
# tables had drifted across seven and sixteen sites respectively, so the same
# sentence got different answers depending on the route it took. A literal
# alias set copied back out is the start of that happening again.
VOCABULARY_HOME = "engine/vocabulary"

# Detect the *mapping*, not the words. A module that merely mentions 왼쪽 may be
# talking about a join direction or a slot template; a module that maps 왼쪽 to
# ``left`` is defining alignment vocabulary.
ALIGNMENT_PAIR_RE = re.compile(
    r'"(?:왼쪽|좌측|가운데|중앙|오른쪽|우측|양쪽|배분)(?:\s*정렬)?"\s*:\s*'
    r'"(?:left|center|right|justify)"'
    r'|"(?:left|center|right|justify)"\s*:\s*'
    r'"(?:왼쪽|좌측|가운데|중앙|오른쪽|우측|양쪽|배분)(?:\s*정렬)?"'
)
# ``center`` alone is not enough: window snapping also maps 가운데 to center
# while mapping 왼쪽 to left_half, which is a different concept entirely.
ALIGNMENT_PAIR_MIN = 2
ALIGNMENT_DISTINCT_MIN = 2

# Cancel wording is a flat list, so proximity is the signal: three ways to say
# no within a few lines is an option's alias list.
CANCEL_MARKERS = ("아니요", "그만", "하지마")
CANCEL_MARKER_WINDOW = 4
FORBIDDEN_BACK_REFERENCES = frozenset({"owner", "parser", "_parser"})
EXCLUDED_PARTS = frozenset({".git", ".venv", "__pycache__", "node_modules"})
GENERATED_WORKSPACE_DIRS = frozenset({
    "backups", "build", "dist", "logs", "outputs", "releases", "temp", "tmp",
})
TEXT_SUFFIXES = frozenset({
    ".bat", ".cmd", ".css", ".html", ".ini", ".js", ".json", ".md",
    ".ps1", ".py", ".toml", ".txt", ".yaml", ".yml",
})
FORBIDDEN_TRACKED_PARTS = frozenset({
    "backups", "build", "data", "dist", "logs", "outputs", "runtime_data",
    "temp", "tmp", "user_data",
})
FORBIDDEN_TRACKED_SUFFIXES = frozenset({".bak", ".exe", ".log", ".pyc", ".zip"})
LITERAL_SECRET_PATTERN = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key)"
    rb"\s*[:=]\s*['\"]([^'\"\r\n]{8,})['\"]"
)
COMPOSITION_SOURCES = (
    "engine/confirmation",
    "engine/pipeline",
    "engine/ai_actions",
    "engine/app_actions/app_command_router.py",
    "engine/edit_mode/controller.py",
    "engine/local_commands/local_command_analyzer.py",
    "engine/skills/candidate_recording_service.py",
    "engine/skills/learned_replay_service.py",
    "engine/skills/skill_executor.py",
    "engine/skills/skill_learning_service.py",
)


@dataclass(frozen=True)
class AuditFinding:
    severity: str
    code: str
    path: str
    detail: str


_REFERENCE_SCAN_LEAVES = (
    str, bytes, bytearray, int, float, complex, bool, type(None), Path,
)


def retained_parent_reference_paths(child, parent, *, max_depth=6):
    """Find retained parent identities without relying on attribute names."""
    queue = deque([(child, type(child).__name__, 0)])
    seen = set()
    findings = []
    while queue:
        value, path, depth = queue.popleft()
        if value is parent:
            findings.append(path)
            continue
        if isinstance(value, _REFERENCE_SCAN_LEAVES) or inspect.ismodule(value):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        bound_self = getattr(value, "__self__", None)
        if (inspect.ismethod(value) or inspect.isbuiltin(value)) and bound_self is parent:
            findings.append(f"{path}.__self__")
            continue
        if depth >= max_depth:
            continue
        if isinstance(value, functools.partial):
            queue.append((value.func, f"{path}.func", depth + 1))
            queue.extend(
                (item, f"{path}.args[{index}]", depth + 1)
                for index, item in enumerate(value.args)
            )
            queue.extend(
                (item, f"{path}.keywords[{index}]", depth + 1)
                for index, item in enumerate((value.keywords or {}).values())
            )
            continue
        if inspect.isfunction(value):
            for index, cell in enumerate(value.__closure__ or ()):
                try:
                    item = cell.cell_contents
                except ValueError:
                    continue
                queue.append((item, f"{path}.closure[{index}]", depth + 1))
            continue
        if isinstance(value, dict):
            queue.extend(
                (item, f"{path}.mapping[{index}]", depth + 1)
                for index, item in enumerate(value.values())
            )
            continue
        if isinstance(value, (list, tuple, set, frozenset, deque)):
            queue.extend(
                (item, f"{path}.items[{index}]", depth + 1)
                for index, item in enumerate(value)
            )
            continue
        try:
            attributes = vars(value)
        except TypeError:
            continue
        queue.extend(
            (item, f"{path}.{name}", depth + 1)
            for name, item in attributes.items()
        )
    return tuple(sorted(set(findings)))


def _git_paths(project_root: Path, args):
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args, "-z"],
        cwd=project_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git_path_enumeration_failed:{result.returncode}")
    try:
        output = result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("git_path_decoding_failed") from error
    return tuple(
        sorted({path.replace("\\", "/") for path in output.split("\0") if path})
    )


def repository_paths(project_root: Path):
    tracked = _git_paths(project_root, ("ls-files",))
    untracked = _git_paths(project_root, ("ls-files", "--others", "--exclude-standard"))
    return tracked, untracked


def audit_parser_budget(project_root: Path, max_lines=DEFAULT_MAX_PARSER_LINES):
    path = project_root / "engine" / "parser.py"
    try:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError) as error:
        return [AuditFinding("error", "parser_unreadable", "engine/parser.py", type(error).__name__)]
    if line_count <= max_lines:
        return []
    return [AuditFinding(
        "error",
        "parser_line_budget_exceeded",
        "engine/parser.py",
        f"line_count={line_count}, budget={max_lines}",
    )]


def audit_module_line_budgets(project_root: Path, budgets=None):
    """Prevent known large modules from growing before they are split."""
    findings = []
    for relative, budget in sorted((budgets or DEFAULT_MODULE_LINE_BUDGETS).items()):
        path = project_root / relative
        try:
            line_count = len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError) as error:
            findings.append(AuditFinding(
                "error", "budgeted_module_unreadable", relative,
                type(error).__name__,
            ))
            continue
        if line_count > int(budget):
            findings.append(AuditFinding(
                "error",
                "module_line_budget_exceeded",
                relative,
                f"line_count={line_count}, budget={int(budget)}",
            ))
    return findings


def audit_duplicated_vocabulary(project_root: Path, paths=None):
    """Fail when shared wording is redefined outside engine/vocabulary/."""
    findings = []
    candidates = paths
    if candidates is None:
        candidates = sorted(
            str(path.relative_to(project_root)).replace("\\", "/")
            for path in (project_root / "engine").rglob("*.py")
        )
    for relative in candidates:
        if relative.replace("\\", "/").startswith(VOCABULARY_HOME):
            continue
        try:
            source = (project_root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        pairs = ALIGNMENT_PAIR_RE.findall(source)
        canonical = {
            name
            for pair in pairs
            for name in ("left", "center", "right", "justify")
            if f'"{name}"' in pair
        }
        if (
            len(pairs) >= ALIGNMENT_PAIR_MIN
            and len(canonical) >= ALIGNMENT_DISTINCT_MIN
        ):
            findings.append(AuditFinding(
                "error",
                "duplicated_vocabulary_table",
                relative,
                f"정렬 어휘 매핑 {len(pairs)}건은 {VOCABULARY_HOME}/ 에서만 정의합니다.",
            ))
        lines = source.splitlines()
        seen = {
            marker: [
                index for index, line in enumerate(lines) if f'"{marker}"' in line
            ]
            for marker in CANCEL_MARKERS
        }
        if all(seen.values()):
            for anchor in seen[CANCEL_MARKERS[0]]:
                if all(
                    any(
                        abs(index - anchor) <= CANCEL_MARKER_WINDOW
                        for index in seen[marker]
                    )
                    for marker in CANCEL_MARKERS[1:]
                ):
                    findings.append(AuditFinding(
                        "error",
                        "duplicated_vocabulary_table",
                        relative,
                        f"취소 어휘 목록(line {anchor + 1})은 "
                        f"{VOCABULARY_HOME}/ 에서만 정의합니다.",
                    ))
                    break
    return findings


def audit_adapter_operation_methods(project_root: Path, adapters=None):
    """Keep user-visible actions out of the adapters.

    A per-operation ``_prepare_*`` or ``_execute_*`` on an adapter is the
    dispatch chain the operation split removed.  Line budgets cannot catch its
    return, because a new branch can be paid for by shrinking something else.
    """
    findings = []
    for relative in adapters or OPERATION_OWNING_ADAPTERS:
        path = project_root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as error:
            findings.append(AuditFinding(
                "error", "adapter_unreadable", relative, type(error).__name__,
            ))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if not isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                if member.name.startswith(ADAPTER_OPERATION_METHOD_PREFIXES):
                    findings.append(AuditFinding(
                        "error",
                        "adapter_carries_operation_method",
                        relative,
                        f"{node.name}.{member.name} 은 "
                        "engine/app_actions/operations/ 아래로 옮겨야 합니다.",
                    ))
    return findings


class _QualifiedFunctionVisitor(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.functions = []

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node):
        qualname = ".".join([*self.stack, node.name])
        self.functions.append((qualname, node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)


def audit_long_function_budgets(
    project_root: Path,
    *,
    exceptions=None,
    today=None,
):
    """Reject new/growing long functions and expired or stale exceptions."""
    configured = LONG_FUNCTION_EXCEPTIONS if exceptions is None else exceptions
    current_date = today or date.today()
    findings = []
    seen = set()
    for path in sorted((project_root / "engine").rglob("*.py")):
        relative = path.relative_to(project_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as error:
            findings.append(AuditFinding(
                "error", "function_budget_source_unreadable", relative,
                type(error).__name__,
            ))
            continue
        visitor = _QualifiedFunctionVisitor()
        visitor.visit(tree)
        for qualname, node in visitor.functions:
            length = int(node.end_lineno) - int(node.lineno) + 1
            if length <= MAX_FUNCTION_LINES:
                continue
            key = f"{relative}::{qualname}"
            seen.add(key)
            exception = configured.get(key)
            if exception is None:
                findings.append(AuditFinding(
                    "error", "long_function_unbudgeted", relative,
                    f"{qualname}={length}, limit={MAX_FUNCTION_LINES}",
                ))
                continue
            try:
                expiry = date.fromisoformat(exception.expires)
            except (TypeError, ValueError):
                expiry = date.min
            if not exception.owner.strip() or not exception.reason.strip():
                findings.append(AuditFinding(
                    "error", "long_function_exception_metadata_invalid", relative,
                    qualname,
                ))
            if expiry < current_date:
                findings.append(AuditFinding(
                    "error", "long_function_exception_expired", relative,
                    f"{qualname}, expired={exception.expires}",
                ))
            if length > int(exception.max_lines):
                findings.append(AuditFinding(
                    "error", "long_function_budget_exceeded", relative,
                    f"{qualname}={length}, budget={exception.max_lines}",
                ))
    for key in sorted(set(configured) - seen):
        relative, _, qualname = key.partition("::")
        findings.append(AuditFinding(
            "error", "stale_long_function_exception", relative, qualname,
        ))
    return findings


def _composition_files(project_root: Path):
    files = []
    for relative in COMPOSITION_SOURCES:
        path = project_root / relative
        if path.is_dir():
            files.extend(path.rglob("*.py"))
        elif path.is_file():
            files.append(path)
    return tuple(sorted(set(files)))


def audit_forbidden_back_references(project_root: Path):
    findings = []
    for path in _composition_files(project_root):
        relative = path.relative_to(project_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as error:
            findings.append(AuditFinding("error", "composition_source_unreadable", relative, type(error).__name__))
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in FORBIDDEN_BACK_REFERENCES
                ):
                    findings.append(AuditFinding(
                        "error",
                        "forbidden_parent_back_reference",
                        relative,
                        f"line={node.lineno}, attribute=self.{target.attr}",
                    ))
    return findings


def audit_tracked_artifacts(project_root: Path, tracked_paths):
    findings = []
    for relative in tracked_paths:
        path = Path(relative)
        lowered_parts = {part.casefold() for part in path.parts}
        if lowered_parts.intersection(FORBIDDEN_TRACKED_PARTS):
            findings.append(AuditFinding("error", "tracked_private_or_generated_directory", relative, "추적 금지 디렉터리의 파일입니다."))
        if path.suffix.casefold() in FORBIDDEN_TRACKED_SUFFIXES:
            findings.append(AuditFinding("error", "tracked_generated_artifact", relative, f"추적 금지 확장자: {path.suffix}"))
    return findings


def audit_secrets(project_root: Path, repository_relative_paths):
    findings = []
    for relative in repository_relative_paths:
        path = project_root / relative
        if path.suffix.casefold() not in TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            blob = path.read_bytes()
        except OSError as error:
            findings.append(AuditFinding("warning", "source_unreadable", relative, type(error).__name__))
            continue
        matched_codes = {
            code for code, pattern in GENERIC_SECRET_PATTERNS if pattern.search(blob)
        }
        if LITERAL_SECRET_PATTERN.search(blob):
            matched_codes.add("literal_secret_assignment")
        for code in sorted(matched_codes):
            findings.append(AuditFinding("error", code, relative, "비밀정보 형식과 일치하는 값이 있습니다. 값은 출력하지 않았습니다."))
    return findings


def audit_large_workspace_files(project_root: Path, threshold=DEFAULT_LARGE_FILE_BYTES):
    findings = []
    for generated_name in sorted(GENERATED_WORKSPACE_DIRS):
        generated = project_root / generated_name
        if generated.is_dir() and any(generated.iterdir()):
            findings.append(AuditFinding(
                "warning",
                "generated_workspace_directory_present",
                generated_name,
                "Git에는 포함되지 않지만 정기적으로 정리할 생성 디렉터리입니다.",
            ))

    excluded = EXCLUDED_PARTS | GENERATED_WORKSPACE_DIRS
    for directory, dirnames, filenames in os.walk(project_root):
        directory_path = Path(directory)
        dirnames[:] = [
            name for name in dirnames if name.casefold() not in excluded
        ]
        for filename in filenames:
            path = directory_path / filename
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= threshold:
                continue
            relative = path.relative_to(project_root)
            findings.append(AuditFinding(
                "warning",
                "large_workspace_file",
                relative.as_posix(),
                f"size_bytes={size}, threshold_bytes={threshold}",
            ))
    return findings


def run_audit(project_root: Path, *, max_parser_lines=DEFAULT_MAX_PARSER_LINES, large_file_bytes=DEFAULT_LARGE_FILE_BYTES):
    project_root = project_root.resolve()
    findings = []
    try:
        tracked, untracked = repository_paths(project_root)
    except (OSError, RuntimeError) as error:
        tracked, untracked = (), ()
        findings.append(AuditFinding(
            "error",
            "repository_paths_unavailable",
            ".",
            type(error).__name__,
        ))
    findings.extend(audit_parser_budget(project_root, max_parser_lines))
    findings.extend(audit_module_line_budgets(project_root))
    findings.extend(audit_adapter_operation_methods(project_root))
    findings.extend(audit_duplicated_vocabulary(project_root))
    findings.extend(audit_long_function_budgets(project_root))
    findings.extend(audit_forbidden_back_references(project_root))
    findings.extend(audit_tracked_artifacts(project_root, tracked))
    findings.extend(audit_secrets(project_root, (*tracked, *untracked)))
    findings.extend(audit_large_workspace_files(project_root, large_file_bytes))
    findings.sort(key=lambda item: (item.severity != "error", item.code, item.path))
    return {
        "schema_version": 1,
        "project_root": str(project_root),
        "tracked_file_count": len(tracked),
        "visible_untracked_file_count": len(untracked),
        "error_count": sum(item.severity == "error" for item in findings),
        "warning_count": sum(item.severity == "warning" for item in findings),
        "success": not any(item.severity == "error" for item in findings),
        "findings": [asdict(item) for item in findings],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-parser-lines", type=int, default=DEFAULT_MAX_PARSER_LINES)
    parser.add_argument("--large-file-mb", type=float, default=DEFAULT_LARGE_FILE_BYTES / (1024 * 1024))
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--strict", action="store_true", help="경고도 실패로 처리")
    args = parser.parse_args(argv)

    report = run_audit(
        args.root,
        max_parser_lines=args.max_parser_lines,
        large_file_bytes=max(1, int(args.large_file_mb * 1024 * 1024)),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"maintenance_audit success={report['success']} "
            f"errors={report['error_count']} warnings={report['warning_count']}"
        )
        for finding in report["findings"]:
            print(
                f"[{finding['severity'].upper()}] {finding['code']} "
                f"{finding['path']}: {finding['detail']}"
            )
    failed = not report["success"] or (args.strict and report["warning_count"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
