import unittest
from pathlib import Path


GUI_ROOT = Path(__file__).resolve().parent.parent / "gui"


class PersistedUiRefreshContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.globals_js = (GUI_ROOT / "globals.js").read_text(encoding="utf-8")
        cls.sessions_js = (GUI_ROOT / "session_manager.js").read_text(encoding="utf-8")
        cls.chat_js = (GUI_ROOT / "chat.js").read_text(encoding="utf-8")
        cls.ui_js = (GUI_ROOT / "ui.js").read_text(encoding="utf-8")

    def test_first_eel_load_has_bounded_retry(self):
        self.assertIn("window.runUiLoadWithRetry", self.globals_js)
        self.assertIn("attempts = 5", self.globals_js)
        self.assertNotIn("setInterval", self.globals_js)

    def test_saved_session_immediately_reloads_sidebar(self):
        save_tail = self.sessions_js.split("await eel.save_chat_session(", 1)[1]
        self.assertIn("await loadSessions();", save_tail[:1000])

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


if __name__ == "__main__":
    unittest.main()
