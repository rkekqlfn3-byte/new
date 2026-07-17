"""Owned-fixture live probe for Prototype 1.0 Stage 5 Excel/HWP editing."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import os
import queue
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import psutil

from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.hwp_adapter import HwpAdapter
from engine.edit_mode import EditContextManager, EditModeController, EditSessionManager
from engine.edit_mode.context import NativeDocumentContextReader
from engine.edit_mode.native_bridge import NativeDocumentBridge
from engine.parser import CommandParser
from verification.prototype1_stage3_probe import (
    APP_SPECS,
    _process_ids,
    _wait_for_processes,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage5_report.json"


def _trace(stage):
    if os.environ.get("JARVIS_STAGE5_TRACE") == "1":
        print(f"[stage5-probe] {stage}", flush=True)


class _NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class _Registry:
    def __init__(self, app_type, adapter):
        self.app_type = app_type
        self.adapter = adapter

    def get(self, target):
        if str(target).casefold() != self.app_type:
            raise RuntimeError("다른 앱 어댑터 요청을 차단했습니다.")
        return self.adapter


class _OwnedExcelProvider:
    app_type = "excel"

    def __init__(self, application, workbook):
        self.application = application
        self.workbook = workbook

    def capture(self, session):
        return NativeDocumentContextReader._capture_excel(
            self.application,
            self.workbook,
        )


class _OwnedHwpProvider:
    app_type = "hwp"

    def __init__(self, hwp, file_path=None):
        self.hwp = hwp
        self.file_path = str(Path(file_path).resolve()) if file_path else None

    def capture(self, session):
        document = self.hwp.XHwpDocuments.Active_XHwpDocument
        selected = tuple(self.hwp.GetSelectedPos())
        has_selection = bool(selected[0]) if selected else False
        coordinates = [int(value) for value in selected[1:7]] if len(selected) >= 7 else []
        position = [int(value) for value in self.hwp.GetPos()]
        selected_text = (
            str(self.hwp.GetTextFile("UNICODE", "saveblock"))
            if has_selection
            else ""
        )
        reference_values = coordinates if has_selection else position
        document_path = self.file_path or str(document.FullName)
        return {
            "app_type": "hwp",
            "file_path": document_path,
            "document_name": Path(document_path).name,
            "active_container": None,
            "selection_reference": (
                ("selected:" if has_selection else "cursor:")
                + ":".join(str(value) for value in reference_values)
            ),
            "selection_kind": "text" if has_selection else "cursor",
            "target": {"coordinates": coordinates, "position": position},
            "selected_text": selected_text,
            "cursor_reference": ":".join(str(value) for value in position),
            "read_only": int(document.EditMode) == 0,
            "modified": bool(self.hwp.IsModified),
        }


def _session_parser(app_type, path, context_manager, native_adapter, window_handle=0):
    sessions = EditSessionManager()
    session = sessions.connect(
        {
            "app_type": app_type,
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": int(window_handle or 0),
            "launch_requested": True,
        }
    )
    controller = EditModeController(
        session_manager=sessions,
        context_manager=context_manager,
        native_action_registry=_Registry(app_type, native_adapter),
        layout_manager=_NoLayout(),
    )
    return session, controller, CommandParser(edit_mode_controller=controller)


def _edit_context(controller, session, request_id):
    context = controller.context(session["session_id"])
    return {
        "edit_session_id": session["session_id"],
        "document_fingerprint": session["document_fingerprint"],
        "context_fingerprint": context["context_fingerprint"],
        "request_id": request_id,
    }


def _approve(parser, controller, session, command, request_id):
    preview = parser.execute_command_result(
        command,
        mode="edit",
        session_id="stage5-probe",
        edit_context=_edit_context(controller, session, request_id),
    )
    if preview.get("status") != "confirmation_required":
        raise RuntimeError(f"편집 미리보기가 생성되지 않았습니다: {preview.get('status')}")
    confirmation = preview["data"]["confirmation"]
    result = parser.resolve_pending_confirmation(
        "stage5-probe",
        confirmation_id=confirmation["confirmation_id"],
        option_id="apply",
    )
    if not result.get("success") or not result.get("verified"):
        raise RuntimeError(f"승인된 편집 검증에 실패했습니다: {result.get('status')}")
    return result


def _probe_excel() -> dict:
    import pythoncom
    import win32com.client

    spec = APP_SPECS["excel"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("excel"):
        return {"status": "unavailable"}

    application = workbook = sheet = provider = controller = parser = None
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage5-excel-")
    path = Path(temp_dir) / f"stage5-{uuid.uuid4().hex}.xlsx"
    stage = "start_excel"
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Range("A1:B3").Value2 = (
            ("항목", "값"),
            ("첫째", 10),
            ("둘째", 20),
        )
        workbook.SaveAs(str(path), FileFormat=51)
        sheet.Range("C1").Select()

        stage = "prepare_controller"
        provider = _OwnedExcelProvider(application, workbook)
        manager = EditContextManager(providers=(provider,))
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
        )
        session, controller, parser = _session_parser(
            "excel",
            path,
            manager,
            adapter,
            window_handle=int(application.Hwnd or 0),
        )
        stage = "approved_write"
        write = _approve(
            parser, controller, session, "42 입력해줘", "stage5-excel-write"
        )
        write_verified = sheet.Range("C1").Value2 == 42

        stage = "approved_row_insert"
        sheet.Range("A2").Select()
        insert = _approve(
            parser, controller, session, "행 추가", "stage5-excel-row"
        )
        inserted_blank = all(
            sheet.Cells(2, column).Value2 in {None, ""}
            for column in range(1, 4)
        )
        shifted_verified = (
            sheet.Range("A3").Value2 == "첫째"
            and sheet.Range("B3").Value2 == 10
            and sheet.Range("A4").Value2 == "둘째"
            and sheet.Range("B4").Value2 == 20
        )
        return {
            "status": "passed" if all(
                (write_verified, inserted_blank, shifted_verified)
            ) else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "preview_approval_verified": True,
            "write_readback_verified": write_verified and write["verified"],
            "row_insert_readback_verified": (
                inserted_blank and shifted_verified and insert["verified"]
            ),
            "session_returned_ready": (
                controller.status()["session"]["state"] == "ready"
            ),
        }
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        parser = None
        controller = None
        provider = None
        sheet = None
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        workbook = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _probe_hwp() -> dict:
    import pythoncom
    import win32com.client

    spec = APP_SPECS["hwp"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("hwp"):
        return {"status": "unavailable"}

    hwp = provider = controller = parser = None
    owned_pid = None
    original = "5단계 편집 검증을 위한 선택 문장입니다."
    replacement = "5단계 승인 편집이 검증되었습니다."
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage5-hwp-")
    path = Path(temp_dir) / f"stage5-{uuid.uuid4().hex}.hwp"
    stage = "start_hwp"
    pythoncom.CoInitialize()
    try:
        _trace("hwp.dispatch")
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        created = _process_ids(spec["processes"]) - baseline
        if len(created) == 1:
            owned_pid = next(iter(created))
        _trace("hwp.insert_fixture")
        parameter = hwp.HParameterSet.HInsertText
        hwp.HAction.GetDefault("InsertText", parameter.HSet)
        parameter.Text = original
        hwp.HAction.Execute("InsertText", parameter.HSet)
        _trace("hwp.save_fixture")
        if not bool(hwp.SaveAs(str(path), "HWP", "")):
            raise RuntimeError("한글 임시 문서를 저장하지 못했습니다.")
        _trace("hwp.select_all")
        hwp.HAction.Run("SelectAll")

        stage = "prepare_controller"
        _trace(stage)
        provider = _OwnedHwpProvider(hwp)
        manager = EditContextManager(providers=(provider,))
        adapter = HwpAdapter(object_getter=lambda: hwp, require_visible=False)
        window = hwp.XHwpWindows.Active_XHwpWindow
        session, controller, parser = _session_parser(
            "hwp",
            path,
            manager,
            adapter,
            window_handle=int(window.WindowHandle or 0),
        )
        stage = "approved_selection_replace"
        _trace(stage)
        result = _approve(
            parser,
            controller,
            session,
            f'선택 문장을 "{replacement}"으로 바꿔줘',
            "stage5-hwp-replace",
        )
        _trace("hwp.approved")
        text = str(hwp.GetTextFile("UNICODE", "") or "")
        verified = replacement in text and original not in text
        _trace("hwp.status")
        return {
            "status": "passed" if verified else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "preview_approval_verified": True,
            "selection_replace_readback_verified": verified and result["verified"],
            "session_returned_ready": (
                controller.status()["session"]["state"] == "ready"
            ),
        }
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        _trace("hwp.cleanup")
        parser = None
        controller = None
        provider = None
        hwp = None
        gc.collect()
        pythoncom.CoUninitialize()
        if owned_pid is not None:
            try:
                process = psutil.Process(owned_pid)
                process.terminate()
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


PROBES = {"excel": _probe_excel, "hwp": _probe_hwp}


def _probe_worker(app_type, output):
    try:
        output.put(PROBES[app_type]())
    except BaseException as error:
        output.put(
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
                "user_process_protected": True,
            }
        )


def _stop_created_processes(process_names, baseline):
    for process_id in _process_ids(process_names) - baseline:
        try:
            process = psutil.Process(process_id)
            process.terminate()
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _run_isolated(app_type, timeout=40):
    spec = APP_SPECS[app_type]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {
            "status": "skipped_user_processes_running",
            "user_process_protected": True,
        }
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=_probe_worker, args=(app_type, output))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        _stop_created_processes(spec["processes"], baseline)
        return {
            "status": "failed",
            "stage": "owned_fixture_timeout",
            "error_type": "TimeoutError",
            "message": f"{app_type} 소유 문서 검증이 {timeout}초 안에 응답하지 않았습니다.",
            "user_process_protected": True,
            "owned_process_cleanup_verified": not (
                _process_ids(spec["processes"]) - baseline
            ),
        }
    try:
        return output.get(timeout=2)
    except queue.Empty:
        _stop_created_processes(spec["processes"], baseline)
        return {
            "status": "failed",
            "stage": "owned_fixture_worker",
            "error_type": "RuntimeError",
            "message": "소유 문서 검증 프로세스가 결과 없이 종료되었습니다.",
            "user_process_protected": True,
        }


def run_probe(apps=("excel", "hwp")) -> dict:
    results = {app: _run_isolated(app) for app in apps}
    attempted = [
        value
        for value in results.values()
        if value["status"] not in {"unavailable", "skipped_user_processes_running"}
    ]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe": "prototype1_stage5_owned_fixture_editing",
        "success": all(value["status"] == "passed" for value in attempted),
        "user_documents_modified": False,
        "paths_or_text_reported": False,
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apps",
        nargs="+",
        choices=tuple(PROBES),
        default=tuple(PROBES),
    )
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_probe(tuple(dict.fromkeys(args.apps)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
