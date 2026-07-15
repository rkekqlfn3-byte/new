"""Failure-oriented tests for the shared atomic JSON storage layer."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from engine.managers.dict_manager import DictionaryManager
from engine.api import config_api
from engine.storage import json_store
from engine.storage.json_store import atomic_write_json, safe_read_json


class AtomicJsonStorageTests(unittest.TestCase):
    def test_read_removes_only_abandoned_pid_temporary_files(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            atomic_write_json(path, {"value": "kept"})
            abandoned = os.path.join(temp_dir, ".state.json.987654321.dead.tmp")
            abandoned_backup = os.path.join(
                temp_dir, ".state.json.bak.987654321.dead.tmp"
            )
            active = os.path.join(
                temp_dir, f".state.json.{os.getpid()}.active.tmp"
            )
            Path(abandoned).write_text("partial", encoding="utf-8")
            Path(abandoned_backup).write_text("partial backup", encoding="utf-8")
            Path(active).write_text("active", encoding="utf-8")

            with mock.patch("psutil.pid_exists", side_effect=lambda pid: pid == os.getpid()):
                loaded = safe_read_json(path, {})

            self.assertEqual({"value": "kept"}, loaded)
            self.assertFalse(os.path.exists(abandoned))
            self.assertFalse(os.path.exists(abandoned_backup))
            self.assertTrue(os.path.exists(active))

    def test_atomic_round_trip_and_backup_recovery(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "state.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(path, {"version": 2})
            self.assertEqual({"version": 1}, safe_read_json(f"{path}.bak"))

            path.write_text('{"broken":', encoding="utf-8")
            recovered = safe_read_json(path, {"fallback": True})

            self.assertEqual({"version": 1}, recovered)
            self.assertEqual({"version": 1}, json.loads(path.read_text(encoding="utf-8")))
            events = json_store.get_recovery_events(clear=True)
            self.assertEqual(str(path), events[-1]["path"])
            self.assertTrue(events[-1]["source"].endswith(".bak"))

    def test_failed_final_replace_keeps_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"stable": true}', encoding="utf-8")

            with mock.patch.object(json_store, "backup_json", return_value=[]), \
                 mock.patch.object(
                     json_store, "_replace_with_retry", side_effect=PermissionError("locked")
                 ):
                with self.assertRaises(PermissionError):
                    atomic_write_json(path, {"stable": False})

            self.assertEqual({"stable": True}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(list(Path(temp_dir).glob("*.tmp")))
            self.assertFalse(list(Path(temp_dir).glob(".*.tmp")))

    def test_replace_retries_short_windows_lock(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "state.json"
            real_replace = os.replace
            attempts = {"count": 0}

            def flaky_replace(source, destination):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise PermissionError("temporarily locked")
                return real_replace(source, destination)

            with mock.patch.object(json_store.os, "replace", side_effect=flaky_replace):
                atomic_write_json(path, {"saved": True}, retries=3)

            self.assertEqual(3, attempts["count"])
            self.assertEqual({"saved": True}, safe_read_json(path))

    def test_concurrent_writers_never_produce_partial_json(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "state.json"

            def write(index):
                atomic_write_json(path, {"writer": index, "payload": "값" * 200})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(24)))

            final = safe_read_json(path)
            self.assertIn(final["writer"], range(24))
            self.assertEqual("값" * 200, final["payload"])

    def test_version_backups_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            root = Path(temp_dir)
            path = root / "dictionaries.json"
            versions = root / "backups"
            for value in range(8):
                atomic_write_json(
                    path, {"value": value}, max_versions=5,
                    backup_dir=versions, version_interval_seconds=0,
                )

            files = list(versions.glob("dictionaries_*.json"))
            self.assertEqual(5, len(files))
            self.assertTrue(all(safe_read_json(item) for item in files))

    def test_version_backups_are_rate_limited_for_frequent_saves(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            root = Path(temp_dir)
            path = root / "dictionaries.json"
            versions = root / "backups"
            for value in range(5):
                atomic_write_json(
                    path, {"value": value}, max_versions=5,
                    backup_dir=versions, version_interval_seconds=300,
                )

            self.assertEqual(1, len(list(versions.glob("dictionaries_*.json"))))

    def test_broken_primary_and_backup_return_independent_default(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "state.json"
            backup = Path(f"{path}.bak")
            path.write_text("broken", encoding="utf-8")
            backup.write_text("also broken", encoding="utf-8")
            default = {"items": []}

            result = safe_read_json(path, default)
            result["items"].append("changed")

            self.assertEqual([], default["items"])


class DictionarySchemaMigrationTests(unittest.TestCase):
    def test_legacy_dictionary_gets_schema_version_without_losing_data(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "dictionaries.json"
            path.write_text(json.dumps({
                "noun_dictionary": {"내앱": "my.exe"},
                "macro_dictionary": {},
                "learned_macros": {},
            }, ensure_ascii=False), encoding="utf-8")

            manager = DictionaryManager(str(path))
            persisted = safe_read_json(path)

            self.assertEqual("my.exe", manager.noun_dict["내앱"])
            self.assertEqual(4, persisted["schema_version"])
            self.assertEqual("my.exe", persisted["noun_dictionary"]["내앱"])
            self.assertTrue(Path(f"{path}.bak").is_file())


class UserDataRecoveryTests(unittest.TestCase):
    def test_user_memory_recovers_last_good_backup(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir:
            path = Path(temp_dir) / "user_memory.json"
            with mock.patch.object(config_api, "USER_MEMORY_PATH", str(path)):
                config_api.save_user_memory("첫 기억", "규칙1", "")
                config_api.save_user_memory("둘째 기억", "규칙2", "")
                path.write_text("{broken", encoding="utf-8")

                recovered = config_api.load_user_memory()

            self.assertEqual("첫 기억", recovered["user_info"])
            self.assertEqual("규칙1", recovered["rules"])
            self.assertEqual("첫 기억", safe_read_json(path)["user_info"])

    def test_chat_session_recovers_and_removes_its_backup_on_delete(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-storage-test-") as temp_dir, \
             mock.patch.object(config_api, "CHAT_SESSION_DIR", temp_dir):
            path = Path(temp_dir) / "session_recovery.json"
            config_api.save_chat_session(
                "session_recovery", "첫 제목", [{"content": "첫 내용"}]
            )
            config_api.save_chat_session(
                "session_recovery", "둘째 제목", [{"content": "둘째 내용"}]
            )
            path.write_text("{broken", encoding="utf-8")

            recovered = config_api.load_chat_session("session_recovery")
            listed = config_api.get_chat_sessions()

            self.assertEqual("첫 제목", recovered["title"])
            self.assertEqual("첫 제목", listed[0]["title"])
            self.assertTrue(config_api.delete_chat_session("session_recovery"))
            self.assertFalse(path.exists())
            self.assertFalse(Path(f"{path}.bak").exists())


if __name__ == "__main__":
    unittest.main()
