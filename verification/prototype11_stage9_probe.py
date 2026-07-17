"""Owned-workbook live verification for Prototype 1.1 Stage 9 Excel VBA."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import queue
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import psutil

from engine.app_actions.excel_vba_adapter import (
    ExcelVbaAdapter,
    VbaTrustAccessBlocked,
    analyze_vba_code,
    vba_trust_status,
)


REPORT_PATH = Path(__file__).with_name("prototype11_stage9_report.json")


def _process_ids():
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                result.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _wait_for_cleanup(baseline, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not (_process_ids() - baseline):
            return True
        time.sleep(0.2)
    return not (_process_ids() - baseline)


def _stop_created_processes(baseline):
    for pid in _process_ids() - baseline:
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(5)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass


def _interface_count(pythoncom):
    getter = getattr(pythoncom, "_GetInterfaceCount", None)
    return int(getter()) if callable(getter) else 0


def _owned_probe():
    import pythoncom
    import win32com.client

    baseline = _process_ids()
    if baseline:
        return {
            "status": "skipped_user_excel_running",
            "user_process_protected": True,
        }
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-stage9-vba-"))
    path = temp_dir / f"stage9-{uuid.uuid4().hex}.xlsm"
    backup_dir = temp_dir / "backups"
    application = workbook = project = component = prepared = None
    stage = "start_excel"
    trust_configuration = vba_trust_status()
    dangerous = analyze_vba_code(
        'Public Sub Danger()\r\nShell("cmd.exe")\r\n'
        'Kill "C:\\temp.txt"\r\nEnd Sub'
    )
    static_analysis_verified = set(dangerous["dangerous_capabilities"]) == {
        "file_delete", "shell"
    }
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = True
        application.DisplayAlerts = False
        workbook = application.Workbooks.Add()
        workbook.SaveAs(str(path), FileFormat=52)

        stage = "access_vba_project"
        project = workbook.VBProject
        component = project.VBComponents.Add(1)
        component.Name = "JarvisStage9"
        original = (
            "Option Explicit\r\n"
            "Public Sub FindLast()\r\n"
            "    Dim lastRow As Long\r\n"
            "    lastRow = Cells(Rows.Count, 1).End(xlUp).Row\r\n"
            "End Sub\r\n"
            "Public Sub Stage9Safe()\r\n"
            "    ThisWorkbook.Worksheets(1).Range(\"A1\").Value = \"stage9-ok\"\r\n"
            "End Sub"
        )
        component.CodeModule.AddFromString(original)
        adapter = ExcelVbaAdapter(
            # Match the production attachment path. Reusing the original
            # DispatchEx proxy after traversing VBProject can leave pywin32
            # with a disconnected server-side proxy on some Office builds.
            application_getter=lambda: win32com.client.GetActiveObject(
                "Excel.Application"
            ),
            process_counter=lambda: 1,
            backup_dir=backup_dir,
        )
        interface_before = _interface_count(pythoncom)

        stage = "prepare_inspect"
        inspect_prepared = adapter.prepare(
            "vba_inspect_project", {"document_path": str(path)}
        )
        stage = "execute_inspect"
        inspected = adapter.execute(inspect_prepared)
        stage = "prepare_read"
        read_prepared = adapter.prepare(
            "vba_read_module",
            {"document_path": str(path), "module_name": "JarvisStage9"},
        )
        stage = "execute_read"
        read = adapter.execute(read_prepared)
        stage = "prepare_analyze"
        analyze_prepared = adapter.prepare(
            "vba_analyze_module",
            {"document_path": str(path), "module_name": "JarvisStage9"},
        )
        stage = "execute_analyze"
        analyzed = adapter.execute(analyze_prepared)

        stage = "prepare_replace"
        prepared = adapter.prepare(
            "vba_replace_module",
            {
                "document_path": str(path),
                "module_name": "JarvisStage9",
                "change_kind": "fix_last_row",
            },
        )
        stage = "execute_replace"
        changed = adapter.execute(prepared)
        stage = "verify_replace"
        live_application = win32com.client.GetActiveObject("Excel.Application")
        live_workbook = live_application.ActiveWorkbook
        live_component = live_workbook.VBProject.VBComponents.Item("JarvisStage9")
        changed_code = str(
            live_component.CodeModule.Lines(
                1, live_component.CodeModule.CountOfLines
            )
        )
        backup_verified = Path(changed["backup_path"]).is_file()

        stage = "execute_restore"
        restored = adapter.undo(prepared)
        stage = "verify_restore"
        live_application = win32com.client.GetActiveObject("Excel.Application")
        live_workbook = live_application.ActiveWorkbook
        live_component = live_workbook.VBProject.VBComponents.Item("JarvisStage9")
        restored_code = str(
            live_component.CodeModule.Lines(
                1, live_component.CodeModule.CountOfLines
            )
        )

        stage = "prepare_run"
        run_prepared = adapter.prepare(
            "vba_run_procedure",
            {
                "document_path": str(path),
                "module_name": "JarvisStage9",
                "procedure_name": "Stage9Safe",
            },
        )
        stage = "execute_run"
        ran = adapter.execute(run_prepared)
        stage = "verify_run_result"
        live_application = win32com.client.GetActiveObject("Excel.Application")
        live_workbook = live_application.ActiveWorkbook
        macro_result_verified = (
            str(live_workbook.Worksheets(1).Range("A1").Value2) == "stage9-ok"
        )

        stage = "danger_scan"
        prepared = run_prepared = adapter = None
        gc.collect()
        interface_growth = max(0, _interface_count(pythoncom) - interface_before)
        procedures = {
            procedure["name"]
            for module in inspected.get("modules", [])
            for procedure in module.get("procedures", [])
        }
        module_names = {
            str(module.get("name") or "")
            for module in inspected.get("modules", [])
        }
        checks = {
            "project_detected": bool(inspected.get("has_vba_project")),
            "module_listed": "JarvisStage9" in module_names,
            "procedures_listed": {"FindLast", "Stage9Safe"}.issubset(procedures),
            "code_read": "Public Sub FindLast" in str(read.get("code") or ""),
            "code_explained": bool(analyzed.get("explanation")),
            "backup_verified": backup_verified,
            "change_verified": bool(changed.get("verified")) and (
                "ActiveSheet.Cells(ActiveSheet.Rows.Count" in changed_code
            ),
            "restore_verified": bool(restored.get("verified")) and restored_code == original,
            "separate_run_verified": (
                bool(ran.get("invocation_completed")) and macro_result_verified
            ),
            "adapter_did_not_claim_business_result": (
                ran.get("business_result_verified") is False
                and ran.get("verification_scope") == "invocation_return"
            ),
            "danger_scan_verified": set(dangerous["dangerous_capabilities"]) == {
                "file_delete", "shell"
            },
            "com_reference_stable": interface_growth <= 32,
        }
        return {
            "status": "passed" if all(checks.values()) else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": checks,
            "com_interface_growth": interface_growth,
            "vba_trust": trust_configuration,
            "security_setting_changed": False,
        }
    except Exception as error:
        message = str(error)
        trust_terms = (
            "programmatic access", "visual basic project", "vbproject",
            "프로그램 방식", "vba 프로젝트", "trusted",
        )
        trust_blocked = (
            isinstance(error, VbaTrustAccessBlocked)
            or stage == "access_vba_project"
            or any(term in message.casefold() for term in trust_terms)
        )
        result = {
            "status": "blocked_vba_trust" if trust_blocked else "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": (
                "Excel이 VBA 프로젝트 개체 모델 접근을 차단했습니다. "
                "사용자 승인 없이 보안 설정을 변경하지 않았습니다."
                if trust_blocked else message
            ),
            "user_process_protected": True,
            "static_analysis_verified": static_analysis_verified,
            "vba_trust": trust_configuration,
            "security_setting_changed": False,
        }
        if trust_blocked:
            result["required_user_action"] = (
                "Excel 파일 → 옵션 → 보안 센터 → 보안 센터 설정 → 매크로 설정에서 "
                "'VBA 프로젝트 개체 모델에 안전하게 액세스'를 직접 허용하고 "
                "Excel을 다시 시작한 뒤 probe를 재실행"
            )
            result["live_checks_pending"] = [
                "project_access",
                "module_backup_modify_verify",
                "restore_original",
                "separate_macro_run",
            ]
        return result
    finally:
        component = project = prepared = live_component = live_workbook = None
        cleanup_application = None
        try:
            cleanup_application = win32com.client.GetActiveObject(
                "Excel.Application"
            )
            cleanup_workbook = cleanup_application.ActiveWorkbook
            if cleanup_workbook is not None:
                cleanup_workbook.Close(SaveChanges=False)
            cleanup_workbook = None
            cleanup_application.Quit()
        except Exception:
            if workbook is not None:
                try:
                    workbook.Close(SaveChanges=False)
                except Exception:
                    pass
            if application is not None:
                try:
                    application.Quit()
                except Exception:
                    pass
        workbook = application = live_application = cleanup_application = None
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_cleanup(baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _worker(output):
    output.put(_owned_probe())


def run_probe(timeout=180):
    baseline = _process_ids()
    if baseline:
        result = {
            "status": "skipped_user_excel_running",
            "user_process_protected": True,
        }
    else:
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=1)
        process = context.Process(target=_worker, args=(output,))
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(5)
            _stop_created_processes(baseline)
            result = {
                "status": "failed",
                "stage": "owned_fixture_timeout",
                "error_type": "TimeoutError",
                "message": "Excel VBA 소유 문서 검증이 시간 안에 끝나지 않았습니다.",
                "user_process_protected": True,
            }
        else:
            try:
                result = output.get(timeout=2)
            except queue.Empty:
                result = {
                    "status": "failed",
                    "stage": "owned_fixture_worker",
                    "message": "Excel VBA 검증 프로세스가 결과 없이 종료됐습니다.",
                    "user_process_protected": True,
                }
    # The worker process owns the last pywin32 proxies. Excel can finish its
    # asynchronous shutdown only after that process has exited.
    _wait_for_cleanup(baseline)
    zombies = _process_ids() - baseline
    cleanup_mode = "graceful"
    if zombies:
        _stop_created_processes(baseline)
        _wait_for_cleanup(baseline, timeout=5)
        cleanup_mode = "forced_owned_instance"
    remaining = _process_ids() - baseline
    result["owned_process_cleanup_verified"] = not remaining
    result["owned_process_cleanup_mode"] = cleanup_mode
    if remaining:
        result["status"] = "failed"
        result.setdefault("stage", "owned_process_cleanup")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe": "prototype11_stage9_excel_vba",
        "success": result["status"] == "passed",
        "user_documents_modified": False,
        "paths_or_code_reported": False,
        "security_setting_changed": False,
        "result": result,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    report = run_probe(args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
