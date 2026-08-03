"""Short-lived native Office/HWP discovery used by edit-file intake."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from pathlib import Path

from engine.app_actions.com_lifecycle import com_apartment

APP_PROGIDS = {
    "excel": "Excel.Application",
    "hwp": "HWPFrame.HwpObject",
    "word": "Word.Application",
    "powerpoint": "PowerPoint.Application",
}
OFFICE_DOCUMENT_EXTENSIONS = {
    "excel": frozenset({".xlsx", ".xlsm", ".xlsb", ".xls"}),
    "word": frozenset({".docx", ".docm", ".doc"}),
    "powerpoint": frozenset({".pptx", ".pptm", ".ppt"}),
}

OFFICE_BUSY_HRESULTS = frozenset({
    -2147418111,  # RPC_E_CALL_REJECTED
    -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
})
_RUNTIME_WINDOW_PROPERTY = f"JARVIS_EDIT_DOCUMENT_{os.getpid()}"


def _set_window_property(window_handle: int, name: str, value: int) -> bool:
    import ctypes
    from ctypes import wintypes

    setter = ctypes.windll.user32.SetPropW
    setter.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE)
    setter.restype = wintypes.BOOL
    return bool(
        setter(
            int(window_handle),
            str(name),
            ctypes.c_void_p(int(value)),
        )
    )


def _get_window_property(window_handle: int, name: str) -> int:
    import ctypes
    from ctypes import wintypes

    getter = ctypes.windll.user32.GetPropW
    getter.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    getter.restype = wintypes.HANDLE
    return int(getter(int(window_handle), str(name)) or 0)


def _remove_window_property(window_handle: int, name: str) -> None:
    import ctypes
    from ctypes import wintypes

    remover = ctypes.windll.user32.RemovePropW
    remover.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    remover.restype = wintypes.HANDLE
    remover(int(window_handle), str(name))


class NativeBridgeError(RuntimeError):
    pass


class NativeOfficeBusy(NativeBridgeError):
    """Office is alive but temporarily rejects external automation."""

    error_type = "environment_error"
    status = "busy"
    retryable = True


def _com_error_code(error):
    """Return a nested COM HRESULT without depending on pywin32 types."""
    pending = [error]
    visited = set()
    first_code = None
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        hresult = getattr(current, "hresult", None)
        if isinstance(hresult, int):
            if hresult in OFFICE_BUSY_HRESULTS:
                return hresult
            first_code = first_code if first_code is not None else hresult
        values = (
            getattr(current, "args", ())
            if isinstance(current, BaseException)
            else current
        )
        if isinstance(values, dict):
            values = tuple(values.values())
        elif not isinstance(values, (tuple, list)):
            values = ()
        for value in values:
            if isinstance(value, int):
                if value in OFFICE_BUSY_HRESULTS:
                    return value
                first_code = first_code if first_code is not None else value
            elif isinstance(value, (BaseException, tuple, list, dict)):
                pending.append(value)
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
    return first_code


def _is_office_busy_error(error) -> bool:
    # pywin32 can expose RPC_E_CALL_REJECTED as an AttributeError while it
    # attempts a late-bound property lookup on a busy Office application.
    return (
        _com_error_code(error) in OFFICE_BUSY_HRESULTS
        or isinstance(error, AttributeError)
    )


def _office_busy(app_type: str, error=None) -> NativeOfficeBusy:
    labels = {
        "excel": "Excel",
        "word": "Word",
        "powerpoint": "PowerPoint",
    }
    message = (
        f"{labels.get(str(app_type).casefold(), 'Office')}이 셀·문서 입력 중이거나 "
        "대화상자를 처리 중이라 연결 요청을 잠시 거부했습니다. "
        "입력을 마친 뒤 다시 시도해주세요."
    )
    busy = NativeOfficeBusy(message)
    if error is not None:
        busy.__cause__ = error
    return busy


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


def _deduplicate_active_documents(items, *, app_type=None, ranks=None):
    """Keep the frontmost live instance for each logical document identity."""
    unique = {}
    for raw in items:
        item = dict(raw or {})
        handle = int(item.get("window_handle") or 0)
        if ranks is not None:
            item["window_rank"] = ranks.get(handle)
        path = str(item.get("file_path") or "").strip()
        runtime_id = str(item.get("runtime_document_id") or "").strip().upper()
        name = str(item.get("document_name") or "").strip().casefold()
        identity = (
            f"file:{_normalized_path(path).casefold()}"
            if path
            else f"runtime:{handle}:{runtime_id or name}"
        )
        key = (
            str(item.get("app_type") or app_type or "").casefold(),
            identity,
        )
        existing = unique.get(key)
        if existing is None:
            unique[key] = item
            continue

        existing_rank = existing.get("window_rank")
        candidate_rank = item.get("window_rank")
        existing_order = (
            existing_rank if isinstance(existing_rank, int) else 1_000_000,
            int(existing.get("window_handle") or 0) <= 0,
        )
        candidate_order = (
            candidate_rank if isinstance(candidate_rank, int) else 1_000_000,
            handle <= 0,
        )
        if candidate_order < existing_order:
            unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (
            item.get("window_rank") is None,
            item.get("window_rank")
            if item.get("window_rank") is not None
            else 1_000_000,
        ),
    )


def _item(collection, index):
    accessor = getattr(collection, "Item", None)
    return accessor(index) if callable(accessor) else collection.Item(index)


def _com_value(value):
    return value() if callable(value) else value


def _powerpoint_visible_window_handle(document) -> int:
    """Resolve one visible PowerPoint frame only by an exact document title token."""
    try:
        import win32gui

        full_name = str(getattr(document, "FullName", "") or "")
        name = str(getattr(document, "Name", "") or "")
        stem = Path(full_name or name).stem
        tokens = {
            token.strip().casefold()
            for token in (name, stem)
            if str(token or "").strip()
        }
        if not tokens:
            return 0
        matches = []

        def callback(hwnd, _):
            try:
                if (
                    not win32gui.IsWindowVisible(hwnd)
                    or str(win32gui.GetClassName(hwnd) or "") != "PPTFrameClass"
                ):
                    return
                title = str(win32gui.GetWindowText(hwnd) or "").strip().casefold()
                if any(
                    title == token
                    or re.match(
                        rf"^{re.escape(token)}(?=\s*[-–—\[(])",
                        title,
                    )
                    for token in tokens
                ):
                    matches.append(int(hwnd))
            except Exception:
                return

        win32gui.EnumWindows(callback, None)
        unique = tuple(dict.fromkeys(matches))
        return unique[0] if len(unique) == 1 else 0
    except Exception:
        return 0


def bind_excel_runtime_window(window_handle: int) -> str:
    """Tag one live Excel window with a process-local non-document token."""
    import win32gui

    handle = int(window_handle or 0)
    if not handle or not win32gui.IsWindow(handle):
        return ""
    token = secrets.randbits(62) or 1
    if not _set_window_property(handle, _RUNTIME_WINDOW_PROPERTY, token):
        return ""
    return f"JARVIS-WINDOW:{os.getpid()}:{token:X}"


def verify_excel_runtime_window(window_handle: int, runtime_document_id: str) -> bool:
    match = re.fullmatch(
        r"JARVIS-WINDOW:(\d+):([0-9A-F]+)",
        str(runtime_document_id or "").strip().upper(),
    )
    handle = int(window_handle or 0)
    if match is None or int(match.group(1)) != os.getpid() or not handle:
        return False
    try:
        return _get_window_property(handle, _RUNTIME_WINDOW_PROPERTY) == int(
            match.group(2),
            16,
        )
    except Exception:
        return False


def release_excel_runtime_window(window_handle: int, runtime_document_id: str) -> None:
    if not verify_excel_runtime_window(window_handle, runtime_document_id):
        return
    try:
        _remove_window_property(int(window_handle), _RUNTIME_WINDOW_PROPERTY)
    except Exception:
        pass


def _com_object_token(value) -> str:
    """Return one process-local opaque COM identity token when available."""
    unknown = None
    try:
        import pythoncom

        dispatch = getattr(value, "_oleobj_", None)
        if dispatch is None:
            return ""
        unknown = dispatch.QueryInterface(pythoncom.IID_IUnknown)
        match = re.search(r"obj at (0x[0-9A-Fa-f]+)", repr(unknown))
        if match is None:
            return ""
        material = f"{os.getpid()}:{match.group(1).casefold()}"
        return hashlib.sha256(material.encode("ascii")).hexdigest().upper()
    except Exception:
        return ""
    finally:
        unknown = None


def _excel_application_from_window(window_handle: int):
    """Resolve the Excel native object model owned by one XLMAIN window."""
    import ctypes
    from ctypes import wintypes

    import comtypes
    import pythoncom
    import win32com.client
    import win32gui

    handle = int(window_handle or 0)
    if not handle or not win32gui.IsWindow(handle):
        return None
    children = []

    def callback(hwnd, _):
        if str(win32gui.GetClassName(hwnd) or "") == "EXCEL7":
            children.append(int(hwnd))

    win32gui.EnumChildWindows(handle, callback, None)
    if not children:
        return None
    pointer = ctypes.c_void_p()
    iid = comtypes.GUID(str(pythoncom.IID_IDispatch))
    result = ctypes.oledll.oleacc.AccessibleObjectFromWindow(
        wintypes.HWND(children[0]),
        ctypes.c_long(-16),  # OBJID_NATIVEOM
        ctypes.byref(iid),
        ctypes.byref(pointer),
    )
    if result != 0 or not pointer.value:
        return None
    dispatch = pythoncom.ObjectFromAddress(
        pointer.value,
        pythoncom.IID_IDispatch,
    )
    native = win32com.client.Dispatch(dispatch)
    return getattr(native, "Application", native)


def _rot_office_reference(expected_path):
    """Return a document moniker and its application from the current ROT."""
    import pythoncom
    import win32com.client

    context = pythoncom.CreateBindCtx(0)
    running_table = pythoncom.GetRunningObjectTable()
    busy_error = None
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
        except Exception as error:
            if _is_office_busy_error(error):
                busy_error = error
            continue
    if busy_error is not None:
        raise _office_busy("office", busy_error)
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

    def launch_visible_document(
        self,
        app_type: str,
        file_path: str,
    ) -> dict | None:
        """Open an exact PowerPoint presentation with a verified visible window."""
        normalized = str(app_type or "").strip().casefold()
        if normalized != "powerpoint":
            self.launch_document(normalized, file_path)
            return None
        if not self.is_available(normalized):
            raise NativeBridgeError("powerpoint 앱이 설치되어 있지 않습니다.")
        application = presentation = window = None
        with com_apartment(self._com_runtime):
            try:
                import win32com.client

                application = win32com.client.DispatchEx(APP_PROGIDS[normalized])
                application.Visible = True
                presentation = application.Presentations.Open(
                    str(file_path),
                    ReadOnly=False,
                    Untitled=False,
                    WithWindow=True,
                )
                metadata = self._office_metadata(
                    normalized,
                    application,
                    presentation,
                )
                if int(metadata.get("window_handle") or 0) <= 0:
                    window = presentation.NewWindow()
                    try:
                        window.Activate()
                    except Exception:
                        pass
                    metadata = self._office_metadata(
                        normalized,
                        application,
                        presentation,
                    )
                if int(metadata.get("window_handle") or 0) <= 0:
                    raise NativeBridgeError(
                        "PowerPoint 문서는 열렸지만 표시 창을 확인하지 못했습니다."
                    )
                return metadata
            except NativeBridgeError:
                if presentation is not None:
                    try:
                        presentation.Close()
                    except Exception:
                        pass
                if application is not None:
                    try:
                        application.Quit()
                    except Exception:
                        pass
                raise
            except Exception as error:
                if presentation is not None:
                    try:
                        presentation.Close()
                    except Exception:
                        pass
                if application is not None:
                    try:
                        application.Quit()
                    except Exception:
                        pass
                raise NativeBridgeError(
                    "PowerPoint가 정확한 프레젠테이션을 표시 창으로 열지 못했습니다."
                ) from error
            finally:
                window = presentation = application = None

    @staticmethod
    def _office_metadata(app_type, application, document) -> dict:
        candidate_path = _normalized_path(getattr(document, "FullName", ""))
        is_saved = bool(
            candidate_path
            and os.path.isfile(candidate_path)
            and Path(candidate_path).suffix.casefold()
            in OFFICE_DOCUMENT_EXTENSIONS.get(app_type, ())
        )
        full_name = candidate_path if is_saved else ""
        name = str(
            getattr(document, "Name", "")
            or (Path(full_name).name if full_name else "")
        )
        window_handle = 0
        active_container = None
        selection_reference = None
        runtime_document_id = ""

        if app_type == "excel":
            try:
                window_handle = int(
                    _com_value(_item(document.Windows, 1).Hwnd) or 0
                )
            except Exception:
                try:
                    window_handle = int(
                        _com_value(application.ActiveWindow.Hwnd) or 0
                    )
                except Exception:
                    window_handle = int(
                        _com_value(getattr(application, "Hwnd", 0)) or 0
                    )
            runtime_document_id = _com_object_token(document)
            try:
                active_container = str(document.ActiveSheet.Name or "") or None
            except Exception:
                active_container = None
            try:
                if _same_path(
                    application.ActiveWorkbook.FullName,
                    candidate_path,
                ):
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
                if _same_path(
                    application.ActiveDocument.FullName,
                    candidate_path,
                ):
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
                    try:
                        # PowerPoint may register the presentation in the ROT
                        # before DocumentWindow/ActiveWindow exposes HWND.
                        # Application.HWND identifies that exact instance's
                        # top-level frame and avoids process-name guessing.
                        window_handle = int(
                            _com_value(getattr(application, "HWND", 0)) or 0
                        )
                    except Exception:
                        window_handle = 0
            if window_handle <= 0:
                # Some Office builds expose a real visible PPTFrameClass but
                # reject both DocumentWindow.HWND and Application.HWND.  The
                # document path is already exact at this point; use a title
                # match only when it identifies exactly one top-level frame.
                window_handle = _powerpoint_visible_window_handle(document)
            try:
                if _same_path(
                    application.ActivePresentation.FullName,
                    candidate_path,
                ):
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
            "is_saved": is_saved,
            "identity_kind": "file" if is_saved else "runtime",
            "runtime_document_id": runtime_document_id,
        }

    @classmethod
    def _rot_active_office_documents(cls, app_type: str) -> list[dict]:
        """Enumerate active saved documents from every Office instance."""
        import pythoncom
        import win32com.client

        extensions = OFFICE_DOCUMENT_EXTENSIONS.get(app_type, ())
        if not extensions:
            return []
        context = pythoncom.CreateBindCtx(0)
        running_table = pythoncom.GetRunningObjectTable()
        results = []
        for moniker in running_table.EnumRunning():
            application = document = active = None
            try:
                display_name = str(moniker.GetDisplayName(context, moniker))
                if Path(display_name).suffix.casefold() not in extensions:
                    continue
                raw = running_table.GetObject(moniker)
                document = win32com.client.Dispatch(
                    raw.QueryInterface(pythoncom.IID_IDispatch)
                )
                application = document.Application
                active = cls._office_document(application, app_type)
                if active is None or not _same_path(
                    getattr(active, "FullName", ""),
                    getattr(document, "FullName", ""),
                ):
                    continue
                metadata = cls._office_metadata(app_type, application, document)
                if metadata.get("is_saved"):
                    results.append(metadata)
            except Exception:
                continue
            finally:
                active = document = application = None
        return results

    @classmethod
    def _excel_window_documents(cls) -> list[dict]:
        """Read the active workbook behind every visible Excel top-level window."""
        try:
            import win32gui

            handles = []

            def callback(hwnd, _):
                if (
                    win32gui.IsWindowVisible(hwnd)
                    and str(win32gui.GetClassName(hwnd) or "") == "XLMAIN"
                ):
                    handles.append(int(hwnd))

            win32gui.EnumWindows(callback, None)
        except Exception:
            return []

        results = []
        for handle in handles:
            application = document = None
            try:
                application = _excel_application_from_window(handle)
                document = getattr(application, "ActiveWorkbook", None)
                if document is None:
                    continue
                metadata = cls._office_metadata(
                    "excel",
                    application,
                    document,
                )
                if int(metadata.get("window_handle") or 0) == handle:
                    results.append(metadata)
            except Exception:
                continue
            finally:
                document = application = None
        return results

    @staticmethod
    def _window_z_order() -> dict[int, int]:
        """Return visible top-level window ranks without changing foreground."""
        try:
            import win32gui

            handles = []

            def callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    handles.append(int(hwnd))

            win32gui.EnumWindows(callback, None)
            return {handle: index for index, handle in enumerate(handles)}
        except Exception:
            return {}

    def _active_office_documents(self, app_type: str) -> list[dict]:
        import win32com.client

        results = []
        busy_error = None
        application = document = None
        try:
            if app_type == "excel":
                results.extend(self._excel_window_documents())
            try:
                application = win32com.client.GetActiveObject(APP_PROGIDS[app_type])
                document = self._office_document(application, app_type)
                if document is not None:
                    results.append(
                        self._office_metadata(app_type, application, document)
                    )
            except Exception as error:
                if _is_office_busy_error(error):
                    busy_error = error
            results.extend(self._rot_active_office_documents(app_type))
        finally:
            document = application = None

        if not results and busy_error is not None:
            raise _office_busy(app_type, busy_error)

        ranks = self._window_z_order()
        return _deduplicate_active_documents(
            results,
            app_type=app_type,
            ranks=ranks,
        )

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
        busy_error = None
        try:
            import win32com.client

            try:
                application = win32com.client.GetActiveObject(APP_PROGIDS[app_type])
                document = self._office_document(application, app_type, expected_path)
            except Exception as error:
                if _is_office_busy_error(error):
                    busy_error = error
                application = None
                document = None
            if document is None and expected_path:
                try:
                    application, document = _rot_office_reference(expected_path)
                except NativeOfficeBusy as error:
                    busy_error = error
            if document is None:
                if busy_error is not None:
                    raise _office_busy(app_type, busy_error)
                return None
            metadata = self._office_metadata(app_type, application, document)
            if not metadata["file_path"]:
                return None
            return metadata
        except NativeOfficeBusy:
            raise
        except Exception as error:
            if _is_office_busy_error(error):
                raise _office_busy(app_type, error) from error
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

    def ensure_visible_document(self, app_type: str, expected_path: str) -> dict | None:
        """Create a visible window for one exact hidden PowerPoint document."""
        normalized = str(app_type or "").strip().casefold()
        if normalized != "powerpoint":
            return self.find_document(normalized, expected_path)
        application = document = window = None
        with com_apartment(self._com_runtime):
            try:
                application, document = _rot_office_reference(expected_path)
                if application is None or document is None:
                    return None
                metadata = self._office_metadata(
                    normalized,
                    application,
                    document,
                )
                if int(metadata.get("window_handle") or 0) > 0:
                    return metadata
                try:
                    application.Visible = True
                except Exception:
                    pass
                try:
                    window = document.NewWindow()
                    try:
                        window.Activate()
                    except Exception:
                        pass
                except Exception:
                    return None
                metadata = self._office_metadata(
                    normalized,
                    application,
                    document,
                )
                return (
                    metadata
                    if int(metadata.get("window_handle") or 0) > 0
                    else None
                )
            except NativeOfficeBusy:
                raise
            except Exception as error:
                if _is_office_busy_error(error):
                    raise _office_busy(normalized, error) from error
                return None
            finally:
                window = document = application = None

    def wait_for_document(
        self,
        app_type: str,
        expected_path: str,
        *,
        timeout: float = 15.0,
        interval: float = 0.2,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.1, min(float(timeout), 30.0))
        last_busy = None
        while time.monotonic() < deadline:
            try:
                found = self.find_document(app_type, expected_path)
            except NativeOfficeBusy as error:
                last_busy = error
            else:
                if found is not None:
                    return found
                # Office answered normally, so a prior transient rejection is
                # no longer the reason the exact document was not found.
                last_busy = None
            try:
                import pythoncom

                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            time.sleep(max(0.05, min(float(interval), 0.5)))
        if last_busy is not None:
            raise last_busy
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
                with com_apartment(self._com_runtime):
                    results.extend(self._active_office_documents(candidate))
        return _deduplicate_active_documents(results)

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


def excel_reference_for_identity(
    *,
    expected_path: str = "",
    window_handle: int = 0,
    runtime_document_id: str = "",
    document_name: str = "",
):
    """Rediscover exactly one active Excel workbook without retaining COM."""
    import win32com.client

    path = str(expected_path or "").strip()
    handle = int(window_handle or 0)
    runtime_id = str(runtime_document_id or "").strip().upper()
    name = str(document_name or "").strip().casefold()
    window_token = runtime_id.startswith("JARVIS-WINDOW:")
    if window_token and not verify_excel_runtime_window(handle, runtime_id):
        return None, None, None
    candidates = []
    busy_error = None

    if handle:
        try:
            candidates.append(_excel_application_from_window(handle))
        except Exception as error:
            if _is_office_busy_error(error):
                busy_error = error
    if path:
        try:
            application, _ = _rot_office_reference(path)
            candidates.append(application)
        except NativeOfficeBusy as error:
            busy_error = error
    try:
        candidates.append(
            win32com.client.GetActiveObject(APP_PROGIDS["excel"])
        )
    except Exception as error:
        if _is_office_busy_error(error):
            busy_error = error

    seen = set()
    for application in candidates:
        document = None
        try:
            if application is None:
                continue
            marker = repr(getattr(application, "_oleobj_", application))
            if marker in seen:
                continue
            seen.add(marker)
            document = getattr(application, "ActiveWorkbook", None)
            if document is None:
                continue
            metadata = NativeDocumentBridge._office_metadata(
                "excel",
                application,
                document,
            )
            if path and not _same_path(metadata.get("file_path"), path):
                continue
            if handle and int(metadata.get("window_handle") or 0) != handle:
                continue
            actual_runtime_id = str(
                metadata.get("runtime_document_id") or ""
            ).strip().upper()
            if runtime_id:
                if window_token:
                    if not metadata.get("is_saved") and name != str(
                        metadata.get("document_name") or ""
                    ).strip().casefold():
                        continue
                else:
                    if actual_runtime_id and actual_runtime_id != runtime_id:
                        continue
                    if not actual_runtime_id and name != str(
                        metadata.get("document_name") or ""
                    ).strip().casefold():
                        continue
            elif not path and name != str(
                metadata.get("document_name") or ""
            ).strip().casefold():
                continue
            return application, document, metadata
        except Exception as error:
            if _is_office_busy_error(error):
                busy_error = error
        finally:
            document = None
    if busy_error is not None:
        raise _office_busy("excel", busy_error)
    return None, None, None
