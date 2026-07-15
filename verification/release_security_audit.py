"""Fail a build when release inputs, binaries, or archives contain private data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.version import APP_VERSION


PRIVATE_SOURCE_DIRS = {
    ".git", ".venv", "__pycache__", "backups", "build", "data", "dist"
}
REQUIRED_GITIGNORE_LINES = {
    ".venv/", "backups/", "build/", "data/", "dist/", "*.bak", "*.log"
}
FORBIDDEN_RELEASE_PARTS = {"backups", "sessions"}
FORBIDDEN_RELEASE_NAMES = {
    "execution_diagnostics.json", "jarvis-startup.log"
}
GENERIC_SECRET_PATTERNS = (
    ("openai_key_pattern", re.compile(rb"sk-[A-Za-z0-9_-]{20,}")),
    ("google_key_pattern", re.compile(rb"AIza[A-Za-z0-9_-]{20,}")),
    ("github_token_pattern", re.compile(rb"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}")),
    ("aws_access_key_pattern", re.compile(rb"AKIA[A-Z0-9]{16}")),
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _nonempty_api_key_paths(value, prefix=""):
    paths = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).casefold().replace("-", "_") in {"api_key", "apikey"}:
                if str(child or "").strip():
                    paths.append(child_path)
            paths.extend(_nonempty_api_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_nonempty_api_key_paths(child, f"{prefix}[{index}]"))
    return paths


def audit_default_data(default_dir: Path, label="default_data"):
    findings = []
    required = {
        "dictionaries.json", "user_memory.json", "user_preferences.json"
    }
    actual = {path.name for path in default_dir.glob("*.json")} if default_dir.is_dir() else set()
    for missing in sorted(required - actual):
        findings.append(Finding("missing_default_file", f"{label}/{missing}", "필수 초기 템플릿이 없습니다."))
    if not default_dir.is_dir():
        return findings

    loaded = {}
    for name in sorted(required & actual):
        path = default_dir / name
        try:
            loaded[name] = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            findings.append(Finding("invalid_default_json", f"{label}/{name}", type(error).__name__))

    dictionaries = loaded.get("dictionaries.json")
    if isinstance(dictionaries, dict):
        for field in _nonempty_api_key_paths(dictionaries):
            findings.append(Finding("default_contains_api_key", f"{label}/dictionaries.json", field))
        for field in (
            "noun_dictionary", "macro_dictionary", "search_engines_dict",
            "favorites", "user_nouns", "learned_macros",
        ):
            if dictionaries.get(field):
                findings.append(Finding("default_contains_user_data", f"{label}/dictionaries.json", field))
    elif dictionaries is not None:
        findings.append(Finding("invalid_default_schema", f"{label}/dictionaries.json", "최상위 값이 객체가 아닙니다."))

    memory = loaded.get("user_memory.json")
    if isinstance(memory, dict) and any(str(value or "").strip() for value in memory.values()):
        findings.append(Finding("default_contains_memory", f"{label}/user_memory.json", "비어 있지 않은 사용자 기억이 있습니다."))

    preferences = loaded.get("user_preferences.json")
    if isinstance(preferences, dict) and preferences.get("preferences"):
        findings.append(Finding("default_contains_preferences", f"{label}/user_preferences.json", "비어 있지 않은 사용자 선호가 있습니다."))
    return findings


def collect_known_secrets(user_data_dir: Path):
    """Read local fingerprints without returning or printing their values."""
    secrets = []
    if not user_data_dir.is_dir():
        return secrets
    for path in user_data_dir.rglob("*.json"):
        try:
            value = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue

        def visit(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = str(key).casefold().replace("-", "_")
                    if normalized in {"api_key", "apikey"}:
                        raw = str(child or "").strip().encode("utf-8")
                        if len(raw) >= 8 and raw not in secrets:
                            secrets.append(raw)
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(value)
    return secrets


def scan_blob(blob: bytes, path: str, known_secrets=(), generic=True):
    findings = []
    for index, secret in enumerate(known_secrets, start=1):
        if secret and secret in blob:
            findings.append(Finding("known_secret", path, f"로컬 비밀값 지문 #{index}이 포함되어 있습니다."))
    if generic:
        for code, pattern in GENERIC_SECRET_PATTERNS:
            if pattern.search(blob):
                findings.append(Finding(code, path, "비밀정보 형식과 일치하는 문자열이 있습니다."))
    return findings


def _iter_release_source_files(project_root: Path):
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project_root)
        if any(part.casefold() in PRIVATE_SOURCE_DIRS for part in relative.parts):
            continue
        yield path


def audit_release_inputs(project_root: Path, known_secrets):
    findings = []
    findings.extend(audit_default_data(project_root / "default_data"))

    spec = project_root / "Jarvis.spec"
    try:
        spec_text = spec.read_text(encoding="utf-8")
    except OSError as error:
        findings.append(Finding("missing_build_spec", str(spec), type(error).__name__))
    else:
        if "default_data" not in spec_text:
            findings.append(Finding("default_data_not_bundled", "Jarvis.spec", "깨끗한 초기 템플릿이 빌드 입력에 없습니다."))
        if "build_identity.json" not in spec_text:
            findings.append(Finding("build_identity_not_bundled", "Jarvis.spec", "Git 빌드 식별 정보가 빌드 입력에 없습니다."))
        if "Jarvis_version_info.txt" not in spec_text:
            findings.append(Finding("windows_version_not_bundled", "Jarvis.spec", "Windows 버전 리소스가 설정되지 않았습니다."))
        if re.search(r"['\"]data[\\/]", spec_text, re.IGNORECASE):
            findings.append(Finding("private_data_in_build_spec", "Jarvis.spec", "쓰기 가능한 legacy data 폴더를 직접 포함합니다."))

    ignore_path = project_root / ".gitignore"
    try:
        lines = {
            line.strip() for line in ignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError as error:
        findings.append(Finding("missing_gitignore", ".gitignore", type(error).__name__))
    else:
        for line in sorted(REQUIRED_GITIGNORE_LINES - lines):
            findings.append(Finding("gitignore_gap", ".gitignore", f"누락: {line}"))

    for path in _iter_release_source_files(project_root):
        try:
            findings.extend(scan_blob(path.read_bytes(), str(path.relative_to(project_root)), known_secrets, generic=False))
        except OSError:
            findings.append(Finding("source_read_error", str(path), "파일을 검사하지 못했습니다."))
    return findings


def _audit_release_json(blob: bytes, path: str):
    try:
        value = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        return []
    return [
        Finding("release_contains_api_key", path, field)
        for field in _nonempty_api_key_paths(value)
    ]


def _audit_build_identity(blob: bytes, path: str):
    try:
        value = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        return [Finding("invalid_build_identity", path, "빌드 식별 JSON을 읽을 수 없습니다.")]
    findings = []
    if value.get("app_version") != APP_VERSION:
        findings.append(Finding(
            "build_version_mismatch", path,
            f"expected={APP_VERSION}, actual={value.get('app_version')}",
        ))
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("git_commit", ""))):
        findings.append(Finding("invalid_build_commit", path, "Git commit 형식이 올바르지 않습니다."))
    if value.get("git_dirty") is not False:
        findings.append(Finding("dirty_build_identity", path, "깨끗한 소스 빌드가 아닙니다."))
    if not str(value.get("built_at_utc", "")).strip():
        findings.append(Finding("missing_build_time", path, "UTC 빌드 시각이 없습니다."))
    return findings


def _forbidden_path_finding(parts, path):
    lowered = {part.casefold() for part in parts}
    if lowered & FORBIDDEN_RELEASE_PARTS:
        return Finding("release_contains_private_directory", path, "세션 또는 백업 디렉터리가 포함되어 있습니다.")
    if parts and parts[-1].casefold() in FORBIDDEN_RELEASE_NAMES:
        return Finding("release_contains_private_file", path, "진단 또는 시작 로그가 포함되어 있습니다.")
    return None


def audit_distribution(dist_dir: Path, known_secrets):
    findings = []
    if not dist_dir.is_dir():
        return [Finding("missing_distribution", str(dist_dir), "배포 폴더가 없습니다.")]
    for path in dist_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(dist_dir)
        forbidden = _forbidden_path_finding(relative.parts, str(relative))
        if forbidden:
            findings.append(forbidden)
        try:
            blob = path.read_bytes()
        except OSError:
            findings.append(Finding("distribution_read_error", str(relative), "파일을 검사하지 못했습니다."))
            continue
        findings.extend(scan_blob(blob, str(relative), known_secrets, generic=True))
        if path.suffix.casefold() == ".json":
            findings.extend(_audit_release_json(blob, str(relative)))

    bundled_default = dist_dir / "_internal" / "default_data"
    findings.extend(audit_default_data(bundled_default, "dist/_internal/default_data"))
    identity_path = dist_dir / "_internal" / "build_identity.json"
    if not identity_path.is_file():
        findings.append(Finding(
            "missing_build_identity", "dist/_internal/build_identity.json",
            "배포본에 빌드 식별 정보가 없습니다.",
        ))
    else:
        try:
            findings.extend(_audit_build_identity(
                identity_path.read_bytes(), "dist/_internal/build_identity.json"
            ))
        except OSError:
            findings.append(Finding(
                "distribution_read_error", "dist/_internal/build_identity.json",
                "빌드 식별 정보를 읽지 못했습니다.",
            ))
    return findings


def audit_archive(archive_path: Path, known_secrets):
    findings = []
    identity_found = False
    if not archive_path.is_file():
        return [Finding("missing_archive", str(archive_path), "배포 압축 파일이 없습니다.")]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = Path(info.filename).parts
                forbidden = _forbidden_path_finding(parts, info.filename)
                if forbidden:
                    findings.append(forbidden)
                blob = archive.read(info)
                findings.extend(scan_blob(blob, info.filename, known_secrets, generic=True))
                if info.filename.casefold().endswith(".json"):
                    findings.extend(_audit_release_json(blob, info.filename))
                normalized_name = info.filename.replace("\\", "/").casefold()
                if (
                    normalized_name == "_internal/build_identity.json"
                    or normalized_name.endswith("/_internal/build_identity.json")
                ):
                    identity_found = True
                    findings.extend(_audit_build_identity(blob, info.filename))
    except (OSError, zipfile.BadZipFile) as error:
        findings.append(Finding("invalid_archive", str(archive_path), type(error).__name__))
    if archive_path.is_file() and not identity_found:
        findings.append(Finding(
            "missing_build_identity", str(archive_path),
            "ZIP에 빌드 식별 정보가 없습니다.",
        ))
    return findings


def audit_git_history(project_root: Path, known_secrets):
    git = shutil.which("git")
    if not (project_root / ".git").exists():
        return "not_a_git_repository", []
    if not git:
        return "git_unavailable", []
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "log", "-p", "--all", "--full-history"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return type(error).__name__, [Finding("git_history_scan_failed", ".git", type(error).__name__)]
    findings = scan_blob(result.stdout, "<git-history>", known_secrets, generic=False)
    return "scanned" if result.returncode == 0 else f"git_exit_{result.returncode}", findings


def _default_user_data_dir():
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Jarvis" / "data"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--archive", action="append", type=Path, default=[])
    parser.add_argument("--user-data-dir", type=Path, default=_default_user_data_dir())
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    known_secrets = collect_known_secrets(args.user_data_dir.resolve())
    findings = audit_release_inputs(project_root, known_secrets)
    git_status, git_findings = audit_git_history(project_root, known_secrets)
    findings.extend(git_findings)
    if args.dist:
        findings.extend(audit_distribution(args.dist.resolve(), known_secrets))
    for archive in args.archive:
        findings.extend(audit_archive(archive.resolve(), known_secrets))

    report = {
        "status": "passed" if not findings else "failed",
        "project_root": str(project_root),
        "known_secret_fingerprint_count": len(known_secrets),
        "git_history_scan": git_status,
        "distribution_scanned": bool(args.dist),
        "archives_scanned": len(args.archive),
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"release_security_audit={report['status']}")
    print(f"known_secret_fingerprints={len(known_secrets)}")
    print(f"git_history_scan={git_status}")
    print(f"findings={len(findings)}")
    for finding in findings:
        print(f"[{finding.code}] {finding.path}: {finding.detail}")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
