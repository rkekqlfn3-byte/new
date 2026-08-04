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

from engine.app_actions.com_lifecycle import com_apartment
from engine.app_actions.hwp_adapter import HwpAdapter, create_owned_hwp_application
from engine.app_actions.operations.hwp.insert_table import table_control_count
from engine.workflows.business_workflow import _registered_hwp_security_module
from verification.source_identity import source_identity

REPORT_PATH = Path(__file__).with_name("hwp_table_report.json")
ROWS = 3
COLUMNS = 4


def _probe(lease, steps, checks):
    hwp = lease.application
    # A fresh HwpObject already owns a blank document, and 한글 refuses
    # automation that reads document state until the user-registered file
    # security module is active. This mirrors what HwpReportWriter does.
    steps.append("register_security_module")
    module_name = _registered_hwp_security_module()
    if not module_name:
        raise RuntimeError("hwp_security_module_not_registered")
    if not bool(hwp.RegisterModule("FilePathCheckDLL", module_name)):
        raise RuntimeError("hwp_security_module_rejected")
    checks["security_module_registered"] = True

    # 한글 does not realise the document window for a DispatchEx instance until
    # it is made visible, and Active_XHwpWindow raises "객체가 연결되지
    # 않았습니다" until then. This is required, not cosmetic.
    steps.append("realise_window")
    hwp.XHwpWindows.Item(0).Visible = True
    checks["window_realised"] = bool(hwp.XHwpWindows.Item(0).Visible)

    steps.append("adapter")
    # com_runtime=False: this probe owns the surrounding apartment for the
    # whole lifetime of the proxy. Letting the adapter open and close its own
    # apartment per call invalidates HeadCtrl between calls.
    adapter = HwpAdapter(
        object_getter=lambda: hwp, require_visible=False, com_runtime=False
    )
    checks["empty_document_has_no_table"] = table_control_count(hwp) == 0

    steps.append("prepare")
    prepared = adapter.prepare(
        "insert_table", {"rows": ROWS, "columns": COLUMNS}
    )
    checks["preview_created_nothing"] = table_control_count(hwp) == 0
    checks["preview_reports_requested_size"] = (
        prepared.params["rows"] == ROWS and prepared.params["columns"] == COLUMNS
    )

    steps.append("execute")
    result = adapter.execute(prepared)
    checks["execute_reported_verified"] = bool(result.get("verified"))
    checks["one_table_control_added"] = table_control_count(hwp) == 1
    checks["result_counted_the_table"] = result["after"]["table_count"] == 1

    steps.append("undo")
    restored = adapter.undo(prepared, {"after_observations": result})
    checks["undo_reported_verified"] = bool(restored.get("verified"))
    checks["undo_removed_the_table"] = table_control_count(hwp) == 0
    return checks


def run():
    checks = {}
    error = None
    detail = ""
    steps = ["create_application"]
    # Cleanup must happen inside the apartment: once com_apartment exits it
    # calls CoUninitialize, and Quit on a dead proxy leaves 한글 running.
    with com_apartment(None):
        lease = None
        try:
            lease = create_owned_hwp_application()
            _probe(lease, steps, checks)
        except Exception as caught:  # noqa: BLE001 - reported, never raised raw
            error = type(caught).__name__
            # The message can name a COM interface or an HRESULT, never
            # document content, so it is safe to record and is the only
            # useful diagnosis.
            detail = str(caught)[:300]
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
            "failed_step": steps[-1] if error else None,
            "error_detail": detail,
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
