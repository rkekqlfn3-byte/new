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


class UserFeedbackUiContractTests(unittest.TestCase):
    def setUp(self):
        self.html = read_gui_file("index.html")
        self.script = read_gui_script("user_feedback.js")

    def test_activity_region_and_eel_callback_are_shipped(self):
        self.assertIn('id="user-feedback-region"', self.html)
        self.assertIn('role="status"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('src="js/user_feedback.js"', self.html)
        self.assertIn("window.eel.expose(window.receive_user_event", self.script)

    def test_activity_renderer_uses_closed_schema_and_text_nodes(self):
        self.assertIn("value.schema_version !== 1", self.script)
        self.assertIn("EVENT_TYPES.has(eventType)", self.script)
        self.assertIn("TONES.has(value.tone)", self.script)
        self.assertIn("node.textContent = text", self.script)
        self.assertIn("region.replaceChildren(card)", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_response_event_is_a_deduplicated_fallback(self):
        controller = read_gui_script("chat/controller.js")
        self.assertIn("response?.user_event", controller)
        self.assertIn("window.renderUserEvent(response.user_event)", controller)
        self.assertIn("signature === lastSignature", self.script)


class AccessibilityContractTests(unittest.TestCase):
    def setUp(self):
        self.html = read_gui_file("index.html")
        self.chat_css = read_gui_file("css/chat.css")
        self.globals = read_gui_script("globals.js")

    def test_mode_radios_remain_keyboard_focusable(self):
        for mode in ("command", "question", "edit"):
            self.assertRegex(
                self.html,
                rf'id="mode-{mode}"[^>]+class="mode-input"',
            )
        self.assertIn(".mode-input {", self.chat_css)
        self.assertIn(".mode-input:focus-visible + .mode-tab", self.chat_css)
        self.assertNotIn(
            'name="chat-mode" value="edit" class="hidden-input"',
            self.html,
        )
        edit_mode = read_gui_script("edit_mode.js")
        self.assertIn("const chatModeInputs = Array.from", edit_mode)
        self.assertIn("ArrowRight: 1", edit_mode)
        self.assertIn("target.dispatchEvent(new Event('change'", edit_mode)

    def test_chat_updates_and_icon_buttons_have_accessible_names(self):
        self.assertIn('id="chat-area" role="log" aria-live="polite"', self.html)
        for label in (
            "추가 기능 메뉴", "이미지 파일 첨부", "요청 전송",
            "현재 실행 취소", "자비스에게 요청 입력", "사이드바 접기",
            "터미널 열기", "실행 터미널 기록",
        ):
            self.assertIn(f'aria-label="{label}"', self.html)

    def test_sidebar_tabs_and_panel_toggles_have_keyboard_state_contract(self):
        app = read_gui_script("app.js")
        controller = read_gui_script("chat/controller.js")
        self.assertIn('aria-controls="sidebar-panel"', self.html)
        self.assertIn('aria-controls="terminal-pane"', self.html)
        self.assertIn('aria-expanded="true"', self.html)
        self.assertIn('aria-expanded="false"', self.html)
        self.assertIn('aria-hidden="true"', self.html)
        self.assertIn('aria-selected="true"', self.html)
        self.assertIn('tabindex="-1"', self.html)
        self.assertIn("const sidebarTabs = Array.from", app)
        self.assertIn("ArrowRight:", app)
        self.assertIn("ArrowLeft:", app)
        self.assertIn("next.focus({ preventScroll: true })", app)
        self.assertIn("sidebar.setAttribute('aria-hidden', String(!expanded))", app)
        self.assertIn("sidebar.removeAttribute('inert')", app)
        self.assertIn("sidebar.setAttribute('inert', '')", app)
        self.assertIn("chatHistoryTab.tabIndex = showHistory ? 0 : -1", controller)
        self.assertIn("userMemoryTab.tabIndex = showHistory ? -1 : 0", controller)
        self.assertIn("paneMemory.hidden = showHistory", controller)
        self.assertIn("rightToggle.setAttribute('aria-expanded'", controller)
        self.assertIn("terminalPane.setAttribute('aria-hidden'", controller)

    def test_memory_view_exposes_selected_toggle_and_named_textboxes(self):
        controller = read_gui_script("chat/controller.js")
        self.assertIn('role="group" aria-label="영구 기억 종류"', self.html)
        self.assertIn('aria-pressed="true" aria-controls="mem-user"', self.html)
        self.assertEqual(2, self.html.count('aria-pressed="false"'))
        for label in ("사용자 정보 기억", "응답 규칙 기억", "기타 기억"):
            self.assertIn(f'aria-label="{label}"', self.html)
        self.assertIn("t.setAttribute('aria-pressed', String(t === clickedTab))", controller)
        self.assertIn("b.hidden = true", controller)
        self.assertIn("activeBox.hidden = false", controller)

    def test_dialogs_trap_focus_close_with_escape_and_restore_origin(self):
        self.assertEqual(5, self.html.count('class="modal" role="dialog"'))
        self.assertEqual(5, self.html.count('aria-modal="true" aria-hidden="true"'))
        self.assertNotIn('<span class="close-modal"', self.html)
        self.assertIn("window.openAccessibleModal = function", self.globals)
        self.assertIn("window.closeAccessibleModal = function", self.globals)
        self.assertIn("modalFocusOrigins.set(modal, trigger)", self.globals)
        self.assertIn("event.key === 'Escape'", self.globals)
        self.assertIn("event.key !== 'Tab'", self.globals)
        self.assertIn("origin.focus({ preventScroll: true })", self.globals)

    def test_history_and_edit_file_controls_have_keyboard_actions(self):
        sessions = read_gui_script("sessions.js")
        edit_mode = read_gui_script("edit_mode.js")
        self.assertIn("const info = document.createElement('button')", sessions)
        self.assertIn("info.setAttribute('aria-label'", sessions)
        self.assertIn("remove.setAttribute('aria-label'", sessions)
        self.assertIn('id="edit-file-drop-zone"', self.html)
        self.assertIn('role="button"', self.html)
        self.assertIn("editDropZone.addEventListener('keydown'", edit_mode)
        self.assertIn("['Enter', ' '].includes(event.key)", edit_mode)

    def test_confirmation_card_moves_focus_to_the_required_choice(self):
        confirmation = read_gui_script("chat/confirmation.js")
        self.assertIn("card.setAttribute('role', 'region')", confirmation)
        self.assertIn("card.setAttribute('aria-labelledby', heading.id)", confirmation)
        self.assertIn(
            "card.querySelector('.confirmation-option.recommended, .confirmation-option')?.focus",
            confirmation,
        )


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

    def test_verified_artifact_handoff_updates_gui_session_before_refresh(self):
        edit_mode = read_gui_script("edit_mode.js")
        controller = read_gui_script("chat/controller.js")
        self.assertIn(
            "window.applyEditSessionHandoff = applyEditSessionHandoff",
            edit_mode,
        )
        self.assertIn("response?.data?.edit_session_handoff", controller)
        self.assertIn(
            "window.applyEditSessionHandoff(",
            controller,
        )
        handoff_index = controller.index(
            "window.applyEditSessionHandoff("
        )
        final_refresh_index = controller.index(
            'if (mode === "edit") await window.refreshEditContext',
            handoff_index,
        )
        self.assertLess(handoff_index, final_refresh_index)


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
