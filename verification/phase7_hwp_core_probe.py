"""Isolated live-Hanword probe for phase 7 native HWP actions."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

import pythoncom
import win32com.client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.app_actions.base import AppActionBlocked, AppActionContextChanged
from engine.app_actions.hwp_adapter import HwpAdapter

REPORT_PATH = os.path.join(ROOT, "verification", "phase7_hwp_core_report.json")
PROGRESS_PATH = os.path.join(ROOT, "verification", "phase7_hwp_core_progress.txt")


def progress(value):
    with open(PROGRESS_PATH, "w", encoding="utf-8") as stream:
        stream.write(str(value) + "\n")


def rot_names():
    context = pythoncom.CreateBindCtx(0)
    running_table = pythoncom.GetRunningObjectTable()
    names = []
    for moniker in running_table.EnumRunning():
        try:
            name = moniker.GetDisplayName(context, moniker)
        except Exception:
            continue
        if str(name).startswith("!HwpObject."):
            names.append(str(name))
    return sorted(names)


def main():
    report = {
        "phase": 7,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checks": [],
        "success": False,
    }
    temporary_root = tempfile.mkdtemp(prefix="jarvis-hwp-phase7-")
    pdf_path = os.path.join(temporary_root, "probe.pdf")
    hwp = None
    owned = False
    before_names = rot_names()
    report["existing_hwp_object_count"] = len(before_names)
    try:
        progress("prepare visible connection")
        # This is prepare-only: it proves the production ROT connector can read
        # the user's visible active document without changing it.
        production = HwpAdapter()
        try:
            prepared_connection = production.prepare(
                "insert_text", {"text": "JARVIS_READ_ONLY_CONNECTION_PROBE"}
            )
        except AppActionBlocked as error:
            if "읽기 전용" not in str(error):
                raise
            report["checks"].append({
                "name": "visible_active_document_read_only_blocked",
                "passed": True,
            })
        else:
            report["checks"].append({
                "name": "visible_active_document_prepare_only",
                "passed": prepared_connection.app == "hwp",
            })

        progress("create isolated object")
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        created_names = sorted(set(rot_names()) - set(before_names))
        if not created_names:
            raise RuntimeError("A separate HwpObject was not created; refusing to mutate.")
        owned = True
        report["isolated_hwp_object_count"] = len(created_names)
        adapter = HwpAdapter(object_getter=lambda: hwp, require_visible=False)

        progress("insert")
        prepared = adapter.prepare(
            "insert_text", {"text": "foo one foo\r\nfoo two"}
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        assert hwp.GetTextFile("UNICODE", "") == "foo one foo\r\nfoo two"
        report["checks"].append({"name": "insert_text", "passed": True})

        progress("text format")
        hwp.HAction.Run("SelectAll")
        prepared = adapter.prepare(
            "set_text_format",
            {"bold": True, "font_size": 15, "text_color": "red"},
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        report["checks"].append({"name": "set_text_format", "passed": True})

        progress("paragraph format")
        prepared = adapter.prepare(
            "set_paragraph_format", {"alignment": "center"}
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        report["checks"].append({
            "name": "set_paragraph_format", "passed": True
        })

        progress("selection replace")
        prepared = adapter.prepare(
            "find_replace",
            {
                "scope": "selection",
                "find": "foo",
                "replace": "bar",
                "match_case": True,
            },
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        assert hwp.GetTextFile("UNICODE", "") == "bar one bar\r\nbar two"
        report["checks"].append({
            "name": "selection_find_replace", "passed": True
        })

        progress("document replace")
        prepared = adapter.prepare(
            "find_replace",
            {
                "scope": "document",
                "find": "bar",
                "replace": "baz",
                "match_case": True,
            },
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        assert hwp.GetTextFile("UNICODE", "") == "baz one baz\r\nbaz two"
        report["checks"].append({
            "name": "document_find_replace", "passed": True
        })

        progress("pdf safety block")
        try:
            adapter.prepare("save_as", {"path": pdf_path, "format": "PDF"})
        except AppActionBlocked:
            pass
        else:
            raise AssertionError("Unstable PDF Automation was not blocked")
        report["checks"].append({
            "name": "unstable_pdf_export_safely_blocked", "passed": True
        })

        progress("stale context")
        stale = adapter.prepare("insert_text", {"text": "stale"})
        action = hwp.CreateAction("InsertText")
        parameter_set = action.CreateSet()
        parameter_set.SetItem("Text", "changed-after-prepare")
        action.Execute(parameter_set)
        try:
            adapter.execute(stale)
        except AppActionContextChanged:
            pass
        else:
            raise AssertionError("Stale HWP context was not blocked")
        assert "stale" not in hwp.GetTextFile("UNICODE", "")
        report["checks"].append({
            "name": "stale_context_blocked", "passed": True
        })

        report["success"] = True
        progress("complete")
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if owned and hwp is not None:
            try:
                hwp.Clear(1)
            except Exception:
                pass
            try:
                hwp.Quit()
            except Exception:
                pass
        shutil.rmtree(temporary_root, ignore_errors=True)
        after_names = rot_names()
        for _ in range(20):
            if after_names == before_names:
                break
            time.sleep(0.1)
            after_names = rot_names()
        report["existing_hwp_objects_preserved"] = after_names == before_names
        report["finished_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        with open(REPORT_PATH, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
