import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from engine.managers.browser_scanner import (
    clean_bookmark_name,
    discover_chrome_bookmark_files,
    scan_chrome_bookmarks,
)


def write_bookmarks(profile_dir, entries):
    profile_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "children": entries,
            }
        }
    }
    (profile_dir / "Bookmarks").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class ChromeProfileDiscoveryTests(unittest.TestCase):
    def test_discovers_default_and_numbered_profiles_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_bookmarks(root / "Profile 10", [])
            write_bookmarks(root / "Default", [])
            write_bookmarks(root / "Profile 2", [])
            write_bookmarks(root / "System Profile", [])
            (root / "Profile 1").mkdir()

            profiles = discover_chrome_bookmark_files(root)

            self.assertEqual(
                ["Default", "Profile 2", "Profile 10"],
                [name for name, _ in profiles],
            )

    def test_returns_empty_when_chrome_data_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual([], discover_chrome_bookmark_files(Path(temp_dir) / "missing"))


class ChromeBookmarkMergeTests(unittest.TestCase):
    def test_korean_names_are_preserved_and_profiles_are_merged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_bookmarks(
                root / "Default",
                [
                    {"type": "url", "name": "네이버 뉴스", "url": "https://news.naver.com"},
                    {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "GitHub!", "url": "https://github.com"}
                        ],
                    },
                ],
            )
            write_bookmarks(
                root / "Profile 2",
                [
                    {"type": "url", "name": "네이버 뉴스", "url": "https://duplicate.invalid"},
                    {"type": "url", "name": "개발 문서", "url": "https://docs.example.com"},
                ],
            )
            broken = root / "Profile 3"
            broken.mkdir()
            (broken / "Bookmarks").write_text("{broken", encoding="utf-8")
            nouns = {"github": "https://existing.example.com"}

            with contextlib.redirect_stdout(io.StringIO()):
                added = scan_chrome_bookmarks(nouns, root)

            self.assertEqual(2, added)
            self.assertEqual("https://news.naver.com", nouns["네이버 뉴스"])
            self.assertEqual("https://docs.example.com", nouns["개발 문서"])
            self.assertEqual("https://existing.example.com", nouns["github"])

    def test_name_cleanup_keeps_korean_english_and_spaces(self):
        self.assertEqual("네이버 뉴스 github", clean_bookmark_name(" 네이버 뉴스 - GitHub! "))

    def test_returns_minus_one_when_no_profile_has_bookmarks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(-1, scan_chrome_bookmarks({}, temp_dir))


if __name__ == "__main__":
    unittest.main()


class MobileBookmarkTests(unittest.TestCase):
    """Bookmarks synced from a phone are not apps on this computer."""

    def _write_roots(self, profile_dir, roots):
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "Bookmarks").write_text(
            json.dumps({"roots": roots}, ensure_ascii=False), encoding="utf-8"
        )

    def test_the_mobile_folder_is_left_out_of_the_app_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_roots(
                root / "Default",
                {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "회사 위키", "url": "https://wiki"}
                        ],
                    },
                    # Chrome's own interface calls this 모바일 즐겨찾기.
                    "synced": {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "폰 북마크", "url": "https://phone"}
                        ],
                    },
                },
            )
            nouns = {}
            scan_chrome_bookmarks(nouns, root)

            self.assertEqual({"회사 위키": "https://wiki"}, nouns)

    def test_the_other_bookmarks_folder_is_still_scanned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_roots(
                root / "Default",
                {
                    "other": {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "기타 항목", "url": "https://other"}
                        ],
                    }
                },
            )
            nouns = {}
            scan_chrome_bookmarks(nouns, root)

            self.assertIn("기타 항목", nouns)
