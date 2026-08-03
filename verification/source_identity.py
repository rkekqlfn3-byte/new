"""Content-free identity for the exact JARVIS source used by a probe."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_VERSION = 1
_SOURCE_ROOTS = frozenset({"engine", "gui", "tests", "verification"})
_SOURCE_SUFFIXES = frozenset({
    ".bat", ".cmd", ".css", ".html", ".ini", ".js", ".json", ".ps1",
    ".py", ".spec", ".toml", ".yaml", ".yml",
})


def _git(root: Path, *args: str, text: bool = True):
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=text,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _is_source_path(relative: str) -> bool:
    normalized = str(relative or "").replace("\\", "/").strip("/")
    if not normalized:
        return False
    path = Path(normalized)
    if path.name.endswith("_report.json"):
        return False
    if path.parts[0] not in _SOURCE_ROOTS and len(path.parts) > 1:
        return False
    return path.suffix.casefold() in _SOURCE_SUFFIXES


def _source_paths(root: Path) -> tuple[str, ...] | None:
    raw = _git(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        text=False,
    )
    if raw is None:
        return None
    paths = {
        value.decode("utf-8", errors="surrogateescape")
        for value in raw.split(b"\0")
        if value
    }
    return tuple(sorted(path for path in paths if _is_source_path(path)))


def _changed_source_paths(root: Path) -> tuple[str, ...] | None:
    changed = _git(
        root, "diff", "--name-only", "-z", "HEAD", "--", text=False
    )
    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        text=False,
    )
    if changed is None or untracked is None:
        return None
    paths = {
        value.decode("utf-8", errors="surrogateescape")
        for payload in (changed, untracked)
        for value in payload.split(b"\0")
        if value
    }
    return tuple(sorted(path for path in paths if _is_source_path(path)))


def _tree_hash(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        path = root / relative
        try:
            payload = path.read_bytes()
        except OSError:
            payload = b"[MISSING]"
        digest.update(hashlib.sha256(payload).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def source_identity(root: Path = ROOT) -> dict:
    """Return hashes only; source paths and contents never enter the report."""
    root = Path(root).resolve()
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    paths = _source_paths(root)
    changed_paths = _changed_source_paths(root)
    available = (
        commit is not None and paths is not None and changed_paths is not None
    )
    if not available:
        return {
            "identity_version": IDENTITY_VERSION,
            "commit": None,
            "branch": None,
            "dirty": None,
            "tree_hash": None,
            "changed_paths_hash": None,
        }
    changed_bytes = "\0".join(changed_paths).encode(
        "utf-8", errors="surrogateescape"
    )
    return {
        "identity_version": IDENTITY_VERSION,
        "commit": commit.strip(),
        "branch": (branch or "").strip(),
        "dirty": bool(changed_paths),
        "tree_hash": _tree_hash(root, paths),
        "changed_paths_hash": hashlib.sha256(changed_bytes).hexdigest(),
    }


def source_identity_errors(report_source, expected_source) -> tuple[str, ...]:
    """Return content-free mismatch reasons for one probe source identity."""
    required = (
        "identity_version", "commit", "dirty", "tree_hash",
        "changed_paths_hash",
    )
    if not isinstance(expected_source, dict) or any(
        expected_source.get(key) is None for key in required
    ):
        return ("current_source_identity_unavailable",)
    if not isinstance(report_source, dict) or any(
        report_source.get(key) is None for key in required
    ):
        return ("probe_source_identity_missing",)
    reasons = []
    for key in required:
        if report_source.get(key) != expected_source.get(key):
            reasons.append(f"probe_source_{key}_mismatch")
    return tuple(reasons)


__all__ = [
    "IDENTITY_VERSION",
    "ROOT",
    "source_identity",
    "source_identity_errors",
]
