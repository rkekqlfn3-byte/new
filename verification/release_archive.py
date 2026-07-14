"""Create a clean ZIP archive from a verified onedir distribution."""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path


def create_archive(source: Path, output: Path):
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"배포 폴더가 없습니다: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, Path(source.name) / path.relative_to(source))
    os.replace(temporary, output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = create_archive(args.source, args.output)
    except (OSError, zipfile.BadZipFile) as error:
        print(f"archive_failed={type(error).__name__}: {error}")
        return 1
    print(f"archive_created={output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
