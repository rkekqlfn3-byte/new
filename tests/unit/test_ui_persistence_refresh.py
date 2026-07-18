import unittest
from pathlib import Path


GUI_ROOT = Path(__file__).resolve().parents[2] / "gui"


class PersistedUiRefreshContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        js_root = GUI_ROOT / "js"
        cls.globals_js = (js_root / "globals.js").read_text(encoding="utf-8")
        cls.sessions_js = (js_root / "sessions.js").read_text(encoding="utf-8")
        cls.chat_js = "\n".join((
            (js_root / "chat" / "learning_review.js").read_text(
                encoding="utf-8"
            ),
            (js_root / "chat" / "controller.js").read_text(encoding="utf-8"),
        ))
        cls.ui_js = (js_root / "dom.js").read_text(encoding="utf-8")
        cls.edit_js = (js_root / "edit_mode.js").read_text(encoding="utf-8")

    def test_first_eel_load_has_bounded_retry(self):
        self.assertIn("window.runUiLoadWithRetry", self.globals_js)
        self.assertIn("attempts = 5", self.globals_js)
        self.assertNotIn("setInterval", self.globals_js)

    def test_saved_session_immediately_reloads_sidebar(self):
        save_tail = self.sessions_js.split("await eel.save_chat_session(", 1)[1]
        self.assertIn("await loadSessions();", save_tail[:1000])

    def test_boot_greeting_is_not_saved_as_a_new_conversation(self):
        save_function = self.sessions_js.split("async function _doSaveSession", 1)[1]
        save_function = save_function.split("async function saveCurrentSession", 1)[0]
        self.assertIn("history.some(message => message.role === 'user')", save_function)
        self.assertLess(
            save_function.index("history.some(message => message.role === 'user')"),
            save_function.index("eel.save_chat_session"),
        )

    def test_session_content_loads_before_current_chat_is_replaced(self):
        switch = self.sessions_js.split("async function switchSession", 1)[1]
        switch = switch.split("async function deleteSession", 1)[0]
        self.assertIn("window.runUiLoadWithRetry(loader)", switch)
        self.assertLess(
            switch.index("await window.runUiLoadWithRetry(loader)"),
            switch.index("currentSessionId = requestedId"),
        )
        self.assertLess(
            switch.index("currentSessionId = requestedId"),
            switch.index("resetChatArea()"),
        )

    def test_approved_learning_immediately_reloads_library(self):
        approval = self.chat_js.split("async function approveLearningReview", 1)[1]
        approval = approval.split("async function discardPendingLearning", 1)[0]
        self.assertIn("await updateLearnedMacroList();", approval)

    def test_initial_page_loads_sessions_and_learned_library(self):
        startup = self.chat_js.split("window.addEventListener('DOMContentLoaded'", 1)[1]
        self.assertIn("loadSessions()", startup[:700])
        self.assertIn("updateLearnedMacroList()", startup[:700])

    def test_opening_macro_tab_refreshes_current_records(self):
        tab_switch = self.ui_js.split("window.switchUnifiedTab", 1)[1]
        tab_switch = tab_switch.split("const dictSearchInput", 1)[0]
        self.assertIn("updateLearnedMacroList", tab_switch)

    def test_direct_edit_learning_notice_is_deduplicated_in_the_ui(self):
        renderer = self.edit_js.split("function renderDirectEditFeedback", 1)[1]
        renderer = renderer.split("function renderEditTargetBar", 1)[0]
        self.assertIn("feedback_id", renderer)
        self.assertIn("window.lastDirectEditFeedbackId", renderer)
        self.assertIn("addSystemMessage", renderer)
        self.assertNotIn("selected_text", renderer)


if __name__ == "__main__":
    unittest.main()
