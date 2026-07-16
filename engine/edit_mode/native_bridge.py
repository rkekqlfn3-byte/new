"""Short-lived native Office/HWP discovery used by edit-file intake."""

from __future__ import annotations

import os
import time
from pathlib import Path

from engine.app_actions.com_lifecycle import com_apartment


APP_PROGIDS = {
    "excel": "Excel.Application",
    "hwp": "HWPFrame.HwpObject",
    "word": "Word.Application",
    "powerpoint": "PowerPoint.Application",
}


class NativeBridgeError(RuntimeError):
    pass


def _normalized_path(value) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(text))


def _same_path(left, right) -> bool:
    first = _normalized_path(left)
    second = _normalized_path(right)
    if not first or not second:
        return False
    try:
        return os.path.samefile(first, second)
    except OSError:
        return first == second


def _item(collection, index):
    accessor = getattr(collection, "Item", None)
    return accessor(index) if callable(accessor) else collection.Item(index)


def _com_value(value):
    return value() if callable(value) else value


def _rot_office_reference(expected_path):
    """Return a document moniker and its application from the current ROT."""
    import pythoncom
    import win32com.client

    context = pythoncom.CreateBindCtx(0)
    running_table = pythoncom.GetRunningObjectTable()
    for moniker in running_table.EnumRunning():
        try:
            display_name = str(moniker.GetDisplayName(context, moniker))
            if not _same_path(display_name, expected_path):
                continue
            raw = running_table.GetObject(moniker)
            document = win32com.client.Dispatch(
                raw.QueryInterface(pythoncom.IID_IDispatch)
            )
            application = document.Application
            return application, document
        except Exception:
            continue
    return None, None


class NativeDocumentBridge:
    """Launch and rediscover documents without retaining a COM reference."""

    def __init__(self, startfile=None, com_runtime=None):
        self._startfile = startfile or getattr(os, "startfile", None)
        self._com_runtime = com_runtime

    def is_available(self, app_type: str) -> bool:
        progid = APP_PROGIDS.get(str(app_type or "").casefold())
        if not progid:
            return False
        try:
            import winreg
        except ImportError:
            return False
        views = {0}
        for name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
            views.add(int(getattr(winreg, name, 0)))
        for view in views:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT,
                    rf"{progid}\CLSID",
                    0,
                    winreg.KEY_READ | view,
                ):
                    return True
            except OSError:
                continue
        return False

    def launch_document(self, app_type: str, file_path: str) -> None:
        if not self.is_available(app_type):
            raise NativeBridgeError(f"{app_type} 앱이 설치되어 있지 않습니다.")
        if not callable(self._startfile):
            raise NativeBridgeError("현재 운영체제에서는 네이티브 문서 실행을 지원하지 않습니다.")
        try:
            self._startfile(file_path)
        except OSError as error:
            raise NativeBridgeError("Windows가 문서 연결 앱을 실행하지 못했습니다.") from error

    @staticmethod
    def _office_metadata(app_type, application, document) -> dict:
        full_name = _normalized_path(getattr(document, "FullName", ""))
        name = str(getattr(document, "Name", "") or Path(full_name).name)
        window_handle = 0
        active_container = None
        selection_reference = None

        if app_type == "excel":
            window_handle = int(_com_value(getattr(application, "Hwnd", 0)) or 0)
            try:
                active_container = str(document.ActiveSheet.Name or "") or None
            except Exception:
                active_container = None
            try:
                if _same_path(application.ActiveWorkbook.FullName, full_name):
                    selection = application.Selection
                    try:
                        selection_reference = str(selection.Address(False, False))
                    except Exception:
                        selection_reference = str(selection.Address)
            except Exception:
                selection_reference = None
        elif app_type == "word":
            try:
                window_handle = int(_com_value(document.ActiveWindow.Hwnd) or 0)
            except Exception:
                try:
                    window_handle = int(
                        _com_value(getattr(application, "Hwnd", 0)) or 0
                    )
                except Exception:
                    window_handle = 0
            try:
                if _same_path(application.ActiveDocument.FullName, full_name):
                    selection = application.Selection
                    selection_reference = f"{int(selection.Start)}:{int(selection.End)}"
            except Exception:
                selection_reference = None
        elif app_type == "powerpoint":
            try:
                window_handle = int(
                    _com_value(_item(document.Windows, 1).HWND) or 0
                )
            except Exception:
                try:
                    window_handle = int(
                        _com_value(application.ActiveWindow.HWND) or 0
                    )
                except Exception:
                    window_handle = 0
            try:
                if _same_path(application.ActivePresentation.FullName, full_name):
                    slide = application.ActiveWindow.View.Slide
                    active_container = (
                        f"슬라이드 {int(_com_value(slide.SlideIndex))}"
                    )
                    selection = application.ActiveWindow.Selection
                    selection_reference = (
                        f"selection:{int(_com_value(selection.Type))}"
                    )
            except Exception:
                active_container = active_container or None
                selection_reference = None

        return {
            "app_type": app_type,
            "file_path": full_name,
            "document_name": name,
            "window_handle": window_handle,
            "active_container": active_container,
            "selection_reference": selection_reference,
        }

    @staticmethod
    def _office_document(application, app_type, expected_path=None):
        if app_type == "excel":
            collection = application.Workbooks
            active = getattr(application, "ActiveWorkbook", None)
        elif app_type == "word":
            collection = application.Documents
            active = getattr(application, "ActiveDocument", None)
        else:
            collection = application.Presentations
            active = getattr(application, "ActivePresentation", None)

        if expected_path:
            for index in range(1, int(collection.Count) + 1):
                document = _item(collection, index)
                if _same_path(getattr(document, "FullName", ""), expected_path):
                    return document
            return None
        return active

    def _find_office_document(self, app_type, expected_path=None) -> dict | None:
        application = None
        document = None
        try:
            import win32com.client

            try:
                application = win32com.client.GetActiveObject(APP_PROGIDS[app_type])
                document = self._office_document(application, app_type, expected_path)
            except Exception:
                application = None
                document = None
            if document is None and expected_path:
                application, document = _rot_office_reference(expected_path)
            if document is None:
                return None
            metadata = self._office_metadata(app_type, application, document)
            if not metadata["file_path"]:
                return None
            return metadata
        except Exception:
            return None
        finally:
            document = None
            application = None

    @staticmethod
    def _hwp_candidates():
        import pythoncom
        import win32com.client

        context = pythoncom.CreateBindCtx(0)
        running_table = pythoncom.GetRunningObjectTable()
        for moniker in running_table.EnumRunning():
            try:
                name = moniker.GetDisplayName(context, moniker)
                if not str(name).startswith("!HwpObject."):
                    continue
                raw = running_table.GetObject(moniker)
                hwp = win32com.client.gencache.EnsureDispatch(
                    raw.QueryInterface(pythoncom.IID_IDispatch)
                )
                if int(hwp.XHwpDocuments.Count) > 0:
                    yield hwp
            except Exception:
                continue

    @staticmethod
    def _hwp_metadata(hwp) -> dict | None:
        try:
            document = hwp.XHwpDocuments.Active_XHwpDocument
            window = hwp.XHwpWindows.Active_XHwpWindow
            full_name = _normalized_path(document.FullName)
            if not full_name:
                return None
            selected = tuple(hwp.GetSelectedPos())
            has_selection = bool(selected[0]) if selected else False
            position = ":".join(str(int(value)) for value in hwp.GetPos())
            return {
                "app_type": "hwp",
                "file_path": full_name,
                "document_name": Path(full_name).name,
                "window_handle": int(window.WindowHandle or 0),
                "active_container": None,
                "selection_reference": (
                    "selected:" + position if has_selection else "cursor:" + position
                ),
            }
        except Exception:
            return None

    def _find_hwp_documents(self, expected_path=None) -> list[dict]:
        documents = []
        try:
            for hwp in self._hwp_candidates():
                metadata = self._hwp_metadata(hwp)
                if metadata and (
                    not expected_path
                    or _same_path(metadata["file_path"], expected_path)
                ):
                    documents.append(metadata)
                hwp = None
        except Exception:
            return []
        return documents

    def find_document(self, app_type: str, expected_path=None) -> dict | None:
        normalized = str(app_type or "").strip().casefold()
        with com_apartment(self._com_runtime):
            if normalized == "hwp":
                matches = self._find_hwp_documents(expected_path)
                return matches[0] if len(matches) == 1 else None
            if normalized in {"excel", "word", "powerpoint"}:
                return self._find_office_document(normalized, expected_path)
        return None

    def wait_for_document(
        self,
        app_type: str,
        expected_path: str,
        *,
        timeout: float = 15.0,
        interval: float = 0.2,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.1, min(float(timeout), 30.0))
        while time.monotonic() < deadline:
            found = self.find_document(app_type, expected_path)
            if found is not None:
                return found
            try:
                import pythoncom

                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            time.sleep(max(0.05, min(float(interval), 0.5)))
        return None

    def active_documents(self, app_type: str | None = None) -> list[dict]:
        requested = str(app_type or "").strip().casefold()
        app_types = (requested,) if requested else tuple(APP_PROGIDS)
        results = []
        for candidate in app_types:
            if candidate not in APP_PROGIDS:
                continue
            if candidate == "hwp":
                with com_apartment(self._com_runtime):
                    results.extend(self._find_hwp_documents())
            else:
                found = self.find_document(candidate)
                if found:
                    results.append(found)
        unique = {}
        for item in results:
            unique[(item["app_type"], item["file_path"])] = item
        return list(unique.values())

    def resolve_explorer_selection(self, file_name: str, file_size=None) -> str | None:
        """Recover a dropped Explorer path only when one selected file matches."""
        expected_name = str(file_name or "").strip().casefold()
        if not expected_name:
            return None
        matches = set()
        shell = None
        try:
            with com_apartment(self._com_runtime):
                import win32com.client

                shell = win32com.client.Dispatch("Shell.Application")
                for window in list(shell.Windows()):
                    try:
                        selected = window.Document.SelectedItems()
                        for index in range(int(selected.Count)):
                            candidate = str(selected.Item(index).Path or "")
                            path = Path(candidate)
                            if not path.is_file() or path.name.casefold() != expected_name:
                                continue
                            if file_size is not None and path.stat().st_size != int(file_size):
                                continue
                            matches.add(_normalized_path(path))
                    except Exception:
                        continue
        except Exception:
            return None
        finally:
            shell = None
        return next(iter(matches)) if len(matches) == 1 else None
