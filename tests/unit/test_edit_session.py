import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine.edit_mode import (
    EditAppBusy,
    EditAppUnavailable,
    EditDocumentAmbiguous,
    EditDocumentOpenTimeout,
    EditRequest,
    DocumentWindowActivator,
    EditSessionManager,
    EditSessionBusy,
    EditSessionState,
    EditSessionStale,
    FileIntakeManager,
    UnsupportedEditDocument,
    WindowLayoutManager,
    app_type_for_path,
)
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.native_bridge import (
    NativeDocumentBridge,
    NativeOfficeBusy,
    bind_excel_runtime_window,
    release_excel_runtime_window,
    verify_excel_runtime_window,
)
from engine.edit_mode.target_identity import direct_text_selection_anchor
from engine.learning import UserPreferenceLearningManager


class FakeBridge:
    def __init__(self):
        self.available = True
        self.existing = None
        self.waited = None
        self.active = []
        self.drop_path = None
        self.launches = []
        self.find_error = None
        self.wait_error = None
        self.active_error = None

    def is_available(self, app_type):
        return self.available

    def find_document(self, app_type, expected_path=None):
        if self.find_error is not None:
            raise self.find_error
        return dict(self.existing) if self.existing else None

    def launch_document(self, app_type, file_path):
        self.launches.append((app_type, file_path))

    def wait_for_document(self, app_type, expected_path, timeout=15.0):
        if self.wait_error is not None:
            raise self.wait_error
        return dict(self.waited) if self.waited else None

    def active_documents(self, app_type=None):
        if self.active_error is not None:
            raise self.active_error
        return [
            dict(item) for item in self.active
            if not app_type or item.get("app_type") == app_type
        ]

    def resolve_explorer_selection(self, file_name, file_size=None):
        return self.drop_path


class FakeWindowBackend:
    def __init__(self):
        self.rectangles = {10: (0, 0, 10, 10), 20: (0, 0, 10, 10)}
        self.moves = []

    def is_window(self, handle):
        return handle in self.rectangles

    def find_jarvis_window(self, excluded_handle=0):
        return 20

    def work_area(self, handle):
        return (100, 50, 2100, 1050)

    def restore(self, handle):
        return None

    def move(self, handle, rect):
        self.rectangles[handle] = rect
        self.moves.append((handle, rect))

    def rect(self, handle):
        return self.rectangles[handle]


class EditFileIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.file = self.root / "매출현황.xlsx"
        self.file.write_bytes(b"test workbook")
        self.bridge = FakeBridge()
        self.manager = FileIntakeManager(self.bridge, open_timeout=1)

    def tearDown(self):
        self.temp_dir.cleanup()

    def metadata(self, path=None, app_type="excel"):
        target = Path(path or self.file).resolve()
        return {
            "app_type": app_type,
            "file_path": str(target),
            "document_name": target.name,
            "window_handle": 10,
            "active_container": "7월 실적",
            "selection_reference": "B3:F18",
        }

    def test_supported_extensions_map_to_native_apps(self):
        expected = {
            ".xlsx": "excel", ".xlsm": "excel", ".xlsb": "excel", ".xls": "excel",
            ".hwp": "hwp", ".hwpx": "hwp",
            ".docx": "word", ".docm": "word", ".doc": "word",
            ".pptx": "powerpoint", ".pptm": "powerpoint", ".ppt": "powerpoint",
        }
        for extension, app_type in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(app_type, app_type_for_path("sample" + extension))
        with self.assertRaises(UnsupportedEditDocument):
            app_type_for_path("unsafe.exe")

    def test_existing_exact_document_is_reused_without_launch(self):
        self.bridge.existing = self.metadata()
        result = self.manager.connect_file(str(self.file))
        self.assertFalse(result["launch_requested"])
        self.assertEqual([], self.bridge.launches)
        self.assertEqual("B3:F18", result["selection_reference"])

    def test_launch_requires_exact_document_rediscovery(self):
        self.bridge.waited = self.metadata()
        result = self.manager.connect_file(str(self.file))
        self.assertTrue(result["launch_requested"])
        self.assertEqual("excel", self.bridge.launches[0][0])

        other = self.root / "다른문서.xlsx"
        other.write_bytes(b"other")
        self.bridge.waited = self.metadata(other)
        with self.assertRaises(EditDocumentOpenTimeout):
            self.manager.connect_file(str(self.file))

    def test_exact_reopen_launches_once_and_verifies_the_same_path(self):
        self.bridge.waited = self.metadata()

        result = self.manager.reopen_exact_file(str(self.file))

        self.assertTrue(result["launch_requested"])
        self.assertEqual(1, len(self.bridge.launches))
        self.assertEqual("excel", self.bridge.launches[0][0])

        other = self.root / "다른문서.xlsx"
        other.write_bytes(b"other")
        self.bridge.waited = self.metadata(other)
        with self.assertRaises(EditDocumentOpenTimeout):
            self.manager.reopen_exact_file(str(self.file))
        self.assertEqual(2, len(self.bridge.launches))

    def test_exact_reopen_never_launches_when_app_is_missing(self):
        self.bridge.available = False

        with self.assertRaises(EditAppUnavailable):
            self.manager.reopen_exact_file(str(self.file))

        self.assertEqual([], self.bridge.launches)

    def test_exact_reopen_reports_busy_after_one_launch(self):
        self.bridge.wait_error = NativeOfficeBusy("Excel still busy")

        with self.assertRaises(EditAppBusy) as raised:
            self.manager.reopen_exact_file(str(self.file))

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(1, len(self.bridge.launches))

    def test_missing_app_and_open_timeout_are_explicit(self):
        self.bridge.available = False
        with self.assertRaises(EditAppUnavailable):
            self.manager.connect_file(str(self.file))
        self.assertEqual([], self.bridge.launches)

        self.bridge.available = True
        with self.assertRaises(EditDocumentOpenTimeout):
            self.manager.connect_file(str(self.file))

    def test_busy_excel_is_retried_without_duplicate_launch_and_stays_retryable(self):
        self.bridge.find_error = NativeOfficeBusy("Excel busy")
        self.bridge.waited = self.metadata()
        result = self.manager.connect_file(str(self.file))
        self.assertFalse(result["launch_requested"])
        self.assertEqual([], self.bridge.launches)

        self.bridge.waited = None
        self.bridge.wait_error = NativeOfficeBusy("Excel still busy")
        with self.assertRaises(EditAppBusy) as raised:
            self.manager.connect_file(str(self.file))
        self.assertTrue(raised.exception.retryable)
        self.assertEqual("busy", raised.exception.status)
        self.assertIn("입력을 마친 뒤", str(raised.exception))
        self.assertEqual([], self.bridge.launches)

    def test_busy_active_excel_returns_specific_busy_error(self):
        self.bridge.active_error = NativeOfficeBusy("Excel busy")
        with self.assertRaises(EditAppBusy) as raised:
            self.manager.connect_active_document("excel")
        self.assertEqual("busy", raised.exception.status)

    def test_active_document_requires_one_unambiguous_supported_target(self):
        word = self.root / "보고서.docx"
        word.write_bytes(b"word")
        self.bridge.active = [self.metadata(), self.metadata(word, "word")]
        with self.assertRaises(EditDocumentAmbiguous) as raised:
            self.manager.connect_active_document()
        self.assertEqual(2, len(raised.exception.candidates))

        result = self.manager.connect_active_document("word")
        self.assertEqual("word", result["app_type"])

    def test_frontmost_ranked_excel_wins_across_multiple_instances(self):
        other = self.root / "다른문서.xlsx"
        other.write_bytes(b"other")
        behind = self.metadata()
        behind.update({"is_saved": True, "window_rank": 8})
        front = self.metadata(other)
        front.update({"is_saved": True, "window_rank": 2})
        self.bridge.active = [behind, front]

        result = self.manager.connect_active_document("excel")

        self.assertEqual(
            str(other.resolve()).casefold(),
            result["file_path"].casefold(),
        )
        self.assertEqual(2, result["window_rank"])

    def test_frontmost_unsaved_excel_uses_temporary_runtime_identity(self):
        saved = self.metadata()
        saved.update({"is_saved": True, "window_rank": 4})
        self.bridge.active = [
            {
                "app_type": "excel",
                "file_path": "",
                "document_name": "통합 문서1",
                "window_handle": 11,
                "window_rank": 1,
                "is_saved": False,
                "runtime_document_id": "RUNTIME-BOOK-1",
            },
            saved,
        ]

        result = self.manager.connect_active_document("excel")

        self.assertEqual("통합 문서1", result["document_name"])
        self.assertEqual("runtime", result["identity_kind"])
        self.assertEqual("", result["file_path"])
        self.assertEqual("RUNTIME-BOOK-1", result["runtime_document_id"])

    def test_drop_uses_exact_path_hint_or_explorer_selection(self):
        self.bridge.existing = self.metadata()
        direct = self.manager.connect_dropped_document(
            file_name=self.file.name,
            file_size=self.file.stat().st_size,
            path_hint=str(self.file),
        )
        self.assertEqual(self.file.name, direct["document_name"])

        self.bridge.drop_path = str(self.file)
        resolved = self.manager.connect_dropped_document(
            file_name=self.file.name,
            file_size=self.file.stat().st_size,
        )
        self.assertEqual(self.file.name, resolved["document_name"])

    def test_powerpoint_late_bound_hwnd_methods_are_normalized(self):
        window = SimpleNamespace(HWND=lambda: 4321)
        slide = SimpleNamespace(SlideIndex=lambda: 2)
        selection = SimpleNamespace(Type=lambda: 3)
        document = SimpleNamespace(
            FullName=str(self.file),
            Name=self.file.name,
            Windows=SimpleNamespace(Item=lambda index: window),
        )
        application = SimpleNamespace(
            ActivePresentation=document,
            ActiveWindow=SimpleNamespace(
                HWND=lambda: 4321,
                View=SimpleNamespace(Slide=slide),
                Selection=selection,
            ),
        )
        metadata = NativeDocumentBridge._office_metadata(
            "powerpoint", application, document
        )
        self.assertEqual(4321, metadata["window_handle"])
        self.assertEqual("슬라이드 2", metadata["active_container"])
        self.assertEqual("selection:3", metadata["selection_reference"])

    def test_excel_metadata_uses_the_workbook_window_handle(self):
        selection = SimpleNamespace(Address=lambda row, column: "E9")
        window = SimpleNamespace(Hwnd=4321)
        document = SimpleNamespace(
            FullName=str(self.file),
            Name=self.file.name,
            Windows=SimpleNamespace(Item=lambda index: window),
            ActiveSheet=SimpleNamespace(Name="7월 실적"),
        )
        application = SimpleNamespace(
            Hwnd=9999,
            ActiveWindow=SimpleNamespace(Hwnd=8888),
            ActiveWorkbook=document,
            Selection=selection,
        )

        metadata = NativeDocumentBridge._office_metadata(
            "excel", application, document
        )

        self.assertEqual(4321, metadata["window_handle"])
        self.assertTrue(metadata["is_saved"])
        self.assertEqual("E9", metadata["selection_reference"])

    def test_excel_window_enumeration_reads_each_visible_instance(self):
        window = SimpleNamespace(Hwnd=4321)
        document = SimpleNamespace(
            FullName=str(self.file),
            Name=self.file.name,
            Windows=SimpleNamespace(Item=lambda index: window),
            ActiveSheet=SimpleNamespace(Name="Sheet1"),
        )
        application = SimpleNamespace(
            Hwnd=4321,
            ActiveWindow=window,
            ActiveWorkbook=document,
            Selection=SimpleNamespace(Address=lambda row, column: "A1"),
        )

        def enumerate_windows(callback, value):
            callback(4321, value)

        with (
            mock.patch("win32gui.EnumWindows", side_effect=enumerate_windows),
            mock.patch("win32gui.IsWindowVisible", return_value=True),
            mock.patch("win32gui.GetClassName", return_value="XLMAIN"),
            mock.patch(
                "engine.edit_mode.native_bridge._excel_application_from_window",
                return_value=application,
            ),
        ):
            documents = NativeDocumentBridge._excel_window_documents()

        self.assertEqual(1, len(documents))
        self.assertEqual(4321, documents[0]["window_handle"])
        self.assertEqual(str(self.file.resolve()).casefold(), documents[0]["file_path"].casefold())

    def test_runtime_excel_window_token_is_verified_and_released(self):
        properties = {}

        def set_property(handle, name, value):
            properties[(handle, name)] = value
            return True

        def get_property(handle, name):
            return properties.get((handle, name), 0)

        def remove_property(handle, name):
            return properties.pop((handle, name), 0)

        with (
            mock.patch("win32gui.IsWindow", return_value=True),
            mock.patch(
                "engine.edit_mode.native_bridge._set_window_property",
                side_effect=set_property,
            ),
            mock.patch(
                "engine.edit_mode.native_bridge._get_window_property",
                side_effect=get_property,
            ),
            mock.patch(
                "engine.edit_mode.native_bridge._remove_window_property",
                side_effect=remove_property,
            ),
            mock.patch("secrets.randbits", return_value=123456),
        ):
            runtime_id = bind_excel_runtime_window(4321)
            self.assertTrue(verify_excel_runtime_window(4321, runtime_id))
            release_excel_runtime_window(4321, runtime_id)
            self.assertFalse(verify_excel_runtime_window(4321, runtime_id))


class EditSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "보고서.docx"
        self.file.write_bytes(b"document")
        self.manager = EditSessionManager()
        self.session = self.manager.connect({
            "app_type": "word",
            "file_path": str(self.file),
            "document_name": self.file.name,
            "window_handle": 22,
            "selection_reference": "10:20",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_is_json_only_and_request_is_bound_to_identity(self):
        encoded = json.dumps(self.manager.current(), ensure_ascii=False)
        self.assertIn("보고서.docx", encoded)
        request = EditRequest(
            text="이 문단을 줄여줘",
            edit_session_id=self.session["session_id"],
            document_fingerprint=self.session["document_fingerprint"],
        )
        validated = self.manager.validate_request(request)
        self.assertEqual(self.session["session_id"], validated.session_id)

        wrong = EditRequest(
            text="이 문단을 줄여줘",
            edit_session_id="edit-wrong-session",
            document_fingerprint=self.session["document_fingerprint"],
        )
        with self.assertRaises(EditSessionStale):
            self.manager.validate_request(wrong)

    def test_unsaved_excel_session_validates_then_promotes_after_save(self):
        runtime = self.manager.connect({
            "app_type": "excel",
            "file_path": "",
            "document_name": "통합 문서1",
            "window_handle": 77,
            "identity_kind": "runtime",
            "runtime_document_id": "RUNTIME-77",
            "is_saved": False,
        })
        request = EditRequest(
            text="A1에 10 입력해줘",
            edit_session_id=runtime["session_id"],
            document_fingerprint=runtime["document_fingerprint"],
        )
        self.manager.validate_request(request)

        saved = Path(self.temp_dir.name) / "저장됨.xlsx"
        saved.write_bytes(b"workbook")
        promoted = self.manager.promote_runtime_document(
            runtime["session_id"],
            str(saved),
            runtime_document_id="RUNTIME-77",
        )

        self.assertEqual("file", promoted["identity_kind"])
        self.assertTrue(promoted["is_saved"])
        self.assertEqual(str(saved.resolve()).casefold(), promoted["file_path"].casefold())
        self.assertNotEqual(
            runtime["document_fingerprint"],
            promoted["document_fingerprint"],
        )

    def test_disconnect_does_not_close_or_delete_document(self):
        disconnected = self.manager.disconnect(self.session["session_id"])
        self.assertEqual("disconnected", disconnected["state"])
        self.assertTrue(self.file.exists())
        self.assertIsNone(self.manager.current())

    def test_rebind_window_handle_updates_only_window_metadata(self):
        updated = self.manager.rebind_window_handle(
            self.session["session_id"], 777
        )
        self.assertEqual(777, updated["window_handle"])
        self.assertEqual(
            self.session["document_fingerprint"],
            updated["document_fingerprint"],
        )
        self.assertEqual(self.session["file_path"], updated["file_path"])
        self.assertEqual("ready", updated["state"])
        with self.assertRaises(EditSessionStale):
            self.manager.rebind_window_handle("edit-other-session", 88)
        from engine.edit_mode.session import EditSessionError

        with self.assertRaises(EditSessionError):
            self.manager.rebind_window_handle(self.session["session_id"], 0)

    def test_replacement_is_blocked_while_an_edit_is_preparing(self):
        machine = self.manager.state_machine_for(self.session["session_id"])
        machine.transition(EditSessionState.PREPARING, reason="test edit")
        with self.assertRaises(EditSessionBusy):
            self.manager.assert_connectable()


class DirectEditPreferenceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.file = self.root / "직접수정.hwp"
        self.file.write_bytes(b"fixture")
        self.learning_path = self.root / "direct-edit-preferences.json"
        self.learning = UserPreferenceLearningManager(self.learning_path)
        self.sessions = EditSessionManager()
        self.session = self.sessions.connect({
            "app_type": "hwp",
            "file_path": str(self.file),
            "document_name": self.file.name,
            "window_handle": 31,
            "selection_reference": "10:110",
        })
        self.controller = EditModeController(
            session_manager=self.sessions,
            user_learning_manager=self.learning,
            settings_path=self.root / "edit-settings.json",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _remember_verified_edit(self, **changes):
        last_action = {
            "action_id": "a" * 32,
            "request_id": "direct-edit-request",
            "app_type": "hwp",
            "operation": "replace_selection",
            "selection_reference": "10:110",
            "post_selected_text_digest": "B" * 64,
            "post_selected_text_length": 100,
            "post_context_fingerprint": "A" * 64,
        }
        last_action.update(changes)
        self.sessions.record_committed_edit(
            self.session["session_id"],
            last_target={"selection_reference": "10:110"},
            last_action=last_action,
            undo_record=None,
        )
        return last_action

    @staticmethod
    def _changed_context(**changes):
        context = {
            "context_fingerprint": "C" * 64,
            "selection_reference": "10:110",
            "selected_text_digest": "D" * 64,
            "selected_text_length": 60,
        }
        context.update(changes)
        return context

    def test_same_target_direct_shortening_records_one_file_scoped_observation(self):
        self._remember_verified_edit()

        current = self.controller._guard_continuation(
            self.sessions.current(), self._changed_context()
        )

        self.assertIsNone(current["last_action"])
        candidates = self.learning.list_candidates(include_observing=True)
        self.assertEqual(1, len(candidates))
        self.assertEqual("report_tone", candidates[0]["preference"])
        self.assertEqual("concise", candidates[0]["proposed_value"])
        self.assertEqual("file", candidates[0]["scope_kind"])
        self.assertEqual(1, candidates[0]["evidence_count"])
        self.assertIsNone(
            self.learning.resolve("report_tone", file_path=self.file)
        )
        feedback = self.controller.direct_edit_feedback()
        self.assertTrue(feedback["recorded"])
        self.assertEqual("verified_direct_edit", feedback["source"])
        self.assertFalse(feedback["raw_content_stored"])
        self.assertNotIn(
            "selected_text",
            self.learning_path.read_text(encoding="utf-8"),
        )

        # The continuation was consumed, so repeated polling cannot count it twice.
        self.controller._guard_continuation(
            self.sessions.current(), self._changed_context()
        )
        self.assertEqual(
            1,
            self.learning.list_candidates(include_observing=True)[0]["evidence_count"],
        )

    def test_word_changed_end_offset_uses_structural_anchor(self):
        word_file = self.root / "직접수정.docx"
        word_file.write_bytes(b"fixture")
        self.sessions.disconnect(self.session["session_id"])
        self.session = self.sessions.connect({
            "app_type": "word",
            "file_path": str(word_file),
            "document_name": word_file.name,
            "window_handle": 33,
            "selection_reference": "10:110",
        })
        fingerprint = self.session["document_fingerprint"]
        post_context = {
            "app_type": "word",
            "selection_kind": "text",
            "target": {"start": 10, "end": 110},
        }
        self._remember_verified_edit(
            app_type="word",
            selection_reference="10:110",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor(
                "word", post_context
            ),
        )

        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                app_type="word",
                document_fingerprint=fingerprint,
                selection_reference="10:70",
                selection_kind="text",
                target={"start": 10, "end": 70},
            ),
        )

        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual(1, candidate["evidence_count"])
        self.assertEqual("file", candidate["scope_kind"])

    def test_structural_anchor_rejects_different_start_or_document(self):
        fingerprint = self.session["document_fingerprint"]
        expected_context = {
            "app_type": "hwp",
            "selection_kind": "text",
            "target": {"coordinates": [0, 1, 10, 0, 1, 110]},
        }
        action = self._remember_verified_edit(
            selection_reference="selected:0:1:10:0:1:110",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor(
                "hwp", expected_context
            ),
        )
        base = self._changed_context(
            app_type="hwp",
            document_fingerprint=fingerprint,
            selection_reference="selected:0:1:20:0:1:80",
            selection_kind="text",
            target={"coordinates": [0, 1, 20, 0, 1, 80]},
        )
        self.assertFalse(
            self.controller._same_direct_edit_target(
                self.sessions.current(), action, base
            )
        )
        different_document = dict(base)
        different_document.update({
            "selection_reference": "selected:0:1:10:0:1:80",
            "target": {"coordinates": [0, 1, 10, 0, 1, 80]},
            "document_fingerprint": "F" * 64,
        })
        self.assertFalse(
            self.controller._same_direct_edit_target(
                self.sessions.current(), action, different_document
            )
        )

    def test_changed_selection_or_ambiguous_length_is_not_learning_evidence(self):
        cases = (
            {"selection_reference": "200:260"},
            {"selected_text_length": 90},
            {"selected_text_length": 5},
        )
        for index, context_changes in enumerate(cases):
            with self.subTest(context_changes=context_changes):
                action = self._remember_verified_edit(
                    action_id=f"{index + 1:032x}"
                )
                self.controller._guard_continuation(
                    self.sessions.current(),
                    self._changed_context(**context_changes),
                )
                self.assertIsNotNone(action)
        self.assertEqual([], self.learning.list_candidates(include_observing=True))

    def test_three_direct_shortening_observations_still_require_user_approval(self):
        for index in range(3):
            self._remember_verified_edit(action_id=f"{index + 10:032x}")
            self.controller._guard_continuation(
                self.sessions.current(),
                self._changed_context(
                    selected_text_digest=f"{index + 20:064x}".upper()
                ),
            )

        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("candidate", candidate["status"])
        self.assertEqual(3, candidate["evidence_count"])
        self.assertIsNone(
            self.learning.resolve("report_tone", file_path=self.file)
        )
        feedback = self.controller.direct_edit_feedback()
        self.assertTrue(feedback["needs_confirmation"])
        self.assertIn("앞으로도 간결하게", feedback["message"])

    def test_tone_switch_at_same_length_records_the_new_tone(self):
        self._remember_verified_edit(
            post_selected_text_tone="formal",
        )
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                selected_text_length=95,
                selected_text_tone="friendly",
            ),
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("report_tone", candidate["preference"])
        self.assertEqual("friendly", candidate["proposed_value"])
        self.assertEqual("file", candidate["scope_kind"])
        feedback = self.controller.direct_edit_feedback()
        self.assertEqual("tone", feedback["observation_kind"])
        self.assertIn("친근한 문체", feedback["message"])

    def test_clear_shortening_wins_over_a_simultaneous_tone_switch(self):
        self._remember_verified_edit(post_selected_text_tone="formal")
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                selected_text_length=60,
                selected_text_tone="friendly",
            ),
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("concise", candidate["proposed_value"])
        self.assertEqual(
            "shortened",
            self.controller.direct_edit_feedback()["observation_kind"],
        )

    def test_unclear_tone_labels_are_not_learning_evidence(self):
        cases = (
            {"previous": "mixed", "current": "friendly"},
            {"previous": "formal", "current": "mixed"},
            {"previous": "formal", "current": "plain"},
            {"previous": "unknown", "current": "formal"},
        )
        for index, tones in enumerate(cases):
            with self.subTest(tones=tones):
                self._remember_verified_edit(
                    action_id=f"{index + 40:032x}",
                    post_selected_text_tone=tones["previous"],
                )
                self.controller._guard_continuation(
                    self.sessions.current(),
                    self._changed_context(
                        selected_text_length=95,
                        selected_text_tone=tones["current"],
                    ),
                )
        self.assertEqual([], self.learning.list_candidates(include_observing=True))

    def _connect_word_session(self):
        word_file = self.root / "서식관찰.docx"
        word_file.write_bytes(b"fixture")
        self.sessions.disconnect(self.session["session_id"])
        self.session = self.sessions.connect({
            "app_type": "word",
            "file_path": str(word_file),
            "document_name": word_file.name,
            "window_handle": 34,
            "selection_reference": "10:110",
        })
        return word_file

    def _connect_powerpoint_session(self):
        powerpoint_file = self.root / "서식관찰.pptx"
        powerpoint_file.write_bytes(b"fixture")
        self.sessions.disconnect(self.session["session_id"])
        self.session = self.sessions.connect({
            "app_type": "powerpoint",
            "file_path": str(powerpoint_file),
            "document_name": powerpoint_file.name,
            "window_handle": 35,
            "selection_reference": "제목 1",
        })
        return powerpoint_file

    def _remember_powerpoint_shape_edit(self, completed_at=None):
        self._connect_powerpoint_session()
        fingerprint = self.session["document_fingerprint"]
        post_context = {
            "app_type": "powerpoint",
            "selection_kind": "shapes",
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "shape_count": 1,
            },
        }
        self._remember_verified_edit(
            app_type="powerpoint",
            operation="replace_shape_text",
            selection_reference="제목 1",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor(
                "powerpoint", post_context
            ),
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 28.0,
                "alignment": "left",
            },
            completed_at=(
                completed_at
                or datetime.now().astimezone().isoformat(timespec="seconds")
            ),
        )
        return fingerprint

    @staticmethod
    def _powerpoint_shape_context(fingerprint, **changes):
        context = {
            "app_type": "powerpoint",
            "context_fingerprint": "C" * 64,
            "document_fingerprint": fingerprint,
            "selection_reference": "제목 1",
            "selection_kind": "shapes",
            "selected_text_digest": "B" * 64,
            "selected_text_length": 100,
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "shape_count": 1,
                "bold": -1,
                "font_size": 28.0,
                "paragraph_alignment": 1,
            },
        }
        context.update(changes)
        return context

    def test_powerpoint_single_shape_formatting_correction_is_observed(self):
        fingerprint = self._remember_powerpoint_shape_edit()
        self.controller._guard_continuation(
            self.sessions.current(),
            self._powerpoint_shape_context(fingerprint),
        )

        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("emphasis_style", candidate["preference"])
        self.assertEqual("bold", candidate["proposed_value"])
        feedback = self.controller.direct_edit_feedback()
        self.assertEqual("formatting", feedback["observation_kind"])
        self.assertFalse(feedback["raw_content_stored"])

    def test_powerpoint_single_shape_shortening_is_observed(self):
        fingerprint = self._remember_powerpoint_shape_edit()
        self.controller._guard_continuation(
            self.sessions.current(),
            self._powerpoint_shape_context(
                fingerprint,
                selected_text_digest="D" * 64,
                selected_text_length=60,
                target={
                    "slide_id": 256,
                    "shape_id": 7,
                    "shape_count": 1,
                    "bold": 0,
                    "font_size": 28.0,
                    "paragraph_alignment": 1,
                },
            ),
        )

        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("report_tone", candidate["preference"])
        self.assertEqual("concise", candidate["proposed_value"])
        self.assertEqual(
            "shortened",
            self.controller.direct_edit_feedback()["observation_kind"],
        )

    def test_powerpoint_collapsed_cursor_defers_until_same_shape_is_selected(self):
        fingerprint = self._remember_powerpoint_shape_edit()
        cursor_context = self._powerpoint_shape_context(
            fingerprint,
            context_fingerprint="D" * 64,
            selection_kind="text",
            selected_text_digest="E" * 64,
            selected_text_length=0,
            target={
                "slide_id": 256,
                "shape_id": 7,
                "shape_count": 1,
                "text_start": 1,
                "text_end": 1,
            },
        )
        current = self.controller._guard_continuation(
            self.sessions.current(), cursor_context
        )
        self.assertIsNotNone(current["last_action"])
        self.assertEqual(
            [], self.learning.list_candidates(include_observing=True)
        )

        self.controller._guard_continuation(
            self.sessions.current(),
            self._powerpoint_shape_context(fingerprint),
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("bold", candidate["proposed_value"])

    def test_powerpoint_collapsed_cursor_after_five_minutes_is_not_deferred(self):
        fingerprint = self._remember_powerpoint_shape_edit(
            completed_at="2026-07-18T00:00:00+09:00"
        )
        current = self.controller._guard_continuation(
            self.sessions.current(),
            self._powerpoint_shape_context(
                fingerprint,
                context_fingerprint="D" * 64,
                selection_kind="text",
                selected_text_digest="E" * 64,
                selected_text_length=0,
                target={
                    "slide_id": 256,
                    "shape_id": 7,
                    "shape_count": 1,
                    "text_start": 1,
                    "text_end": 1,
                },
            ),
        )
        self.assertIsNone(current["last_action"])
        self.assertEqual(
            [], self.learning.list_candidates(include_observing=True)
        )

    def test_powerpoint_other_or_multiple_shapes_are_not_observed(self):
        cases = (
            {"shape_id": 8, "shape_count": 1},
            {"shape_id": 7, "shape_count": 2},
        )
        for index, shape_target in enumerate(cases):
            with self.subTest(target=shape_target):
                fingerprint = self._remember_powerpoint_shape_edit()
                context = self._powerpoint_shape_context(fingerprint)
                context["target"].update(shape_target)
                context["selection_reference"] = f"제목 {index + 2}"
                self.controller._guard_continuation(
                    self.sessions.current(), context
                )
                self.assertEqual(
                    [], self.learning.list_candidates(include_observing=True)
                )

    def test_single_formatting_change_with_same_text_records_style_evidence(self):
        self._connect_word_session()
        self._remember_verified_edit(
            app_type="word",
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
        )
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                app_type="word",
                selected_text_digest="B" * 64,
                selected_text_length=100,
                selection_kind="text",
                target={
                    "start": 10,
                    "end": 110,
                    "bold": -1,
                    "font_size": 11.0,
                    "paragraph_alignment": 0,
                },
            ),
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("emphasis_style", candidate["preference"])
        self.assertEqual("bold", candidate["proposed_value"])
        self.assertEqual("file", candidate["scope_kind"])
        feedback = self.controller.direct_edit_feedback()
        self.assertEqual("formatting", feedback["observation_kind"])
        self.assertIn("굵게 강조", feedback["message"])
        self.assertFalse(feedback["raw_content_stored"])

    def test_word_collapsed_cursor_and_unchanged_reselection_defer_then_observe(self):
        self._connect_word_session()
        fingerprint = self.session["document_fingerprint"]
        post_context = {
            "app_type": "word",
            "selection_kind": "text",
            "target": {"start": 10, "end": 110},
        }
        self._remember_verified_edit(
            app_type="word",
            selection_reference="10:110",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor(
                "word", post_context
            ),
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
            completed_at=datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        )
        cursor = self._changed_context(
            app_type="word",
            document_fingerprint=fingerprint,
            selection_reference="10:10",
            selection_kind="cursor",
            selected_text_digest="E" * 64,
            selected_text_length=0,
            target={"start": 10, "end": 10},
        )
        current = self.controller._guard_continuation(
            self.sessions.current(), cursor
        )
        self.assertIsNotNone(current["last_action"])

        unchanged = self._changed_context(
            app_type="word",
            document_fingerprint=fingerprint,
            selection_reference="10:110",
            selection_kind="text",
            selected_text_digest="B" * 64,
            selected_text_length=100,
            target={
                "start": 10,
                "end": 110,
                "bold": 0,
                "font_size": 11.0,
                "paragraph_alignment": 0,
            },
        )
        current = self.controller._guard_continuation(
            self.sessions.current(), unchanged
        )
        self.assertIsNotNone(current["last_action"])

        formatted = dict(unchanged)
        formatted["context_fingerprint"] = "F" * 64
        formatted["target"] = {**unchanged["target"], "bold": -1}
        self.controller._guard_continuation(
            self.sessions.current(), formatted
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("emphasis_style", candidate["preference"])
        self.assertEqual("bold", candidate["proposed_value"])

    def test_word_collapsed_cursor_at_another_start_is_not_deferred(self):
        self._connect_word_session()
        fingerprint = self.session["document_fingerprint"]
        self._remember_verified_edit(
            app_type="word",
            selection_reference="10:110",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor("word", {
                "selection_kind": "text",
                "target": {"start": 10, "end": 110},
            }),
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
            completed_at=datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        )
        current = self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                app_type="word",
                document_fingerprint=fingerprint,
                selection_reference="20:20",
                selection_kind="cursor",
                selected_text_length=0,
                target={"start": 20, "end": 20},
            ),
        )
        self.assertIsNone(current["last_action"])
        self.assertEqual(
            [], self.learning.list_candidates(include_observing=True)
        )

    def test_multi_facet_formatting_change_is_not_interpreted(self):
        self._connect_word_session()
        self._remember_verified_edit(
            app_type="word",
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
        )
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(
                app_type="word",
                selected_text_digest="B" * 64,
                selected_text_length=100,
                selection_kind="text",
                target={
                    "start": 10,
                    "end": 110,
                    "bold": -1,
                    "font_size": 16.0,
                    "paragraph_alignment": 0,
                },
            ),
        )
        self.assertEqual([], self.learning.list_candidates(include_observing=True))

    def test_hwp_formatting_only_change_is_not_interpreted(self):
        self._remember_verified_edit(
            post_selection_formatting=None,
        )
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(selected_text_digest="B" * 64),
        )
        self.assertEqual([], self.learning.list_candidates(include_observing=True))

    def test_hwp_reselection_is_deferred_then_single_format_change_is_observed(self):
        fingerprint = self.session["document_fingerprint"]
        anchor_context = {
            "app_type": "hwp",
            "selection_kind": "text",
            "target": {"coordinates": [0, 1, 10, 0, 1, 110]},
        }
        self._remember_verified_edit(
            selection_reference="cursor:0:1:110",
            post_document_fingerprint=fingerprint,
            post_selection_anchor=direct_text_selection_anchor(
                "hwp", anchor_context
            ),
            post_selection_formatting={
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
            completed_at=datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        )
        unchanged = self._changed_context(
            app_type="hwp",
            document_fingerprint=fingerprint,
            selection_reference="selected:0:1:10:0:1:110",
            selection_kind="text",
            selected_text_digest="B" * 64,
            selected_text_length=100,
            target={
                "coordinates": [0, 1, 10, 0, 1, 110],
                "bold": 0,
                "font_size_hu": 1100,
                "paragraph_alignment": 1,
            },
        )
        current = self.controller._guard_continuation(
            self.sessions.current(), unchanged
        )
        self.assertIsNotNone(current["last_action"])
        self.assertEqual(
            [], self.learning.list_candidates(include_observing=True)
        )

        formatted = dict(unchanged)
        formatted["context_fingerprint"] = "E" * 64
        formatted["target"] = {**unchanged["target"], "bold": 1}
        self.controller._guard_continuation(
            self.sessions.current(), formatted
        )
        candidate = self.learning.list_candidates(include_observing=True)[0]
        self.assertEqual("emphasis_style", candidate["preference"])
        self.assertEqual("bold", candidate["proposed_value"])
        self.assertEqual(
            "formatting",
            self.controller.direct_edit_feedback()["observation_kind"],
        )

    def test_three_tone_observations_suggest_the_matching_activation_phrase(self):
        for index in range(3):
            self._remember_verified_edit(
                action_id=f"{index + 60:032x}",
                post_selected_text_tone="formal",
            )
            self.controller._guard_continuation(
                self.sessions.current(),
                self._changed_context(
                    selected_text_digest=f"{index + 70:064x}".upper(),
                    selected_text_length=95,
                    selected_text_tone="friendly",
                ),
            )
        feedback = self.controller.direct_edit_feedback()
        self.assertTrue(feedback["needs_confirmation"])
        self.assertIn("앞으로도 친근하게 해줘", feedback["message"])

    def test_excel_direct_cell_change_is_not_treated_as_writing_style(self):
        excel = self.root / "직접수정.xlsx"
        excel.write_bytes(b"fixture")
        self.sessions.disconnect(self.session["session_id"])
        self.session = self.sessions.connect({
            "app_type": "excel",
            "file_path": str(excel),
            "document_name": excel.name,
            "window_handle": 32,
            "selection_reference": "A1",
        })
        self._remember_verified_edit(
            app_type="excel",
            selection_reference="A1",
        )
        self.controller._guard_continuation(
            self.sessions.current(),
            self._changed_context(selection_reference="A1"),
        )
        self.assertEqual([], self.learning.list_candidates(include_observing=True))


class WindowLayoutManagerTests(unittest.TestCase):
    def test_arranges_once_on_document_monitor_and_then_respects_user_move(self):
        backend = FakeWindowBackend()
        manager = WindowLayoutManager(backend=backend, document_ratio=0.68)
        result = manager.arrange("edit-session-1", 10)
        self.assertTrue(result["success"])
        self.assertEqual((100, 50, 1460, 1050), backend.rect(10))
        self.assertEqual((1460, 50, 2100, 1050), backend.rect(20))
        self.assertEqual(2, len(backend.moves))

        second = manager.arrange("edit-session-1", 10)
        self.assertEqual("already_arranged", second["status"])
        self.assertEqual(2, len(backend.moves))

    def test_layout_is_optional_and_failure_is_non_throwing(self):
        backend = FakeWindowBackend()
        manager = WindowLayoutManager(backend=backend, enabled=False)
        self.assertEqual("disabled", manager.arrange("session", 10)["status"])
        manager.set_enabled(True)
        result = manager.arrange("session", 999)
        self.assertFalse(result["success"])
        self.assertEqual("skipped", result["status"])

    def test_auto_layout_preference_persists_outside_browser_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "edit_mode_settings.json"
            first = EditModeController(settings_path=settings_path)
            first.set_auto_layout(False)
            second = EditModeController(settings_path=settings_path)
            self.assertFalse(second.status()["auto_layout"])


class FakeActivationBackend:
    def __init__(self, valid=True, foreground=20, accepts=True):
        self.valid = valid
        self.foreground = foreground
        self.accepts = accepts
        self.restored = []
        self.requests = []

    def is_window(self, handle):
        return self.valid and handle == 10

    def foreground_root(self):
        return self.foreground

    def restore(self, handle):
        self.restored.append(handle)

    def request_foreground(self, handle):
        self.requests.append(handle)
        if self.accepts:
            self.foreground = handle


class DocumentWindowActivatorTests(unittest.TestCase):
    def test_connected_document_is_restored_and_verified_foreground(self):
        backend = FakeActivationBackend()
        result = DocumentWindowActivator(
            backend=backend,
            attempts=2,
            retry_delay=0,
        ).activate(10)
        self.assertTrue(result["success"])
        self.assertEqual("focused", result["status"])
        self.assertEqual([10], backend.restored)
        self.assertEqual([10], backend.requests)

    def test_activation_failure_is_non_throwing_and_verified(self):
        backend = FakeActivationBackend(accepts=False)
        result = DocumentWindowActivator(
            backend=backend,
            attempts=2,
            retry_delay=0,
        ).activate(10)
        self.assertFalse(result["success"])
        self.assertEqual("focus_rejected", result["status"])
        self.assertEqual([10, 10], backend.requests)


if __name__ == "__main__":
    unittest.main()
