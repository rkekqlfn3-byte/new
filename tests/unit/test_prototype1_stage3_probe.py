import unittest
from unittest import mock

from verification import prototype1_stage3_probe as probe


class PrototypeStage3ProbeSafetyTests(unittest.TestCase):
    def test_existing_user_process_forces_read_only_discovery(self):
        bridge = mock.Mock()
        bridge.active_documents.return_value = []
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe, "NativeDocumentBridge", return_value=bridge),
            mock.patch.object(probe, "_create_document") as create_document,
        ):
            result = probe._probe_app("hwp")

        self.assertEqual("skipped_user_processes_running", result["status"])
        self.assertTrue(result["user_process_protected"])
        create_document.assert_not_called()
        self.assertEqual(
            [mock.call("hwp"), mock.call("hwp")],
            bridge.active_documents.call_args_list,
        )
        self.assertNotIn("file_path", result)


if __name__ == "__main__":
    unittest.main()
