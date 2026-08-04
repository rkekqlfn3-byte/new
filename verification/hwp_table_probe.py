"""Confirm the 한글 table automation call against a real 한글 install.

Nothing else in this repository creates a 한글 table, so ``insert_table``'s COM
call shape has no precedent here and the unit tests cannot prove it: they run
against a fake this repository also wrote, which can only confirm the fake
agrees with the operation.  This probe is the part that proves the automation.

It uses a 한글 instance it creates and closes itself, and a document it never
saves, so no user document is touched.  The report records booleans and counts
only - never document text or paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.app_actions.hwp_adapter import HwpAdapter, create_owned_hwp_application
from engine.app_actions.operations.hwp.insert_table import table_control_count
from verification.source_identity import source_identity

REPORT_PATH = Path(__file__).with_name("hwp_table_report.json")
ROWS = 3
COLUMNS = 4


def _probe(lease):
    hwp = lease.application
    checks = {}
    hwp.XHwpWindows.Item(0).Visible = True
    hwp.HAction.Run("FileNew")

    adapter = HwpAdapter(object_getter=lambda: hwp, require_visible=False)
    checks["empty_document_has_no_table"] = table_control_count(hwp) == 0

    prepared = adapter.prepare(
        "insert_table", {"rows": ROWS, "columns": COLUMNS}
    )
    checks["preview_created_nothing"] = table_control_count(hwp) == 0
    checks["preview_reports_requested_size"] = (
        prepared.params["rows"] == ROWS and prepared.params["columns"] == COLUMNS
    )

    result = adapter.execute(prepared)
    checks["execute_reported_verified"] = bool(result.get("verified"))
    checks["one_table_control_added"] = table_control_count(hwp) == 1
    checks["result_counted_the_table"] = result["after"]["table_count"] == 1

    restored = adapter.undo(prepared, {"after_observations": result})
    checks["undo_reported_verified"] = bool(restored.get("verified"))
    checks["undo_removed_the_table"] = table_control_count(hwp) == 0
    return checks


def run():
    lease = None
    checks = {}
    error = None
    try:
        lease = create_owned_hwp_application()
        checks = _probe(lease)
    except Exception as caught:  # noqa: BLE001 - reported, never re-raised raw
        error = type(caught).__name__
    finally:
        if lease is not None:
            try:
                lease.application.XHwpDocuments.Item(0).Clear(option=1)
            except Exception:
                pass
            try:
                lease.cleanup()
            except Exception:
                pass
    passed = bool(checks) and all(checks.values()) and error is None
    report = {
        "probe": "hwp_table_automation",
        "source": source_identity(),
        "success": passed,
        "user_documents_modified": False,
        "paths_or_contents_reported": False,
        "result": {
            "status": "passed" if passed else "failed",
            "owned_fixture_only": True,
            "requested_rows": ROWS,
            "requested_columns": COLUMNS,
            "checks": checks,
            "error_type": error,
        },
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = run()
    print(json.dumps(report["result"], ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
