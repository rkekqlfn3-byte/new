import unittest

from engine.pdf import (
    PdfReadError,
    PdfReadErrorCode,
    normalize_page_selection,
)


class PdfPageSelectionTests(unittest.TestCase):
    def test_none_and_all_select_every_page(self):
        expected = (1, 2, 3, 4)

        self.assertEqual(expected, normalize_page_selection(None, 4))
        self.assertEqual(expected, normalize_page_selection("all", 4))
        self.assertEqual(expected, normalize_page_selection("*", 4))

    def test_text_ranges_are_sorted_and_deduplicated(self):
        self.assertEqual(
            (1, 2, 3, 4, 5),
            normalize_page_selection("5, 1~3, 2-4", 6),
        )

    def test_integer_and_iterable_are_normalized(self):
        self.assertEqual((2,), normalize_page_selection(2, 3))
        self.assertEqual((1, 2, 3), normalize_page_selection([3, 1, 2, 2], 3))

    def test_descending_and_out_of_range_pages_are_rejected(self):
        for selection in ("3-1", "0", "4", [1, 0], [1.5]):
            with self.subTest(selection=selection):
                with self.assertRaises(PdfReadError) as caught:
                    normalize_page_selection(selection, 3)
                self.assertEqual(
                    PdfReadErrorCode.PAGE_SELECTION_INVALID,
                    caught.exception.code,
                )

    def test_empty_document_allows_only_implicit_empty_selection(self):
        self.assertEqual((), normalize_page_selection(None, 0))
        with self.assertRaises(PdfReadError):
            normalize_page_selection("all", 0)

    def test_error_evidence_is_content_free(self):
        error = PdfReadError(
            PdfReadErrorCode.PASSWORD_REQUIRED,
            "private-file-name.pdf needs a password",
            page_number=2,
        )

        evidence = error.to_evidence_dict()

        self.assertEqual("needs_input", error.outcome)
        self.assertEqual("password_required", evidence["code"])
        self.assertEqual(2, evidence["page_number"])
        self.assertNotIn("message", evidence)


if __name__ == "__main__":
    unittest.main()
