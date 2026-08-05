"""Check the arguments PDF commands accept, not just that they run.

The acceptance battery already proves all eleven PDF intents work — 120
cases, owned fixtures, no user document touched.  What it does not cover is
the space inside each intent: it rotates by 180 and by "오른쪽", never by
270, and it never asks for a report as 한글 or as PowerPoint even though
the intent accepts both.

An argument that is accepted but wrong is worse than one that is refused,
so each combination is driven against a real PDF and the result checked
against what the transformation service verified: the rotations it read
back, the page count it counted, the output kinds it parsed.

Everything happens on fixtures this probe writes and deletes.  No user
document is opened, nothing is sent anywhere, and the external-transfer
path is only ever prepared, never confirmed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from engine.pdf import PdfIntakeManager, PdfTransformationService
from engine.pdf.intent import PdfIntentKind
from tests.fixtures.pdf_factory import write_text_pdf

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path(__file__).with_name("pdf_argument_report.json")
SCRATCH_ROOT = ROOT / "tmp" / "pdfs"

# Every rotation the intent accepts, and the wording that should reach it.
ROTATIONS = (
    ("이 PDF 2페이지를 오른쪽으로 회전해줘", 90),
    ("이 PDF 2페이지를 180도 회전해줘", 180),
    ("이 PDF 2페이지를 왼쪽으로 회전해줘", 270),
    ("이 PDF 2페이지를 270도 돌려줘", 270),
)

# Every output the report intent accepts.
REPORTS = (
    ("이 PDF로 보고서 만들어줘", ("word",)),
    ("이 PDF로 한글 보고서 만들어줘", ("hwp",)),
    ("이 PDF로 발표자료 만들어줘", ("powerpoint",)),
    ("이 PDF 표를 엑셀로 뽑아줘", ("excel",)),
)

# The wording, and how many pages the result must contain.
SPLITS = (
    ("이 PDF 1페이지만 분할해줘", 1),
    ("이 PDF 2~3페이지만 분할해줘", 2),
    ("이 PDF 1,3페이지만 분할해줘", 2),
)


def _prepare_directory():
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="pdf-args-", dir=SCRATCH_ROOT))


def _check_rotations(manager, service) -> list[dict]:
    """The service withholds the output path on purpose, so its own
    verification is the read-back. An earlier version of this probe looked
    for a path that is never reported, found nothing, and treated "could not
    read" as "matched" — a check that passed without checking anything.
    """
    records = []
    for command, expected in ROTATIONS:
        record = {"kind": "rotate", "command": command, "expected": expected}
        try:
            request = manager.resolve_command(command)
            record["parsed"] = request.intent.rotation_degrees
            result = service.execute(service.prepare(request))
            verification = dict(result.get("verification") or {})
            record["rotations_verified"] = bool(
                verification.get("page_rotations_verified")
            )
            record["pages"] = verification.get("page_count")
            record["ok"] = (
                record["parsed"] == expected
                and record["rotations_verified"]
                and record["pages"] == 4
            )
        except Exception as error:
            record["ok"] = False
            record["error"] = f"{type(error).__name__}: {error}"[:140]
        records.append(record)
    return records


def _check_reports(manager) -> list[dict]:
    records = []
    for command, expected in REPORTS:
        record = {"kind": "report", "command": command, "expected": list(expected)}
        try:
            intent = manager.parse_intent(command)
            record["parsed_kind"] = intent.kind.value
            record["parsed_outputs"] = list(intent.output_kinds)
            record["ok"] = tuple(intent.output_kinds) == expected
        except Exception as error:
            record["ok"] = False
            record["error"] = f"{type(error).__name__}: {error}"[:140]
        records.append(record)
    return records


def _check_splits(manager, service) -> list[dict]:
    records = []
    for command, expected_pages in SPLITS:
        record = {
            "kind": "split", "command": command, "expected_pages": expected_pages
        }
        try:
            request = manager.resolve_command(command)
            record["parsed_kind"] = request.intent.kind.value
            result = service.execute(service.prepare(request))
            verification = dict(result.get("verification") or {})
            record["pages"] = verification.get("page_count")
            record["ok"] = (
                request.intent.kind is PdfIntentKind.SPLIT
                and bool(verification.get("page_order_verified"))
                and record["pages"] == expected_pages
            )
        except Exception as error:
            record["ok"] = False
            record["error"] = f"{type(error).__name__}: {error}"[:140]
        records.append(record)
    return records


def run() -> dict:
    directory = _prepare_directory()
    try:
        source = write_text_pdf(
            directory / "arguments.pdf",
            ["Argument One", "Argument Two", "Argument Three", "Argument Four"],
        )
        other = write_text_pdf(directory / "other.pdf", ["Other One"])
        manager = PdfIntakeManager()
        manager.connect_file(str(source))
        service = PdfTransformationService(
            manager, merge_picker=lambda: (str(other),)
        )
        records = [
            *_check_rotations(manager, service),
            *_check_reports(manager),
            *_check_splits(manager, service),
        ]
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        for candidate in (SCRATCH_ROOT, SCRATCH_ROOT.parent):
            try:
                candidate.rmdir()
            except OSError:
                pass

    failures = [record for record in records if not record.get("ok")]
    return {
        "schema_version": 1,
        "total": len(records),
        "passed": len(records) - len(failures),
        "success": not failures,
        "records": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    report = run()
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for record in report["records"]:
        mark = "O" if record.get("ok") else "X"
        detail = record.get("error") or ""
        print(f"  {mark} {record['kind']:8s} {record['command'][:34]:36s} {detail}")
    print(f"\n{report['passed']}/{report['total']}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
