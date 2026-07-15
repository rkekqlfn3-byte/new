"""Generate commit and Windows version metadata for a clean release build."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from engine.version import APP_VERSION, DATA_SCHEMA_VERSION


def _git_executable(explicit=None):
    candidate = explicit or os.environ.get("JARVIS_GIT") or shutil.which("git")
    if not candidate:
        raise RuntimeError(
            "Git executable not found. Set JARVIS_GIT to the full git.exe path."
        )
    return candidate


def _git(git_executable, root, *arguments):
    completed = subprocess.run(
        [git_executable, *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Git command failed.")
    return completed.stdout.strip()


def _version_tuple(version):
    numbers = [int(value) for value in re.findall(r"\d+", version)[:4]]
    return tuple((numbers + [0, 0, 0, 0])[:4])


def _version_resource(version, commit):
    numeric = _version_tuple(version)
    short_commit = commit[:12]
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric}, prodvers={numeric},
    mask=0x3f, flags=0x0, OS=0x40004,
    fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('041204B0', [
        StringStruct('CompanyName', 'Jarvis'),
        StringStruct('FileDescription', 'Jarvis Command Center'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Jarvis'),
        StringStruct('OriginalFilename', 'Jarvis.exe'),
        StringStruct('ProductName', 'Jarvis Command Center'),
        StringStruct('ProductVersion', '{version} ({short_commit})')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0412, 1200])])
  ]
)
"""


def generate(project_root, git_path=None, allow_dirty=False):
    root = Path(project_root).resolve()
    git_executable = _git_executable(git_path)
    commit = _git(git_executable, root, "rev-parse", "HEAD")
    status = _git(
        git_executable, root, "status", "--porcelain", "--untracked-files=all"
    )
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise RuntimeError(
            "Working tree is not clean. Commit or remove changes before building."
        )

    build_dir = root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    identity = {
        "app_version": APP_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
        "git_commit": commit,
        "git_dirty": dirty,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    identity_path = build_dir / "build_identity.json"
    temp_path = identity_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, identity_path)
    (build_dir / "Jarvis_version_info.txt").write_text(
        _version_resource(APP_VERSION, commit), encoding="utf-8"
    )
    print(json.dumps(identity, ensure_ascii=False))
    return identity


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--git")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    try:
        generate(args.project_root, git_path=args.git, allow_dirty=args.allow_dirty)
    except RuntimeError as error:
        print(f"[ERROR] {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
