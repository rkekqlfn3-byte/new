"""Regression checks for the GUI's AI-response HTML sanitization boundary.

The chat window can call eel-exposed Python functions, so raw HTML from an
AI response (or a saved conversation) must never reach ``innerHTML``
unsanitized. These tests pin the sanitization patterns in the shipped GUI
files, in the same text-inspection style as ``test_release_security``.
"""

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = PROJECT_ROOT / "gui"


def read_gui_file(name):
    return (GUI_DIR / name).read_text(encoding="utf-8")


def read_gui_script(name):
    return (GUI_DIR / "js" / name).read_text(encoding="utf-8")


class MarkedLocalBundleTests(unittest.TestCase):
    def test_index_loads_marked_from_local_vendor_only(self):
        html = read_gui_file("index.html")
        self.assertIn('src="vendor/marked.min.js"', html)
        self.assertNotIn("cdn.jsdelivr.net", html)

    def test_vendor_bundle_exists_and_is_marked(self):
        bundle = GUI_DIR / "vendor" / "marked.min.js"
        self.assertTrue(bundle.is_file(), "gui/vendor/marked.min.js가 없습니다.")
        head = bundle.read_text(encoding="utf-8")[:200]
        self.assertIn("marked", head)
        self.assertGreater(bundle.stat().st_size, 10_000)

    def test_index_has_no_other_remote_scripts_or_styles(self):
        html = read_gui_file("index.html")
        remote = re.findall(
            r'<(?:script|link)[^>]+(?:src|href)="(https?:)?//[^"]+"', html
        )
        self.assertEqual([], remote)


class ChatSanitizationTests(unittest.TestCase):
    def setUp(self):
        self.chat = "\n".join((
            read_gui_script("chat/renderer.js"),
            read_gui_script("chat/streaming.js"),
        ))

    def test_rendered_markdown_is_sanitized_before_innerhtml(self):
        self.assertIn("function sanitizeRenderedHtml(", self.chat)
        self.assertIn("sanitizeRenderedHtml(marked.parse(", self.chat)
        # marked가 없을 때의 폴백도 원문을 그대로 넣지 않고 이스케이프한다.
        self.assertIn("escapeHtml(processed)", self.chat)

    def test_sanitizer_uses_tag_allowlist_and_href_allowlist(self):
        match = re.search(
            r"SANITIZE_ALLOWED_TAGS = new Set\(\[(.*?)\]\)", self.chat, re.S
        )
        self.assertIsNotNone(match, "태그 허용 목록 상수가 없습니다.")
        allowed = set(re.findall(r"'([A-Z0-9]+)'", match.group(1)))
        self.assertIn("A", allowed)
        self.assertIn("CODE", allowed)
        for banned in (
            "SCRIPT", "IFRAME", "IMG", "STYLE", "FORM",
            "INPUT", "OBJECT", "EMBED", "LINK", "META",
        ):
            self.assertNotIn(
                banned, allowed, f"허용 태그 목록에 {banned}는 안 됩니다."
            )
        self.assertIn("SANITIZE_SAFE_HREF", self.chat)
        self.assertIn("(https?:|mailto:)", self.chat)
        self.assertIn("noopener noreferrer", self.chat)

    def test_stream_chunks_are_inserted_as_text_nodes(self):
        self.assertIn(
            "window.appendTextLineBreaks(bubble, currentStreamRawContent);",
            self.chat,
        )
        self.assertNotIn(
            "innerHTML = currentStreamRawContent.replace", self.chat
        )

    def test_image_attachment_is_restricted_to_raster_data_urls(self):
        self.assertIn("function isSafeImageData(imageData)", self.chat)
        self.assertIn("png|jpeg|gif|webp", self.chat)
        self.assertIn("image.addEventListener('click'", self.chat)

    def test_korean_suffix_after_strong_markdown_gets_invisible_boundary(self):
        self.assertIn("function normalizeKoreanMarkdownBoundaries(", self.chat)
        self.assertIn("(?=[가-힣])", self.chat)
        self.assertIn("`**${content}\\u200B**\\u200B`", self.chat)

    def test_command_reference_state_is_bounded_and_sent_separately(self):
        controller = read_gui_script("chat/controller.js")
        sessions = read_gui_script("sessions.js")
        self.assertIn("function boundedReferenceText(", controller)
        self.assertIn("recent_turns: turns.slice(-3)", controller)
        self.assertIn("mode === 'command' ? window.commandConversationState : null", controller)
        self.assertIn("window.commandConversationState || {}", sessions)
        self.assertIn("restoredState.recent_turns.slice(-3)", sessions)


class ChatBubbleLayoutTests(unittest.TestCase):
    def setUp(self):
        self.css = read_gui_file("css/chat.css")

    def test_message_content_is_bounded_by_the_outer_message(self):
        block = re.search(r"\.message-content\s*\{(.*?)\}", self.css, re.S)
        self.assertIsNotNone(block)
        self.assertIn("width: 100%", block.group(1))
        self.assertIn("min-width: 0", block.group(1))
        self.assertIn("max-width: 100%", block.group(1))

    def test_markdown_content_wraps_inside_the_bubble(self):
        bubble = re.search(r"\.bubble\s*\{(.*?)\}", self.css, re.S)
        self.assertIsNotNone(bubble)
        self.assertIn("overflow-wrap: anywhere", bubble.group(1))
        self.assertIn("max-width: 100%", bubble.group(1))
        self.assertIn(".bubble ul", self.css)
        self.assertIn("padding-inline-start: 1.45em", self.css)
        self.assertIn(".bubble pre", self.css)
        self.assertIn("white-space: pre-wrap", self.css)
        self.assertIn(".bubble table", self.css)
        self.assertIn("overflow-x: auto", self.css)


class EditModeUiContractTests(unittest.TestCase):
    def test_edit_tab_and_local_document_controls_are_shipped(self):
        html = read_gui_file("index.html")
        self.assertIn('name="chat-mode" value="edit"', html)
        self.assertIn('id="edit-file-drop-zone"', html)
        self.assertIn('id="btn-edit-connect-active"', html)
        self.assertIn('src="js/edit_mode.js"', html)

    def test_edit_document_drop_never_reads_or_uploads_file_content(self):
        script = read_gui_script("edit_mode.js")
        self.assertIn("connect_dropped_edit_document", script)
        self.assertNotIn("FileReader", script)
        self.assertNotIn("readAsDataURL", script)
        self.assertNotIn("FormData", script)
        self.assertNotIn("innerHTML", script)

    def test_edit_requests_include_session_and_current_context_identity(self):
        controller = read_gui_script("chat/controller.js")
        self.assertIn(
            "await window.refreshEditContext({ required: true })",
            controller,
        )
        self.assertIn("edit_session_id: activeEditSession.session_id", controller)
        self.assertIn(
            "document_fingerprint: activeEditSession.document_fingerprint",
            controller,
        )
        self.assertIn(
            "context_fingerprint: latestEditContext.context_fingerprint",
            controller,
        )

    def test_current_edit_context_is_event_driven_and_rendered_as_text(self):
        html = read_gui_file("index.html")
        script = read_gui_script("edit_mode.js")
        self.assertIn('id="edit-context-target"', html)
        self.assertIn('id="edit-context-preview"', html)
        self.assertIn('id="btn-edit-refresh-context"', html)
        self.assertIn("eel.get_edit_context(sessionId)", script)
        self.assertIn("editContextTarget.textContent", script)
        self.assertIn("editContextPreview.textContent", script)
        self.assertIn("editContextRefreshPromise", script)
        self.assertNotIn("setInterval(refreshEditContext", script)


class DictionaryDomSafetyTests(unittest.TestCase):
    def setUp(self):
        self.macros = read_gui_script("dictionaries/macros.js")
        self.actions = read_gui_script("dictionaries/action_dictionary.js")
        self.learned = read_gui_script("dictionaries/learned_skills.js")
        self.candidates = read_gui_script("dictionaries/native_candidates.js")

    def test_action_errors_are_inserted_as_text(self):
        self.assertIn("error.textContent", self.actions)
        self.assertIn("actionDictList.replaceChildren(error)", self.actions)
        self.assertNotIn("${e.message}", self.actions)
        self.assertNotIn("itemDiv.innerHTML", self.actions)

    def test_dictionary_numeric_values_are_normalized_before_templates(self):
        self.assertIn("function safeDictionaryInteger(", self.macros)
        self.assertIn(
            "safeDictionaryInteger(record.step_count", self.learned
        )
        self.assertNotIn("${record.step_count}", self.learned)
        for field in (
            "process_success_count", "verified_success_count",
            "user_confirmed_count", "failure_count", "distinct_document_count",
        ):
            self.assertIn(f"safeDictionaryInteger(record.{field}", self.candidates)

    def test_dictionary_strings_use_text_nodes_or_escaping(self):
        self.assertIn("name.textContent = macro", self.actions)
        self.assertIn("codeBox.textContent = data.description", self.actions)
        self.assertIn("escapeDictionaryHtml(record.description", self.learned)
        self.assertIn("escapeDictionaryHtml(record.description", self.candidates)


if __name__ == "__main__":
    unittest.main(verbosity=2)
