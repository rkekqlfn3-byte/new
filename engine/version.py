"""Central application version and source/build identity."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


# Release candidate for the R0-R4 remediation work. It becomes the runtime
# current build only after the complete R6 validation gate passes.
APP_VERSION = "1.1.0-rc.3"

# Persisted user-data schema (see default_data/dictionaries.json schema_version).
DATA_SCHEMA_VERSION = 4


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git_directory(project_root: Path) -> Path | None:
    marker = project_root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if text.lower().startswith("gitdir:"):
            return (project_root / text.split(":", 1)[1].strip()).resolve()
    return None


def _source_commit(project_root: Path) -> str:
    """Read HEAD without requiring a Git executable at application runtime."""
    git_dir = _git_directory(project_root)
    if not git_dir:
        return "unknown"
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if not head.startswith("ref:"):
            return head
        ref_name = head.split(":", 1)[1].strip()
        loose_ref = git_dir / Path(ref_name)
        if loose_ref.is_file():
            return loose_ref.read_text(encoding="ascii").strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_file():
            for line in packed_refs.read_text(encoding="ascii").splitlines():
                if line and not line.startswith(("#", "^")):
                    commit, name = line.split(" ", 1)
                    if name == ref_name:
                        return commit
    except (OSError, ValueError):
        pass
    return "unknown"


def _packaged_identity() -> dict:
    bundle_root = Path(getattr(sys, "_MEIPASS", _project_root()))
    identity_path = bundle_root / "build_identity.json"
    try:
        data = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _source_dirty(project_root: Path):
    git_executable = os.environ.get("JARVIS_GIT") or shutil.which("git")
    if not git_executable:
        return None
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    )
    try:
        completed = subprocess.run(
            [git_executable, "status", "--porcelain", "--untracked-files=all"],
            cwd=project_root,
            capture_output=True,
            check=False,
            timeout=5,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(completed.stdout) if completed.returncode == 0 else None


@lru_cache(maxsize=1)
def runtime_info() -> dict:
    """Return the exact source or packaged build identity for diagnostics."""
    frozen = bool(getattr(sys, "frozen", False))
    identity = _packaged_identity() if frozen else {}
    metadata_version = str(identity.get("app_version", "")) if frozen else ""
    return {
        "app_version": APP_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
        "git_commit": (
            str(identity.get("git_commit", "unknown"))
            if frozen else _source_commit(_project_root())
        ),
        "git_dirty": (
            identity.get("git_dirty") if frozen else _source_dirty(_project_root())
        ),
        "built_at_utc": str(identity.get("built_at_utc", "")),
        "build_kind": "frozen" if frozen else "source",
        "build_metadata_version": metadata_version,
        "identity_matches_app_version": (
            metadata_version == APP_VERSION if frozen else True
        ),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "frozen": frozen,
    }
