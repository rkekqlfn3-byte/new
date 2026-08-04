import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.api import config_api
from engine.document_reader import extract_text, resolve_file_path
from engine.managers.dict_manager import DictionaryManager
from engine.runtime_paths import seed_user_data
from engine.security.credential_protection import CredentialProtectionError


class DictionaryPersistenceTests(unittest.TestCase):
    def test_dictionary_config_and_macro_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            manager = DictionaryManager(dictionary_path=path)

            self.assertTrue(manager.add_custom_noun("테스트앱", "test.exe"))
            self.assertTrue(manager.add_noun_synonym("테스트앱", "별명앱"))
            self.assertTrue(manager.rename_noun("테스트앱", "새테스트앱"))
            self.assertTrue(manager.macro_manager.add_custom_macro(
                "TEST_MACRO", "테스트", ["테스트 실행"], "hotkey", "ctrl+shift+9"
            ))
            self.assertTrue(manager.config_manager.save_ai_config(
                "gemini", "test-key", "ai_first"
            ))

            stored = Path(path).read_text(encoding="utf-8")
            stored_json = json.loads(stored)
            self.assertNotIn("test-key", stored)
            self.assertNotIn("api_key", stored_json["ai_config"])
            self.assertTrue(
                stored_json["ai_config"]["api_key_protected"].startswith(
                    "dpapi:v1:"
                )
            )

            reloaded = DictionaryManager(dictionary_path=path)
            self.assertEqual("test.exe", reloaded.noun_dict["새테스트앱"])
            self.assertEqual("test.exe", reloaded.noun_dict["별명앱"])
            self.assertEqual("ctrl+shift+9", reloaded.macro_dict["TEST_MACRO"]["data"])
            self.assertEqual("gemini", reloaded.get_ai_config()["provider"])
            self.assertEqual(
                {"provider", "api_key", "routing_mode"},
                set(reloaded.get_ai_config()),
            )
            self.assertEqual("ai_first", reloaded.get_ai_config()["routing_mode"])
            self.assertEqual("test-key", reloaded.get_ai_config()["api_key"])

    def test_legacy_plaintext_key_and_backups_migrate_to_dpapi(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "dictionaries.json"
            backup = Path(f"{path}.bak")
            version_dir = root / "backups"
            version_dir.mkdir()
            version = version_dir / "dictionaries_legacy.json"
            legacy_secret = "owned-legacy-api-key"
            legacy = {
                "schema_version": 4,
                "ai_config": {
                    "provider": "openai",
                    "api_key": legacy_secret,
                    "routing_mode": "auto",
                },
            }
            for candidate in (path, backup, version):
                candidate.write_text(
                    json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
                )

            manager = DictionaryManager(dictionary_path=str(path))

            self.assertEqual(legacy_secret, manager.get_ai_config()["api_key"])
            for candidate in (path, backup, version):
                content = candidate.read_text(encoding="utf-8")
                stored = json.loads(content)
                self.assertNotIn(legacy_secret, content)
                self.assertEqual(5, stored["schema_version"])
                self.assertNotIn("api_key", stored["ai_config"])
                self.assertTrue(
                    stored["ai_config"]["api_key_protected"].startswith(
                        "dpapi:v1:"
                    )
                )

    def test_invalid_protected_key_is_not_erased_or_exposed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dictionaries.json"
            protected = "dpapi:v1:not-valid-base64"
            path.write_text(
                json.dumps({
                    "schema_version": 5,
                    "ai_config": {
                        "provider": "openai",
                        "api_key_protected": protected,
                        "routing_mode": "auto",
                    },
                }),
                encoding="utf-8",
            )

            manager = DictionaryManager(dictionary_path=str(path))

            self.assertEqual("", manager.get_ai_config()["api_key"])
            self.assertEqual(
                "unavailable",
                manager.config_manager.get_public_ai_config()[
                    "credential_status"
                ],
            )
            self.assertEqual(
                protected,
                json.loads(path.read_text(encoding="utf-8"))[
                    "ai_config"
                ]["api_key_protected"],
            )

    def test_protection_failure_never_mutates_or_persists_plaintext(self):
        class FailingProtector:
            def protect(self, secret):
                raise CredentialProtectionError("owned failure")

            def unprotect(self, protected):
                raise CredentialProtectionError("owned failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dictionaries.json"
            manager = DictionaryManager(
                dictionary_path=str(path),
                credential_protector=FailingProtector(),
            )
            before_config = manager.get_ai_config()
            before_file = path.read_text(encoding="utf-8")

            with self.assertRaises(CredentialProtectionError):
                manager.config_manager.save_ai_config(
                    "gemini", "must-not-be-persisted", "ai_first"
                )

            self.assertEqual(before_config, manager.get_ai_config())
            self.assertEqual(before_file, path.read_text(encoding="utf-8"))
            self.assertNotIn(
                "must-not-be-persisted", path.read_text(encoding="utf-8")
            )

    def test_new_dictionary_has_no_apps_before_manual_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            manager = DictionaryManager(dictionary_path=path)

            self.assertEqual({}, manager.noun_dict)
            self.assertEqual([], manager.favorites)
            self.assertEqual(set(), manager.user_nouns)
            self.assertFalse(manager.has_scanned)

    def test_noun_revision_changes_after_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DictionaryManager(
                dictionary_path=os.path.join(temp_dir, "dictionaries.json")
            )
            revision = manager.noun_revision
            manager.add_custom_noun("리비전앱", "revision.exe")
            self.assertGreater(manager.noun_revision, revision)


class RuntimeDataPathTests(unittest.TestCase):
    def test_seed_prefers_legacy_data_and_merges_missing_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            bundled = root / "bundled"
            target = root / "target"
            (legacy / "sessions").mkdir(parents=True)
            (bundled / "sessions").mkdir(parents=True)
            (legacy / "dictionaries.json").write_text('{"source":"legacy"}', encoding="utf-8")
            (bundled / "dictionaries.json").write_text('{"source":"bundle"}', encoding="utf-8")
            (bundled / "user_memory.json").write_text('{"user_info":"seed"}', encoding="utf-8")
            (bundled / "user_preferences.json").write_text(
                '{"schema_version":1,"preferences":{"test":{}}}', encoding="utf-8"
            )
            (legacy / "sessions" / "session_old.json").write_text('{"id":"old"}', encoding="utf-8")
            (bundled / "sessions" / "session_new.json").write_text('{"id":"new"}', encoding="utf-8")

            seed_user_data(target, [legacy, bundled])

            self.assertEqual('{"source":"legacy"}', (target / "dictionaries.json").read_text(encoding="utf-8"))
            self.assertTrue((target / "user_memory.json").is_file())
            self.assertTrue((target / "user_preferences.json").is_file())
            self.assertTrue((target / "sessions" / "session_old.json").is_file())
            self.assertTrue((target / "sessions" / "session_new.json").is_file())

    def test_seed_never_overwrites_existing_user_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "bundled"
            target = root / "target"
            bundled.mkdir()
            target.mkdir()
            (bundled / "dictionaries.json").write_text('{"version":2}', encoding="utf-8")
            (target / "dictionaries.json").write_text('{"version":1,"custom":true}', encoding="utf-8")
            (bundled / "user_preferences.json").write_text(
                '{"source":"bundle"}', encoding="utf-8"
            )
            (target / "user_preferences.json").write_text(
                '{"source":"user"}', encoding="utf-8"
            )

            seed_user_data(target, [bundled])

            self.assertEqual(
                '{"version":1,"custom":true}',
                (target / "dictionaries.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                '{"source":"user"}',
                (target / "user_preferences.json").read_text(encoding="utf-8"),
            )


class MemoryAndSessionPersistenceTests(unittest.TestCase):
    def test_user_memory_round_trip_and_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "user_memory.json")
            with patch.object(config_api, "USER_MEMORY_PATH", path):
                self.assertTrue(config_api.save_user_memory("사용자", "짧게", "기타"))
                self.assertEqual(
                    {"user_info": "사용자", "rules": "짧게", "others": "기타"},
                    config_api.load_user_memory(),
                )

                with open(path, "w", encoding="utf-8") as file:
                    json.dump({"userInfo": "이전형식", "rules": "규칙"}, file)
                self.assertEqual("이전형식", config_api.load_user_memory()["user_info"])

    def test_session_save_load_list_and_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(config_api, "CHAT_SESSION_DIR", temp_dir):
            messages = [{"role": "user", "content": "테스트 질문"}]
            self.assertTrue(config_api.save_chat_session(
                "session_test", "테스트", messages, "요약", {"mode": "question"}
            ))
            loaded = config_api.load_chat_session("session_test")
            self.assertEqual(messages, loaded["messages"])
            self.assertEqual("요약", loaded["summary"])
            self.assertEqual("session_test", config_api.get_chat_sessions()[0]["id"])
            self.assertTrue(config_api.delete_chat_session("session_test"))
            self.assertIsNone(config_api.load_chat_session("session_test"))

    def test_session_path_rejects_traversal_and_preserves_other_files(self):
        invalid_ids = [
            "../dictionaries", "..\\dictionaries", "../../config",
            "C:\\temp\\x", "/absolute/path", "session_../test",
            "session_a/b", "session_a\\b", "", " ",
            "session_" + "a" * 81, None, 42,
        ]
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(config_api, "CHAT_SESSION_DIR", temp_dir):
            sentinel = Path(temp_dir).parent / "dictionaries.json"
            sentinel.write_text('{"protected":true}', encoding="utf-8")
            for session_id in invalid_ids:
                with self.assertRaises(ValueError):
                    config_api.resolve_session_path(session_id)
                self.assertFalse(config_api.save_chat_session(
                    session_id, "bad", [], "", {}
                ))
                self.assertIsNone(config_api.load_chat_session(session_id))
                self.assertFalse(config_api.delete_chat_session(session_id))
            self.assertEqual('{"protected":true}', sentinel.read_text(encoding="utf-8"))

    def test_session_path_accepts_allowlisted_id_and_rejects_name_id_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(config_api, "CHAT_SESSION_DIR", temp_dir):
            session_id = "session_ABC_def-123"
            self.assertTrue(config_api.save_chat_session(
                session_id, "valid", [], "", {}
            ))
            self.assertTrue(config_api.resolve_session_path(session_id).is_file())
            Path(temp_dir, "session_mismatch.json").write_text(
                '{"id":"session_ABC_def-123","title":"wrong file"}',
                encoding="utf-8",
            )
            sessions = config_api.get_chat_sessions()

        self.assertEqual([session_id], [item["id"] for item in sessions])


class DocumentReaderTests(unittest.TestCase):
    def test_text_file_resolution_and_extraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "report.txt")
            with open(path, "w", encoding="utf-8") as file:
                file.write("정확한 테스트 문서")

            self.assertEqual(os.path.abspath(path), resolve_file_path(path))
            self.assertEqual("정확한 테스트 문서", extract_text(path))

    def test_missing_file_returns_clear_message(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely-missing-jarvis.txt")
        self.assertIsNone(resolve_file_path(missing))
        self.assertIn("찾을 수 없습니다", extract_text(missing))


if __name__ == "__main__":
    unittest.main()
