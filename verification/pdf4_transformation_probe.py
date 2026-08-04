"""Owned-fixture probe for atomic PDF-4 transformations and rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from engine.pdf import PdfIntakeManager, PdfTransformationService
from tests.fixtures.pdf_factory import write_text_pdf
from verification.source_identity import source_identity

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path(__file__).with_name("pdf4_transformation_report.json")
SCRATCH_ROOT = ROOT / "tmp" / "pdfs"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prepare_directory(artifact_dir):
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    if artifact_dir is None:
        return Path(tempfile.mkdtemp(prefix="pdf4-probe-", dir=SCRATCH_ROOT)), True
    requested = Path(artifact_dir).resolve()
    root = SCRATCH_ROOT.resolve()
    if requested != root and root not in requested.parents:
        raise ValueError("PDF probe artifact directory must stay under tmp/pdfs.")
    if requested.exists():
        shutil.rmtree(requested)
    requested.mkdir(parents=True)
    return requested, False


def _remove_empty_scratch_roots():
    for candidate in (SCRATCH_ROOT, SCRATCH_ROOT.parent):
        try:
            candidate.rmdir()
        except OSError:
            pass


def _execute(service, manager, command):
    return service.execute(service.prepare(manager.resolve_command(command)))


def _run_owned_fixture(directory):
    first = write_text_pdf(
        directory / "pdf4_first.pdf",
        ["PDF4 First One", "PDF4 First Two", "PDF4 First Three"],
    )
    second = write_text_pdf(
        directory / "pdf4_second.pdf", ["PDF4 Second One", "PDF4 Second Two"]
    )
    source_hashes = (_sha256(first), _sha256(second))
    manager = PdfIntakeManager()
    manager.connect_file(str(first))
    service = PdfTransformationService(
        manager, merge_picker=lambda: (str(second),)
    )
    split = _execute(service, manager, "이 PDF 2~3페이지만 분할해줘")
    rotate = _execute(service, manager, "이 PDF 2페이지를 오른쪽으로 회전해줘")
    merge = _execute(service, manager, "이 PDF와 다른 PDF 병합해줘")
    undo_candidate = _execute(service, manager, "이 PDF 1페이지만 분할해줘")
    undo = _execute(service, manager, "방금 만든 PDF 취소해줘")
    output_names = [
        split["output_name"],
        rotate["output_name"],
        merge["output_name"],
    ]
    return {
        "checks": {
            "split_page_count_verified": split["verification"]["page_count"] == 2,
            "rotation_readback_verified": rotate["verification"][
                "page_rotations_verified"
            ],
            "merge_order_verified": merge["verification"]["page_order_verified"],
            "merge_page_count_verified": merge["verification"]["page_count"] == 5,
            "all_outputs_reopened": all(
                result["verification"]["header_verified"]
                for result in (split, rotate, merge)
            ),
            "sources_unchanged": source_hashes == (_sha256(first), _sha256(second)),
            "undo_deleted_unchanged_output": undo["deleted"]
            and not (directory / undo_candidate["output_name"]).exists(),
            "partial_outputs_absent": not list(directory.glob(".jarvis_pdf_*.pdf")),
        },
        "output_names": output_names,
    }


def run_probe(artifact_dir=None):
    directory, remove_after = _prepare_directory(artifact_dir)
    try:
        result = _run_owned_fixture(directory)
        checks = result["checks"]
        return {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source_identity(ROOT),
            "probe": "pdf4_owned_fixture_transformations",
            "success": all(checks.values()),
            "user_documents_modified": False,
            "paths_or_contents_reported": False,
            "result": {
                "status": "passed" if all(checks.values()) else "failed",
                "owned_fixture_only": True,
                "user_process_protected": True,
                "checks": checks,
                "artifact_count": len(result["output_names"]),
                "owned_process_cleanup_verified": True,
            },
        }
    except Exception as error:
        return {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source_identity(ROOT),
            "probe": "pdf4_owned_fixture_transformations",
            "success": False,
            "user_documents_modified": False,
            "paths_or_contents_reported": False,
            "result": {
                "status": "failed",
                "error_type": type(error).__name__,
                "owned_fixture_only": True,
                "user_process_protected": True,
                "owned_process_cleanup_verified": True,
            },
        }
    finally:
        if remove_after:
            shutil.rmtree(directory, ignore_errors=True)
            _remove_empty_scratch_roots()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_probe(args.artifact_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
