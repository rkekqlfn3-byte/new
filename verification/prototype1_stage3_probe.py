"""Safe live probe for Prototype 1.0 local file intake and session pinning.

The probe never uses a real document.  For an app with no existing user
process, it creates one temporary native document, opens it through the same
FileIntakeManager used by JARVIS, verifies the COM-free session, and closes
only that temporary document.  Apps with existing processes are read-only
discovery checks and are never closed or modified.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import psutil

from engine.edit_mode import EditRequest, EditSessionManager, FileIntakeManager
from engine.edit_mode.native_bridge import (
    NativeDocumentBridge,
    _rot_office_reference,
    _same_path,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage3_report.json"

APP_SPECS = {
    "excel": {"extension": ".xlsx", "processes": ("excel.exe",)},
    "hwp": {"extension": ".hwp", "processes": ("hwp.exe", "hwp64.exe")},
    "word": {"extension": ".docx", "processes": ("winword.exe",)},
    "powerpoint": {"extension": ".pptx", "processes": ("powerpnt.exe",)},
}


def _process_ids(names) -> set[int]:
    expected = {str(name).casefold() for name in names}
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() in expected:
                result.add(int(process.info["pid"]))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return result


def _wait_for_processes(names, expected, timeout=8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _process_ids(names) == set(expected):
            return True
        time.sleep(0.1)
    return _process_ids(names) == set(expected)


def _close(document, *args, **kwargs):
    if document is None:
        return
    try:
        document.Close(*args, **kwargs)
    except Exception:
        pass


def _quit(application):
    if application is None:
        return
    try:
        application.Quit()
    except Exception:
        pass


def _create_office_document(app_type, path):
    import pythoncom
    import win32com.client

    application = document = None
    pythoncom.CoInitialize()
    try:
        if app_type == "excel":
            application = win32com.client.DispatchEx("Excel.Application")
            application.Visible = False
            application.DisplayAlerts = False
            document = application.Workbooks.Add()
            document.Worksheets(1).Range("A1").Value2 = "JARVIS_STAGE3"
            document.SaveAs(str(path), FileFormat=51)
            _close(document, SaveChanges=False)
        elif app_type == "word":
            application = win32com.client.DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            document = application.Documents.Add()
            document.Content.Text = "JARVIS_STAGE3"
            document.SaveAs2(str(path), FileFormat=12, AddToRecentFiles=False)
            _close(document, SaveChanges=0)
        elif app_type == "powerpoint":
            application = win32com.client.DispatchEx("PowerPoint.Application")
            application.Visible = -1
            document = application.Presentations.Add(-1)
            slide = document.Slides.Add(1, 12)
            shape = slide.Shapes.AddTextbox(1, 20, 20, 400, 60)
            shape.TextFrame.TextRange.Text = "JARVIS_STAGE3"
            document.SaveAs(str(path), 24)
            shape = None
            slide = None
            _close(document)
        else:
            raise ValueError(app_type)
        document = None
    finally:
        _close(document, SaveChanges=False)
        document = None
        _quit(application)
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _create_hwp_document(path):
    import pythoncom
    import win32com.client

    hwp = None
    pythoncom.CoInitialize()
    try:
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        parameter = hwp.HParameterSet.HInsertText
        hwp.HAction.GetDefault("InsertText", parameter.HSet)
        parameter.Text = "JARVIS_STAGE3"
        hwp.HAction.Execute("InsertText", parameter.HSet)
        if not bool(hwp.SaveAs(str(path), "HWP", "")):
            raise RuntimeError("한글 임시 문서를 저장하지 못했습니다.")
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
            except Exception:
                pass
            _quit(hwp)
        hwp = None
        gc.collect()
        pythoncom.CoUninitialize()


def _create_document(app_type, path):
    if app_type == "hwp":
        _create_hwp_document(path)
    else:
        _create_office_document(app_type, path)


def _close_temporary_office_document(app_type, path):
    import pythoncom
    import win32com.client

    application = document = None
    pythoncom.CoInitialize()
    try:
        progids = {
            "excel": "Excel.Application",
            "word": "Word.Application",
            "powerpoint": "PowerPoint.Application",
        }
        try:
            application = win32com.client.GetActiveObject(progids[app_type])
        except Exception:
            application, document = _rot_office_reference(path)
        if application is None:
            return
        if app_type == "excel":
            collection = application.Workbooks
        elif app_type == "word":
            collection = application.Documents
        else:
            collection = application.Presentations
        if document is None:
            for index in range(1, int(collection.Count) + 1):
                candidate = collection.Item(index)
                if _same_path(candidate.FullName, path):
                    document = candidate
                    break
        if document is not None:
            if app_type == "excel":
                _close(document, SaveChanges=False)
            elif app_type == "word":
                _close(document, SaveChanges=0)
            else:
                _close(document)
            document = None
        if int(collection.Count) == 0:
            _quit(application)
    finally:
        document = None
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _close_temporary_hwp_document(path):
    import pythoncom

    bridge = NativeDocumentBridge()
    pythoncom.CoInitialize()
    try:
        for hwp in bridge._hwp_candidates():
            metadata = bridge._hwp_metadata(hwp)
            if metadata and _same_path(metadata["file_path"], path):
                try:
                    hwp.Clear(1)
                finally:
                    _quit(hwp)
                return
            hwp = None
    finally:
        gc.collect()
        pythoncom.CoUninitialize()


def _close_temporary_document(app_type, path):
    if app_type == "hwp":
        _close_temporary_hwp_document(path)
    else:
        _close_temporary_office_document(app_type, path)


def _probe_app(app_type) -> dict:
    spec = APP_SPECS[app_type]
    baseline = _process_ids(spec["processes"])
    bridge = NativeDocumentBridge()
    if baseline:
        documents = bridge.active_documents(app_type)
        read_only_session_verified = False
        read_only_error = None
        try:
            document = FileIntakeManager(bridge=bridge).connect_active_document(app_type)
            sessions = EditSessionManager()
            session = sessions.connect(document)
            disconnected = sessions.disconnect(session["session_id"])
            read_only_session_verified = disconnected["state"] == "disconnected"
        except Exception as error:
            read_only_error = f"{type(error).__name__}: {error}"
        return {
            "status": "skipped_user_processes_running",
            "baseline_process_count": len(baseline),
            "read_only_active_document_count": len(documents),
            "read_only_session_verified": read_only_session_verified,
            "read_only_error": read_only_error,
            "user_process_protected": True,
        }
    if not bridge.is_available(app_type):
        return {"status": "unavailable", "registered": False}

    with tempfile.TemporaryDirectory(prefix=f"jarvis-stage3-{app_type}-") as temp_dir:
        path = Path(temp_dir) / f"stage3-{uuid.uuid4().hex}{spec['extension']}"
        stage = "create_temporary_document"
        try:
            _create_document(app_type, path)
            if not path.is_file():
                raise RuntimeError("임시 문서가 생성되지 않았습니다.")
            if not _wait_for_processes(spec["processes"], baseline):
                raise RuntimeError("임시 문서 생성 프로세스가 정리되지 않았습니다.")
            stage = "connect_file"
            intake = FileIntakeManager(bridge=bridge, open_timeout=20)
            document = intake.connect_file(str(path))
            stage = "pin_session"
            sessions = EditSessionManager()
            session = sessions.connect(document)
            request = EditRequest(
                text="연결 검증",
                edit_session_id=session["session_id"],
                document_fingerprint=session["document_fingerprint"],
            )
            sessions.validate_request(request)
            json.dumps(session, ensure_ascii=False, allow_nan=False)
            disconnected = sessions.disconnect(session["session_id"])
            return {
                "status": "passed",
                "registered": True,
                "launch_requested": bool(document.get("launch_requested")),
                "exact_path_verified": _same_path(document["file_path"], path),
                "session_ready": session["state"] == "ready",
                "session_json_only": True,
                "disconnect_verified": disconnected["state"] == "disconnected",
                "user_process_protected": True,
            }
        except Exception as error:
            return {
                "status": "failed",
                "stage": stage,
                "error": f"{type(error).__name__}: {error}",
                "user_process_protected": True,
            }
        finally:
            try:
                _close_temporary_document(app_type, str(path))
            except Exception:
                pass
            _wait_for_processes(spec["processes"], baseline)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=("all", *APP_SPECS), default="all")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args(argv)
    selected = tuple(APP_SPECS) if args.app == "all" else (args.app,)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe": "prototype1_stage3_file_intake_session",
        "applications": {app_type: _probe_app(app_type) for app_type in selected},
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    statuses = {item["status"] for item in report["applications"].values()}
    return 1 if "failed" in statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
