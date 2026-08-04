import unittest

from engine.pdf import (
    PdfGroundedAnswerService,
    PdfTextChunk,
    build_transfer_plan,
)


def chunk(index, page, text):
    return PdfTextChunk.from_text(index, page, text)


class PdfGroundedAnswerTests(unittest.TestCase):
    def test_transfer_plan_contains_counts_and_pages_but_no_text(self):
        chunks = (chunk(1, 1, "Alpha"), chunk(2, 3, "Beta"))

        plan = build_transfer_plan(chunks, provider="openai", purpose="summary")
        encoded = repr(plan.to_dict())

        self.assertEqual((1, 3), plan.page_numbers)
        self.assertEqual(9, plan.character_count)
        self.assertNotIn("Alpha", encoded)
        self.assertNotIn("Beta", encoded)

    def test_single_batch_answer_keeps_only_valid_provider_citations(self):
        calls = []

        def provider(system, prompt):
            calls.append((system, prompt))
            return "첫 페이지 핵심입니다 [p.1]. 존재하지 않는 인용 [p.99]"

        service = PdfGroundedAnswerService(provider)
        answer = service.generate(
            (chunk(1, 1, "Alpha"), chunk(2, 2, "Beta")),
            provider="openai",
            purpose="summary",
            request_text="요약해줘",
        )

        self.assertEqual((1,), answer.citation_pages)
        self.assertEqual(1, answer.provider_call_count)
        self.assertIn("근거 페이지: p.1", answer.display_text)
        self.assertIn("[p.1]", calls[0][1])
        self.assertIn("[p.2]", calls[0][1])

    def test_missing_provider_citation_falls_back_to_transferred_pages(self):
        service = PdfGroundedAnswerService(lambda _system, _prompt: "요약 결과")

        answer = service.generate(
            (chunk(1, 2, "Only page"),),
            provider="gemini",
            purpose="explain",
            request_text="설명해줘",
        )

        self.assertEqual((2,), answer.citation_pages)

    def test_large_document_uses_map_reduce_without_losing_page_labels(self):
        calls = []

        def provider(_system, prompt):
            calls.append(prompt)
            if prompt.startswith("요청:") and "부분 분석" in prompt:
                return "통합 결과 [p.1] [p.2]"
            return "부분 결과 [p.1] [p.2]"

        chunks = (
            chunk(1, 1, "A" * 6_000),
            chunk(2, 2, "B" * 6_000),
            chunk(3, 3, "C" * 6_000),
        )
        answer = PdfGroundedAnswerService(provider).generate(
            chunks,
            provider="openai",
            purpose="summary",
            request_text="전체를 요약해줘",
        )

        self.assertGreater(answer.provider_call_count, 1)
        self.assertEqual(answer.provider_call_count, len(calls))
        self.assertEqual(18_000, answer.transfer.character_count)
        self.assertEqual(3, len(answer.transfer.page_numbers))

    def test_evidence_excludes_answer_and_document_text(self):
        answer = PdfGroundedAnswerService(
            lambda _system, _prompt: "Private answer [p.1]"
        ).generate(
            (chunk(1, 1, "Private document"),),
            provider="openai",
            purpose="summary",
            request_text="요약",
        )

        encoded = repr(answer.to_evidence_dict())
        self.assertNotIn("Private answer", encoded)
        self.assertNotIn("Private document", encoded)


if __name__ == "__main__":
    unittest.main()
