"""Apply bounded post-build cleanup and record P2 distribution evidence."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


REMOVABLE_METADATA_FILES = frozenset({
    "INSTALLER",
    "RECORD",
    "REQUESTED",
    "direct_url.json",
})
EXCLUDED_BINARIES = frozenset({"win32trace.pyd"})


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def optimize_distribution(dist_root: Path) -> dict:
    root = dist_root.resolve()
    if not root.is_dir() or not (root / "Jarvis.exe").is_file():
        raise ValueError(f"Jarvis 배포 폴더가 아닙니다: {root}")

    candidates = []
    for metadata_dir in root.rglob("*.dist-info"):
        if not metadata_dir.is_dir() or not _inside(root, metadata_dir):
            continue
        for name in REMOVABLE_METADATA_FILES:
            candidate = (metadata_dir / name).resolve()
            if candidate.is_file() and _inside(root, candidate):
                candidates.append(candidate)

    removed = []
    removed_bytes = 0
    for candidate in sorted(candidates):
        size = candidate.stat().st_size
        candidate.unlink()
        removed.append(str(candidate.relative_to(root)))
        removed_bytes += size

    bundled_files = [path for path in root.rglob("*") if path.is_file()]
    excluded_found = sorted({
        path.name.casefold()
        for path in bundled_files
        if path.name.casefold() in EXCLUDED_BINARIES
    })
    total_bytes = sum(path.stat().st_size for path in bundled_files)
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dist_root": str(root),
        "file_count": len(bundled_files),
        "total_bytes": total_bytes,
        "removed_metadata_files": removed,
        "removed_metadata_bytes": removed_bytes,
        "excluded_binaries_found": excluded_found,
        "upx_available_on_path": bool(shutil.which("upx")),
        "upx_enabled": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = optimize_distribution(args.dist)
    except (OSError, ValueError) as error:
        print(f"[ERROR] P2 배포 최적화 실패: {error}")
        return 1

    print(
        "[P2] metadata removed: "
        f"{len(report['removed_metadata_files'])} files / "
        f"{report['removed_metadata_bytes']} bytes"
    )
    print(f"[P2] distribution size: {report['total_bytes']} bytes")
    print(
        "[P2] UPX: disabled; executable on PATH="
        f"{report['upx_available_on_path']}"
    )
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if report["excluded_binaries_found"]:
        print(
            "[ERROR] 제외 대상 바이너리가 배포본에 남아 있습니다: "
            + ", ".join(report["excluded_binaries_found"])
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
