"""Read-only live probe for Prototype 1.0 stage 4 document contexts."""

from __future__ import annotations

import argparse
import gc
import json
import tempfile
import uuid
from pathlib import Path

from engine.edit_mode.context import EditContextManager, NativeDocumentContextReader
from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.native_bridge import (
    NativeDocumentBridge,
    _rot_office_reference,
    _same_path,
)
from engine.edit_mode.session import EditSessionManager, document_identity_fingerprint
from verification.prototype1_stage3_probe import (
    APP_SPECS,
    _close_temporary_document,
    _create_document,
    _process_ids,
    _wait_for_processes,
)


APP_TYPES = ("excel", "hwp", "word", "powerpoint")


def _probe_app(bridge, manager, app_type: str) -> dict:
    documents = bridge.active_documents(app_type)
    if len(documents) != 1:
        return {
            "app_type": app_type,
            "status": "skipped",
            "reason": "one_active_saved_document_required",
            "candidate_count": len(documents),
        }
    document = documents[0]
    file_path = str(document["file_path"])
    session = {
        "session_id": f"stage4-probe-{app_type}",
        "app_type": app_type,
        "file_path": file_path,
        "document_name": Path(file_path).name,
        "document_fingerprint": document_identity_fingerprint(file_path, app_type),
    }
    first = manager.capture(session)
    second = manager.capture(session)
    json.dumps(first, allow_nan=False, ensure_ascii=False)
    return {
        "app_type": app_type,
        "status": "passed",
        "json_only": True,
        "same_selection_stable": (
            first["context_fingerprint"] == second["context_fingerprint"]
        ),
        "selection_kind": first["selection_kind"],
        "has_selection_reference": bool(first["selection_reference"]),
        "has_text_preview": bool(first["selected_text_preview"]),
    }


def _office_reference(app_type: str, expected_path: str):
    import win32com.client

    application = None
    document = None
    try:
        application = win32com.client.GetActiveObject(
            {
                "excel": "Excel.Application",
                "word": "Word.Application",
                "powerpoint": "PowerPoint.Application",
            }[app_type]
        )
        document = NativeDocumentBridge._office_document(
            application, app_type, expected_path
        )
    except Exception:
        application = None
        document = None
    if document is None:
        application, document = _rot_office_reference(expected_path)
    if application is None or document is None:
        raise RuntimeError("임시 Office 문서를 다시 찾지 못했습니다.")
    return application, document


def _select_owned_fixture(app_type: str, expected_path: str, variant: int) -> None:
    import pythoncom

    application = document = hwp = slide = shape = text_range = None
    pythoncom.CoInitialize()
    try:
        if app_type == "hwp":
            for candidate in NativeDocumentBridge._hwp_candidates():
                active = candidate.XHwpDocuments.Active_XHwpDocument
                if _same_path(active.FullName, expected_path):
                    hwp = candidate
                    break
            if hwp is None:
                raise RuntimeError("임시 한글 문서를 다시 찾지 못했습니다.")
            hwp.HAction.Run("SelectAll" if variant == 1 else "MoveDocBegin")
            return

        application, document = _office_reference(app_type, expected_path)
        if app_type == "excel":
            sheet = document.Worksheets(1)
            sheet.Activate()
            sheet.Range("B3:F18" if variant == 1 else "G3:G18").Select()
        elif app_type == "word":
            end = min(_integer_or_default(document.Content.End, 1), 8)
            document.Range(0 if variant == 1 else 1, end).Select()
        else:
            application.ActiveWindow.View.GotoSlide(1)
            slide = document.Slides(1)
            shape = slide.Shapes(1)
            if variant == 1:
                shape.Select()
            else:
                text_range = shape.TextFrame.TextRange
                text_range.Select()
    finally:
        text_range = None
        shape = None
        slide = None
        document = None
        application = None
        hwp = None
        gc.collect()
        pythoncom.CoUninitialize()


def _integer_or_default(value, default=0) -> int:
    try:
        return int(value() if callable(value) else value)
    except (TypeError, ValueError):
        return int(default)


class _OwnedWordProvider:
    """Fixture-only provider that keeps the probe-owned Word references."""

    app_type = "word"

    def __init__(self, application, document):
        self.application = application
        self.document = document

    def capture(self, session):
        return NativeDocumentContextReader._capture_word(
            self.application, self.document
        )


def _probe_owned_word_direct(path: Path) -> dict:
    """Verify Word extraction when this PC cannot reopen .docx through the shell."""
    import pythoncom
    import win32com.client

    application = document = provider = None
    sessions = EditSessionManager()
    session = None
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        document = application.Documents.Add()
        document.Content.Text = "JARVIS_STAGE4_WORD_CONTEXT"
        document.SaveAs2(str(path), FileFormat=12, AddToRecentFiles=False)
        document.Activate()
        application.Selection.SetRange(0, 12)
        session = sessions.connect({
            "app_type": "word",
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": _integer_or_default(document.ActiveWindow.Hwnd),
            "selection_reference": "0:12",
            "launch_requested": True,
        })
        provider = _OwnedWordProvider(application, document)
        manager = EditContextManager(providers=(provider,))
        first = manager.capture(session)
        repeated = manager.capture(session)
        application.Selection.SetRange(1, 12)
        changed = manager.capture(session)
        return {
            "app_type": "word",
            "status": "passed",
            "user_process_protected": True,
            "owned_fixture_only": True,
            "same_selection_stable": (
                first["context_fingerprint"] == repeated["context_fingerprint"]
            ),
            "changed_selection_detected": (
                first["context_fingerprint"] != changed["context_fingerprint"]
            ),
            "app_context_verified": (
                first["selection_reference"] == "0:12"
                and bool(first["selected_text_preview"])
            ),
            "json_only": bool(json.dumps(first, ensure_ascii=False)),
            "native_reader_direct": True,
            "shell_rot_rediscovery_verified": False,
            "shell_rot_note": "현재 PC의 임시 .docx 셸 재실행은 3단계에서도 시간 초과",
        }
    except Exception as error:
        return {
            "app_type": "word",
            "status": "failed",
            "stage": "direct_owned_word_reader",
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        provider = None
        if session is not None:
            try:
                sessions.disconnect(session["session_id"])
            except Exception:
                pass
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass
        document = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _probe_owned_fixture(app_type: str) -> dict:
    spec = APP_SPECS[app_type]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {
            "app_type": app_type,
            "status": "skipped_user_processes_running",
            "user_process_protected": True,
        }
    bridge = NativeDocumentBridge()
    if not bridge.is_available(app_type):
        return {"app_type": app_type, "status": "unavailable"}

    sessions = EditSessionManager()
    session = None
    with tempfile.TemporaryDirectory(prefix=f"jarvis-stage4-{app_type}-") as temp_dir:
        path = Path(temp_dir) / f"stage4-{uuid.uuid4().hex}{spec['extension']}"
        if app_type == "word":
            return _probe_owned_word_direct(path)
        stage = "create_owned_fixture"
        try:
            _create_document(app_type, path)
            if not path.is_file():
                raise RuntimeError("4단계 임시 문서가 생성되지 않았습니다.")
            if not _wait_for_processes(spec["processes"], baseline):
                raise RuntimeError("임시 문서 생성 프로세스가 정리되지 않았습니다.")
            stage = "connect_owned_fixture"
            document = FileIntakeManager(bridge=bridge, open_timeout=30).connect_file(
                str(path)
            )
            session = sessions.connect(document)
            manager = EditContextManager()

            stage = "capture_primary_selection"
            _select_owned_fixture(app_type, str(path), 1)
            first = manager.capture(session)
            repeated = manager.capture(session)

            stage = "capture_changed_selection"
            _select_owned_fixture(app_type, str(path), 2)
            changed = manager.capture(session)
            app_expectation = {
                "excel": first["selection_reference"] == "B3:F18",
                "hwp": bool(first["selected_text_preview"]),
                "word": bool(first["selected_text_preview"]),
                "powerpoint": bool(first["target"].get("shape_id")),
            }[app_type]
            return {
                "app_type": app_type,
                "status": "passed",
                "user_process_protected": True,
                "owned_fixture_only": True,
                "same_selection_stable": (
                    first["context_fingerprint"]
                    == repeated["context_fingerprint"]
                ),
                "changed_selection_detected": (
                    first["context_fingerprint"]
                    != changed["context_fingerprint"]
                ),
                "app_context_verified": app_expectation,
                "json_only": bool(json.dumps(first, ensure_ascii=False)),
            }
        except Exception as error:
            return {
                "app_type": app_type,
                "status": "failed",
                "stage": stage,
                "error_type": type(error).__name__,
                "message": str(error),
                "user_process_protected": True,
            }
        finally:
            if session is not None:
                try:
                    sessions.disconnect(session["session_id"])
                except Exception:
                    pass
            try:
                _close_temporary_document(app_type, str(path))
            except Exception:
                pass
            _wait_for_processes(spec["processes"], baseline)


def run_probe(*, owned_fixtures=False) -> dict:
    if owned_fixtures:
        results = [_probe_owned_fixture(app_type) for app_type in APP_TYPES]
        attempted = [
            item for item in results
            if item["status"] not in {"unavailable", "skipped_user_processes_running"}
        ]
        return {
            "probe": "prototype1_stage4_owned_fixture_context",
            "success": all(item["status"] == "passed" for item in attempted),
            "user_documents_modified": False,
            "paths_or_text_reported": False,
            "results": results,
        }

    bridge = NativeDocumentBridge()
    manager = EditContextManager()
    results = []
    for app_type in APP_TYPES:
        try:
            results.append(_probe_app(bridge, manager, app_type))
        except Exception as error:
            results.append({
                "app_type": app_type,
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
            })
    attempted = [item for item in results if item["status"] != "skipped"]
    return {
        "probe": "prototype1_stage4_read_only_context",
        "success": all(item["status"] == "passed" for item in attempted),
        "documents_modified": False,
        "paths_or_text_reported": False,
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owned-fixtures",
        action="store_true",
        help="사용자 앱이 없을 때 JARVIS 소유 임시 문서로 선택 변경까지 검증",
    )
    args = parser.parse_args(argv)
    report = run_probe(owned_fixtures=args.owned_fixtures)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
