import unittest

from engine.pdf import (
    PdfIntentError,
    PdfIntentKind,
    looks_like_pdf_command,
    parse_pdf_intent,
)


class PdfIntentParserTests(unittest.TestCase):
    def test_read_only_intents_are_deterministic(self):
        cases = {
            "이 PDF 요약해줘": PdfIntentKind.SUMMARY,
            "3페이지 설명해줘": PdfIntentKind.EXPLAIN,
            "총 페이지 수 알려줘": PdfIntentKind.PAGE_COUNT,
            "목차 보여줘": PdfIntentKind.TABLE_OF_CONTENTS,
            "표만 추출해줘": PdfIntentKind.TABLE_EXTRACT,
            "이 PDF로 보고서 만들어줘": PdfIntentKind.REPORT,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertIs(expected, parse_pdf_intent(command).kind)

    def test_mutating_intents_are_marked_for_pdf4_confirmation(self):
        cases = {
            "2페이지에서 분할해줘": PdfIntentKind.SPLIT,
            "두 PDF를 병합해줘": PdfIntentKind.MERGE,
            "3페이지를 오른쪽으로 회전해줘": PdfIntentKind.ROTATE,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                intent = parse_pdf_intent(command)
                self.assertIs(expected, intent.kind)
                self.assertTrue(intent.requires_confirmation)
                self.assertEqual("PDF-4", intent.implementation_stage)

    def test_search_query_is_runtime_only_and_evidence_is_content_free(self):
        intent = parse_pdf_intent('이 PDF에서 "계약 해지" 찾아줘')

        self.assertIs(PdfIntentKind.SEARCH, intent.kind)
        self.assertEqual("계약 해지", intent.query)
        evidence = intent.to_evidence_dict()
        self.assertNotIn("query", evidence)
        self.assertEqual(5, evidence["query_length"])

        unquoted = parse_pdf_intent("이 PDF 3페이지에서 계약 해지 찾아줘")
        self.assertEqual("계약 해지", unquoted.query)

    def test_search_without_query_needs_clarification(self):
        with self.assertRaises(PdfIntentError) as caught:
            parse_pdf_intent("이 PDF에서 검색해줘")

        self.assertEqual("pdf_search_query_missing", caught.exception.code)

    def test_unknown_intent_needs_clarification(self):
        with self.assertRaises(PdfIntentError) as caught:
            parse_pdf_intent("이 PDF로 뭔가 해줘")

        self.assertEqual("pdf_intent_missing", caught.exception.code)

    def test_connected_pdf_context_does_not_steal_unrelated_web_search(self):
        self.assertFalse(looks_like_pdf_command("구글에서 고양이 찾아줘", connected=True))
        self.assertTrue(looks_like_pdf_command("이거에서 계약 찾아줘", connected=True))
        self.assertTrue(looks_like_pdf_command("3페이지 설명해줘", connected=True))
        self.assertTrue(looks_like_pdf_command("목차 보여줘", connected=True))

    def test_explicit_pdf_is_candidate_before_connection(self):
        self.assertTrue(looks_like_pdf_command("PDF에서 검색해줘", connected=False))
        self.assertFalse(looks_like_pdf_command("3페이지 설명해줘", connected=False))

    def test_office_export_to_pdf_is_not_an_input_pdf_command(self):
        for command in (
            "한글 현재 문서를 PDF로 저장해줘",
            "이 워드 문서를 PDF 파일로 내보내줘",
            "파워포인트를 PDF 형식으로 변환해줘",
        ):
            with self.subTest(command=command):
                self.assertFalse(looks_like_pdf_command(command, connected=True))


if __name__ == "__main__":
    unittest.main()
