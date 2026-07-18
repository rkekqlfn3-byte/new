import json
import unittest

from engine.recovery import (
    PreExecutionRecoveryContract,
    recovery_target_signature,
)


class PreExecutionRecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.raw_target = "사내용 비밀앱"
        self.signature = recovery_target_signature("OPEN", [self.raw_target])

    def contract(self, **changes):
        values = {
            "strategy": "bounded_app_rediscovery",
            "target_kind": "app",
            "target_signature": self.signature,
        }
        values.update(changes)
        return PreExecutionRecoveryContract(**values)

    def test_one_pre_execution_attempt_records_content_free_proof(self):
        contract = self.contract(retry_limit=99)
        self.assertTrue(contract.begin(self.signature))
        outcome = contract.complete(
            "recovered",
            current_target_signature=self.signature,
            target_resolved=True,
        )
        proof = contract.to_dict()

        self.assertEqual("recovered", outcome)
        self.assertEqual("pre_execution", proof["phase"])
        self.assertFalse(proof["execution_started"])
        self.assertTrue(proof["target_unchanged"])
        self.assertEqual(1, proof["retry_count"])
        self.assertEqual(1, proof["retry_limit"])
        self.assertNotIn(self.raw_target, json.dumps(proof, ensure_ascii=False))

    def test_second_attempt_is_refused(self):
        contract = self.contract()
        self.assertTrue(contract.begin(self.signature))
        self.assertFalse(contract.begin(self.signature))
        self.assertEqual("retry_exhausted", contract.to_dict()["outcome"])
        self.assertEqual(1, contract.to_dict()["retry_count"])

    def test_attempt_after_execution_started_is_refused(self):
        contract = self.contract()
        contract.mark_execution_started()
        self.assertFalse(contract.begin(self.signature))
        proof = contract.to_dict()
        self.assertEqual("blocked_after_execution", proof["outcome"])
        self.assertTrue(proof["execution_started"])
        self.assertFalse(proof["attempted"])
        self.assertEqual(0, proof["retry_count"])

    def test_changed_target_is_refused_before_and_after_discovery(self):
        other = recovery_target_signature("CLOSE", [self.raw_target])
        before = self.contract()
        self.assertFalse(before.begin(other))
        self.assertEqual("target_changed", before.to_dict()["outcome"])

        after = self.contract()
        self.assertTrue(after.begin(self.signature))
        self.assertEqual(
            "target_changed",
            after.complete(
                "recovered",
                current_target_signature=other,
                target_resolved=True,
            ),
        )
        self.assertFalse(after.to_dict()["target_unchanged"])
        self.assertFalse(after.to_dict()["target_resolved"])

    def test_recovered_outcome_requires_a_resolved_target(self):
        contract = self.contract()
        self.assertTrue(contract.begin(self.signature))
        self.assertEqual(
            "not_found",
            contract.complete(
                "recovered",
                current_target_signature=self.signature,
                target_resolved=False,
            ),
        )

    def test_completion_without_reserved_attempt_is_invalid(self):
        contract = self.contract(retry_limit="invalid")
        self.assertEqual(
            "invalid_contract_state",
            contract.complete(
                "recovered",
                current_target_signature=self.signature,
                target_resolved=True,
            ),
        )
        proof = contract.to_dict()
        self.assertFalse(proof["attempted"])
        self.assertFalse(proof["target_resolved"])
        self.assertEqual(0, proof["retry_limit"])


if __name__ == "__main__":
    unittest.main()
