import unittest
from unittest import mock

from verification import prototype11_hwp_watchdog_probe as probe


class Prototype11HwpWatchdogProbeTests(unittest.TestCase):
    def test_existing_user_hwp_process_skips_probe(self):
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            report = probe.run_probe()

        self.assertFalse(report["success"])
        self.assertEqual(
            "skipped_user_hwp_running",
            report["result"]["status"],
        )
        self.assertTrue(report["result"]["user_process_protected"])
        context.assert_not_called()

    def test_pass_requires_worker_success_and_owned_process_cleanup(self):
        with (
            mock.patch.object(
                probe,
                "_process_ids",
                side_effect=[set(), set()],
            ),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {
                "status": "passed",
                "checks": {
                    "bounded_timeout_or_generation": True,
                    "generation_readback_or_typed_block": True,
                    "partial_output_absent_on_block": True,
                },
            }
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertTrue(
            report["result"]["owned_process_cleanup_verified"]
        )


if __name__ == "__main__":
    unittest.main()
