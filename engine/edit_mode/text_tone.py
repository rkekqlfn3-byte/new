"""Deterministic Korean sentence-ending tone labels without storing text.

The classifier looks only at how sentences end.  It returns one coarse label
and never keeps the input, so callers may store the label next to a digest
without retaining document content.  Meaning, vocabulary, and politeness
nuances beyond the ending pattern are deliberately out of scope.
"""

from __future__ import annotations

import re


TONE_FORMAL = "formal"
TONE_FRIENDLY = "friendly"
TONE_PLAIN = "plain"
TONE_MIXED = "mixed"
TONE_UNKNOWN = "unknown"

TONE_LABELS = frozenset({
    TONE_FORMAL, TONE_FRIENDLY, TONE_PLAIN, TONE_MIXED, TONE_UNKNOWN,
})

_SENTENCE_SPLIT = re.compile(r"[.!?…。\r\n]+")
_TRAILING_NOISE = re.compile(r"[\s\"'“”‘’)\]}>~♪‥,;:·-]+$")

# 합쇼체: ~합니다/입니다/습니다/됩니다, ~습니까/ㅂ니까, ~십시오.
_FORMAL_ENDINGS = ("니다", "니까", "십시오", "시오")
# 해요체: every polite-informal ending finishes with 요/죠 once noise is removed.
_FRIENDLY_ENDINGS = ("요", "죠")
# 해라체와 개조식 명사형 어미.
_PLAIN_ENDINGS = ("다", "함", "됨", "음", "임")

_DOMINANCE_RATIO = 0.7


def _sentence_label(fragment: str) -> str | None:
    text = _TRAILING_NOISE.sub("", str(fragment or "").strip())
    if len(text) < 2:
        return None
    for ending in _FORMAL_ENDINGS:
        if text.endswith(ending):
            return TONE_FORMAL
    for ending in _FRIENDLY_ENDINGS:
        if text.endswith(ending):
            return TONE_FRIENDLY
    for ending in _PLAIN_ENDINGS:
        if text.endswith(ending):
            return TONE_PLAIN
    return None


def classify_text_tone(text) -> str:
    """Label text as formal/friendly/plain/mixed/unknown by sentence endings."""
    value = str(text or "").strip()
    if len(value) < 5:
        return TONE_UNKNOWN
    counts: dict[str, int] = {}
    for fragment in _SENTENCE_SPLIT.split(value):
        label = _sentence_label(fragment)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    labeled = sum(counts.values())
    if labeled == 0:
        return TONE_UNKNOWN
    leader, leader_count = max(counts.items(), key=lambda item: item[1])
    if leader_count / labeled >= _DOMINANCE_RATIO:
        return leader
    return TONE_MIXED
