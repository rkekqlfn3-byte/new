import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


class EditTargetUiTests(unittest.TestCase):
    def test_target_bar_and_overlay_toggle_have_unique_elements(self):
        parser = _IdCollector()
        parser.feed((PROJECT_ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        required = {
            "edit-target-bar",
            "edit-target-state",
            "edit-target-address",
            "edit-target-details",
            "edit-target-preview",
            "edit-selection-overlay",
        }
        for element_id in required:
            self.assertEqual(1, parser.ids.count(element_id), element_id)

    def test_target_bar_tracks_context_and_overlay_api(self):
        source = (PROJECT_ROOT / "gui" / "js" / "edit_mode.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("function renderEditTargetBar", source)
        self.assertIn("window.lastConfirmedEditContext", source)
        self.assertIn("eel.set_edit_selection_overlay", source)
        self.assertIn("window.addEventListener('focus', refreshEditContext)", source)
        self.assertIn("function syncEditContextMonitor", source)
        self.assertIn("const EDIT_CONTEXT_MONITOR_MS = 700", source)
        self.assertIn("window.setInterval", source)
        self.assertIn("result?.data?.session", source)
        self.assertIn("임시 연결 · 저장 전", source)

    def test_current_context_displays_location_without_selected_cell_values(self):
        source = (PROJECT_ROOT / "gui" / "js" / "edit_mode.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("editContextTarget.textContent = location", source)
        self.assertIn("editContextPreview.textContent = '';", source)
        self.assertNotIn("context.selected_text_preview", source)

    def test_target_bar_has_busy_and_responsive_styles(self):
        chat_css = (PROJECT_ROOT / "gui" / "css" / "chat.css").read_text(
            encoding="utf-8"
        )
        responsive_css = (
            PROJECT_ROOT / "gui" / "css" / "responsive.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".edit-target-bar.target-busy", chat_css)
        self.assertIn(".edit-target-bar.target-error", chat_css)
        self.assertIn(".edit-target-preview", responsive_css)


if __name__ == "__main__":
    unittest.main()
