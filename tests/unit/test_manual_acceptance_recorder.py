import tempfile
import unittest
from pathlib import Path

from verification.manual_acceptance_recorder import (
    PROTOCOL_IDS,
    initialize_evidence,
    record_evidence,
)


class ManualAcceptanceRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "manual.json"

    def test_initialize_creates_only_pending_protocol_records(self):
        evidence = initialize_evidence(self.path)

        self.assertEqual(2, evidence["schema_version"])
        self.assertEqual(set(PROTOCOL_IDS), set(evidence["checks"]))
        self.assertTrue(
            all(item["status"] == "pending" for item in evidence["checks"].values())
        )

    def test_final_status_requires_explicit_attestation(self):
        initialize_evidence(self.path)

        with self.assertRaisesRegex(ValueError, "requires --attest"):
            record_evidence(self.path, "screen_reader", "passed")

    def test_completed_protocol_records_timezone_and_attestation(self):
        initialize_evidence(self.path)

        evidence = record_evidence(
            self.path,
            "voice_input",
            "passed",
            attested=True,
            performed_at="2026-08-03T23:30:00+09:00",
        )

        item = evidence["checks"]["voice_input"]
        self.assertEqual("passed", item["status"])
        self.assertEqual(PROTOCOL_IDS["voice_input"], item["protocol_id"])
        self.assertTrue(item["attested"])
        self.assertEqual("2026-08-03T23:30:00+09:00", item["performed_at"])

    def test_naive_timestamp_is_rejected(self):
        initialize_evidence(self.path)

        with self.assertRaisesRegex(ValueError, "timezone"):
            record_evidence(
                self.path,
                "novice_user_observation",
                "failed",
                attested=True,
                performed_at="2026-08-03T23:30:00",
            )


if __name__ == "__main__":
    unittest.main()
