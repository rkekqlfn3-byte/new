"""Regression checks for the GUI's AI-response HTML sanitization boundary.

The chat window can call eel-exposed Python functions, so raw HTML from an
AI response (or a saved conversation) must never reach ``innerHTML``
unsanitized. These tests pin the sanitization patterns in the shipped GUI
files, in the same text-inspection style as ``test_release_security``.
"""

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_DIR = PROJECT_ROOT / "gui"


def read_gui_file(name):
    return (GUI_DIR / name).read_text(encoding="utf-8")


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
        self.chat = read_gui_file("chat.js")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
