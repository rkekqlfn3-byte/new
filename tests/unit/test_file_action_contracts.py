import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from engine.file_actions import (
    FileActionContractError,
    OutputPathPolicyError,
    PageSelection,
    PreparedFileAction,
    evaluate_pdf_output_path,
    suggest_pdf_output_path,
)

SOURCE_FINGERPRINT = hashlib.sha256(b"owned-source").hexdigest()
SECOND_FINGERPRINT = hashlib.sha256(b"owned-source-2").hexdigest()


def prepared(output_path, **overrides):
    values = {
        "action_id": "pdf-action-1",
        "request_id": "pdf-request-1",
        "operation": "extract_pages",
        "source_fingerprints": (SOURCE_FINGERPRINT,),
        "output_path": str(output_path),
        "page_selection": (PageSelection(0, (1, 2)),),
        "current_state": {"page_count": 3, "private": "runtime only"},
        "expected_state": {"page_count": 2},
        "destructive": False,
        "reversible": True,
        "requires_approval": True,
        "verification_plan": {"method": "pdf_page_readback"},
        "rollback_plan": {"strategy": "delete_owned_output_if_unchanged"},
        "prepared_at": "2026-08-04T09:00:00+09:00",
    }
    values.update(overrides)
    return PreparedFileAction(**values)


class FileActionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-file-action-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"%PDF-owned-source")
        self.output = self.root / "output.pdf"

    def test_prepared_action_round_trip_is_json_serializable(self):
        original = prepared(self.output)

        encoded = json.dumps(original.to_dict(), ensure_ascii=False)
        restored = PreparedFileAction.from_dict(json.loads(encoded))

        self.assertEqual(original, restored)

    def test_evidence_excludes_path_and_runtime_state_content(self):
        action = prepared(self.output)

        encoded = json.dumps(action.to_evidence_dict(), ensure_ascii=False)

        self.assertNotIn(str(self.output), encoded)
        self.assertNotIn("runtime only", encoded)
        self.assertNotIn('"output_path"', encoded)
        self.assertIn("output_path_fingerprint", encoded)

    def test_every_pdf_file_action_requires_approval(self):
        with self.assertRaisesRegex(FileActionContractError, "require user approval"):
            prepared(self.output, requires_approval=False)

    def test_merge_requires_at_least_two_sources(self):
        with self.assertRaisesRegex(FileActionContractError, "at least two"):
            prepared(self.output, operation="merge_documents")

    def test_merge_accepts_ordered_source_page_selections(self):
        action = prepared(
            self.output,
            operation="merge_documents",
            source_fingerprints=(SOURCE_FINGERPRINT, SECOND_FINGERPRINT),
            page_selection=(PageSelection(1, (2,)), PageSelection(0, (1,))),
        )

        self.assertEqual((1, 0), tuple(item.source_index for item in action.page_selection))

    def test_page_selection_rejects_out_of_range_source(self):
        with self.assertRaisesRegex(FileActionContractError, "out of range"):
            prepared(self.output, page_selection=(PageSelection(1, (1,)),))

    def test_output_policy_accepts_new_sibling_path_without_writing(self):
        decision = evaluate_pdf_output_path((str(self.source),), str(self.output))

        self.assertEqual(os.path.abspath(self.output), decision.canonical_output_path)
        self.assertFalse(decision.overwrite)
        self.assertFalse(decision.backup_required)
        self.assertTrue(decision.requires_confirmation)
        self.assertFalse(self.output.exists())

    def test_output_policy_blocks_source_overwrite_even_when_requested(self):
        with self.assertRaisesRegex(OutputPathPolicyError, "overwrite a source"):
            evaluate_pdf_output_path(
                (str(self.source),),
                str(self.source),
                overwrite=True,
            )

    def test_existing_output_requires_explicit_overwrite(self):
        self.output.write_bytes(b"%PDF-existing-output")

        with self.assertRaisesRegex(OutputPathPolicyError, "already exists"):
            evaluate_pdf_output_path((str(self.source),), str(self.output))

        decision = evaluate_pdf_output_path(
            (str(self.source),),
            str(self.output),
            overwrite=True,
        )
        self.assertTrue(decision.backup_required)

    def test_output_policy_blocks_network_and_non_pdf_paths(self):
        with self.assertRaisesRegex(OutputPathPolicyError, "absolute local path"):
            evaluate_pdf_output_path((str(self.source),), r"\\server\share\out.pdf")
        with self.assertRaisesRegex(OutputPathPolicyError, r"\.pdf extension"):
            evaluate_pdf_output_path((str(self.source),), str(self.root / "out.txt"))

    def test_output_policy_blocks_missing_source(self):
        with self.assertRaisesRegex(OutputPathPolicyError, "existing local file"):
            evaluate_pdf_output_path(
                (str(self.root / "missing.pdf"),),
                str(self.output),
            )

    def test_output_suggestion_skips_existing_names(self):
        first = self.root / "source_split.pdf"
        first.write_bytes(b"%PDF-existing")

        suggestion = suggest_pdf_output_path(str(self.source), "split")

        self.assertEqual(str(self.root / "source_split_2.pdf"), suggestion)
        self.assertFalse(Path(suggestion).exists())

    def test_output_suggestion_accepts_safe_korean_suffix(self):
        suggestion = suggest_pdf_output_path(str(self.source), "분할")

        self.assertEqual(str(self.root / "source_분할.pdf"), suggestion)


if __name__ == "__main__":
    unittest.main()
