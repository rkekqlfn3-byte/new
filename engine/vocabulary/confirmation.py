"""The one place cancel wording is defined.

Sixteen call sites each carried their own cancel alias list and they had
drifted: `하지마` cancelled an Excel clarification but not the PDF external
transfer confirmation, and `아니오` — the standard spelling — matched no
confirmation option at all.  The worst possible direction, because the PDF
confirmation is the one that sends document content to an external service.

``PendingConfirmationManager`` merges this set into every option marked
``cancel``, so an option author cannot omit a way to say no.
"""

from __future__ import annotations

CANCEL_ALIASES = frozenset({
    "아니",
    "아니오",
    "아니요",
    "그만",
    "하지마",
    "하지 마",
    "취소",
    "취소해",
    "버려",
    "됐어",
    "틀렸어",
    "ㄴㄴ",
    "ㄴ",
})

APPROVAL_ALIASES = frozenset({
    "예",
    "네",
    "응",
    "어",
    "그래",
    "맞아",
    "ㅇㅇ",
    "ㅇ",
    "y",
    "yes",
})

__all__ = ["APPROVAL_ALIASES", "CANCEL_ALIASES"]
