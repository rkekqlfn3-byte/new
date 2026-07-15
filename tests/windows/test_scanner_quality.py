import json
import os
import tempfile
import unittest
from pathlib import Path

from engine.managers.app_scanner import (
    is_likely_primary_executable,
    is_noise_app_candidate,
)
from engine.managers.dict_manager import DictionaryManager


class ScannerCandidateTests(unittest.TestCase):
    def test_noise_tools_are_rejected(self):
        self.assertTrue(is_noise_app_candidate("EA Updater", r"C:\Apps\EA Updater.exe"))
        self.assertTrue(is_noise_app_candidate("프로그램 제거", r"C:\Apps\프로그램 제거.lnk"))
        self.assertTrue(is_noise_app_candidate("helper", r"C:\Apps\helper.exe"))
        self.assertFalse(is_noise_app_candidate("반디집", r"C:\Apps\Bandizip.exe"))

    def test_only_representative_deep_scan_executables_are_kept(self):
        root = r"C:\Program Files"
        self.assertTrue(
            is_likely_primary_executable(r"C:\Program Files\Bandizip\Bandizip.exe", root)
        )
        self.assertTrue(
            is_likely_primary_executable(r"C:\Program Files\Steam\steam.exe", root)
        )
        self.assertFalse(
            is_likely_primary_executable(
                r"C:\Program Files\Bandizip\data\Amsiman.x64.exe", root
            )
        )
        self.assertFalse(
            is_likely_primary_executable(r"C:\Program Files\Example\Updater.exe", root)
        )


class DictionaryCleanupTests(unittest.TestCase):
    def test_cleanup_removes_missing_and_noise_but_preserves_favorite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing_app = root / "RealApp.exe"
            existing_app.touch()
            updater = root / "Updater.exe"
            updater.touch()
            favorite_missing = str(root / "FavoriteMissing.exe")
            dictionary_path = root / "dictionaries.json"
            dictionary_path.write_text(
                json.dumps(
                    {
                        "noun_dictionary": {
                            "정상앱": str(existing_app),
                            "사라진앱": str(root / "Missing.exe"),
                            "업데이트 도구": str(updater),
                            "즐겨찾기앱": favorite_missing,
                            "웹": "https://example.com",
                            "계산기": "calc",
                        },
                        "macro_dictionary": {},
                        "search_engines_dict": {},
                        "favorites": ["즐겨찾기앱"],
                        "user_nouns": ["사용자등록앱"],
                        "has_scanned": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manager = DictionaryManager(dictionary_path=str(dictionary_path))
            manager.noun_dict["사용자등록앱"] = str(root / "TemporarilyOffline.exe")
            result = manager.prune_invalid_nouns()

            self.assertEqual(["사라진앱"], [item["noun"] for item in result["missing"]])
            self.assertEqual(["업데이트 도구"], [item["noun"] for item in result["noise"]])
            self.assertIn("정상앱", manager.noun_dict)
            self.assertIn("즐겨찾기앱", manager.noun_dict)
            self.assertIn("사용자등록앱", manager.noun_dict)
            self.assertIn("웹", manager.noun_dict)
            self.assertIn("계산기", manager.noun_dict)


if __name__ == "__main__":
    unittest.main()
