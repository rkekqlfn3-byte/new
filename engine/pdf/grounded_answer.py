"""Bounded external-AI synthesis that preserves PDF page citations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from engine.pdf.analysis import PdfTextChunk, citation_label

MAX_PDF_AI_BATCH_CHARS = 12_000
MAX_PDF_AI_REQUEST_CHARS = 1_000
MAX_PDF_AI_RESPONSE_CHARS = 100_000
_CITATION_RE = re.compile(r"(?:\[?p\.?\s*(\d+)\]?|\[(\d+)\s*페이지\])", re.IGNORECASE)


@dataclass(frozen=True)
class PdfExternalTransferPlan:
    provider: str
    purpose: str
    page_numbers: tuple[int, ...]
    character_count: int
    chunk_count: int
    batch_count: int

    def __post_init__(self):
        provider = str(self.provider or "").casefold()
        if provider not in {"openai", "gemini"}:
            raise ValueError("Unsupported PDF AI provider.")
        purpose = str(self.purpose or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", purpose):
            raise ValueError("PDF AI purpose is invalid.")
        pages = tuple(self.page_numbers)
        if not pages or pages != tuple(sorted(set(pages))):
            raise ValueError("PDF AI transfer pages must be sorted and unique.")
        if any(type(page) is not int or page < 1 for page in pages):
            raise ValueError("PDF AI transfer page is invalid.")
        for value, label in (
            (self.character_count, "character count"),
            (self.chunk_count, "chunk count"),
            (self.batch_count, "batch count"),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"PDF AI transfer {label} must be positive.")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "page_numbers", pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "purpose": self.purpose,
            "page_numbers": list(self.page_numbers),
            "page_count": len(self.page_numbers),
            "character_count": self.character_count,
            "chunk_count": self.chunk_count,
            "batch_count": self.batch_count,
        }


@dataclass(frozen=True)
class PdfGroundedAnswer:
    answer: str
    citation_pages: tuple[int, ...]
    transfer: PdfExternalTransferPlan
    provider_call_count: int

    def __post_init__(self):
        answer = str(self.answer or "").strip()
        if not answer or "\x00" in answer or len(answer) > MAX_PDF_AI_RESPONSE_CHARS:
            raise ValueError("Grounded PDF answer is empty or exceeds its limit.")
        pages = tuple(self.citation_pages)
        if not pages or pages != tuple(sorted(set(pages))):
            raise ValueError("Grounded PDF citations must be sorted and unique.")
        if any(page not in self.transfer.page_numbers for page in pages):
            raise ValueError("Grounded PDF citation is outside the transferred pages.")
        if type(self.provider_call_count) is not int or self.provider_call_count < 1:
            raise ValueError("Grounded PDF provider call count must be positive.")
        object.__setattr__(self, "answer", answer)
        object.__setattr__(self, "citation_pages", pages)

    @property
    def display_text(self) -> str:
        return f"{self.answer}\n\n{citation_label(self.citation_pages)}"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citation_pages": list(self.citation_pages),
            "transfer": self.transfer.to_dict(),
            "provider_call_count": self.provider_call_count,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "answer_character_count": len(self.answer),
            "citation_pages": list(self.citation_pages),
            "transfer": self.transfer.to_dict(),
            "provider_call_count": self.provider_call_count,
        }


def _batches(chunks: tuple[PdfTextChunk, ...]) -> tuple[tuple[PdfTextChunk, ...], ...]:
    batches = []
    current = []
    size = 0
    for chunk in chunks:
        extra = len(chunk.text) + 20
        if current and size + extra > MAX_PDF_AI_BATCH_CHARS:
            batches.append(tuple(current))
            current = []
            size = 0
        current.append(chunk)
        size += extra
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def build_transfer_plan(
    chunks: tuple[PdfTextChunk, ...],
    *,
    provider: str,
    purpose: str,
) -> PdfExternalTransferPlan:
    if not chunks or any(not isinstance(chunk, PdfTextChunk) for chunk in chunks):
        raise ValueError("PDF AI transfer requires structured chunks.")
    return PdfExternalTransferPlan(
        provider=provider,
        purpose=purpose,
        page_numbers=tuple(sorted({chunk.page_number for chunk in chunks})),
        character_count=sum(len(chunk.text) for chunk in chunks),
        chunk_count=len(chunks),
        batch_count=len(_batches(chunks)),
    )


def _batch_text(chunks: tuple[PdfTextChunk, ...]) -> str:
    return "\n\n".join(f"[p.{chunk.page_number}]\n{chunk.text}" for chunk in chunks)


def _provider_text(value) -> str:
    if isinstance(value, dict):
        value = value.get("response") or value.get("message") or ""
    text = str(value or "").strip()
    if not text or "\x00" in text or len(text) > MAX_PDF_AI_RESPONSE_CHARS:
        raise ValueError("PDF AI provider returned an invalid response.")
    return text


def _citations(text: str, allowed_pages: tuple[int, ...]) -> tuple[int, ...]:
    allowed = set(allowed_pages)
    found = {
        int(first or second)
        for first, second in _CITATION_RE.findall(text)
        if int(first or second) in allowed
    }
    return tuple(sorted(found)) or allowed_pages


class PdfGroundedAnswerService:
    def __init__(self, provider_call: Callable[[str, str], Any]):
        if not callable(provider_call):
            raise TypeError("PDF grounded answer service requires a provider callable.")
        self._provider_call = provider_call

    def generate(
        self,
        chunks: tuple[PdfTextChunk, ...],
        *,
        provider: str,
        purpose: str,
        request_text: str,
    ) -> PdfGroundedAnswer:
        request = str(request_text or "").strip()
        if not request or "\x00" in request or len(request) > MAX_PDF_AI_REQUEST_CHARS:
            raise ValueError("PDF AI request is empty or exceeds its limit.")
        plan = build_transfer_plan(chunks, provider=provider, purpose=purpose)
        batches = _batches(chunks)
        system = (
            "제공된 PDF 발췌만 사용하세요. 발췌 밖의 사실을 추측하지 말고, "
            "각 핵심 주장 뒤에 [p.N] 형식의 근거 페이지를 붙이세요."
        )
        calls = 0
        if len(batches) == 1:
            prompt = f"요청: {request}\n\nPDF 발췌:\n{_batch_text(batches[0])}"
            answer = _provider_text(self._provider_call(system, prompt))
            calls = 1
        else:
            notes = []
            for batch in batches:
                prompt = (
                    f"최종 요청은 '{request}'입니다. 다음 발췌에서 관련 사실만 "
                    f"페이지 인용과 함께 추출하세요.\n\n{_batch_text(batch)}"
                )
                notes.append(_provider_text(self._provider_call(system, prompt)))
                calls += 1
            synthesis = (
                f"요청: {request}\n\n페이지 인용이 포함된 부분 분석:\n"
                + "\n\n".join(notes)
            )
            answer = _provider_text(self._provider_call(system, synthesis))
            calls += 1
        citations = _citations(answer, plan.page_numbers)
        return PdfGroundedAnswer(answer, citations, plan, calls)
