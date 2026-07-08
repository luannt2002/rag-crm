"""Deterministic NON-numeric grounding check — scope-over-extension detector.

Root cause (deep-analysis 2026-07-08): the answer-path deterministic guards are
number-only (``numeric_fidelity``) and false-DENIAL-only (``brand_scope``), so a
false AFFIRMATIVE non-numeric claim passes un-gated. The clearest reproducible
class is a SCOPE over-extension: a warranty chunk scoped to "lốp xe du lịch (PCR)"
answered as "áp dụng cho tất cả các loại lốp, bao gồm cả lốp xe tải" — the object
"xe tải" is absent from the served context.

This module extracts the OBJECT that an answer's scope-affirmation phrase affirms
(the words after the phrase) and checks whether its salient content tokens appear
in the served context. A salient token absent from EVERY served passage means the
answer affirmed something the corpus does not contain → a likely over-extension.

Domain / language neutral, ZERO vocab, ZERO model:
  * affirmation phrases are injected from config (per-locale), never hardcoded
    here — the code default is empty, so the check is a silent no-op until an
    operator seeds phrases (same governed contract as ``brand_scope``);
  * tokens are compared by SHAPE (word runs) with diacritic-insensitive
    normalization — no stopword list, no brand/service literal.

OBSERVE-only by design: the caller logs the verdict; it never modifies the answer
(sacred #10). Blocking is a separate owner-gated step after FP is measured — this
mirrors how ``numeric_fidelity`` and ``brand_scope`` shipped (observe → measure →
block). SOTA basis: RAGAS Faithfulness / FactScore / AIS (every claim must trace
to a served passage); this is the cheap deterministic recall pre-filter tier.
"""
from __future__ import annotations

import re
import unicodedata

# A content token: a run of 3+ letters (Unicode, diacritics included). Digits and
# 1-2 char glue are excluded — a scope object is named by words, not numbers, and
# very short tokens are too ambiguous to attribute.
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# Cap how many words after the phrase count as the affirmed object — a scope
# object is a short noun phrase ("lốp xe tải"), not a whole clause.
_OBJECT_WINDOW_WORDS = 6


def _norm(text: str) -> str:
    """Diacritic-insensitive, case-insensitive normalization for membership.

    NFKD-fold + strip combining marks + casefold, so "Xe Tải" == "xe tai" and a
    served "xe tải" matches an answer "Xe Tải". Domain-neutral; no language table.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def _tokens(normalized_text: str) -> list[str]:
    """Content tokens from ALREADY-normalized text."""
    return _WORD_RE.findall(normalized_text)


def detect_scope_overextension(
    answer: str,
    served_texts: list[str],
    affirmation_phrases: tuple[str, ...] | list[str],
) -> list[str]:
    """Return the salient object tokens an answer AFFIRMS but the served context
    does NOT contain.

    For each affirmation phrase found in ``answer``, take the next
    ``_OBJECT_WINDOW_WORDS`` content tokens (the affirmed object) and keep those
    absent from the union of ``served_texts``. A non-empty result = the answer
    scoped an affirmation to an object with no support in the served passages.

    Empty when: no phrases configured, no phrase occurs, no served context, or
    every affirmed object token is grounded. Pure + deterministic (no model/IO).
    """
    if not affirmation_phrases or not answer:
        return []
    norm_answer = _norm(answer)
    served_vocab: set[str] = set()
    for s in served_texts or []:
        served_vocab.update(_tokens(_norm(s)))
    if not served_vocab:
        # No served context to attribute against → cannot judge; stay silent
        # (an empty-context answer is a different class handled by refuse/empty).
        return []

    unsupported: list[str] = []
    seen: set[str] = set()
    for phrase in affirmation_phrases:
        p = _norm(str(phrase))
        if not p:
            continue
        start = 0
        while True:
            idx = norm_answer.find(p, start)
            if idx < 0:
                break
            after = norm_answer[idx + len(p):]
            start = idx + len(p)
            for tok in _tokens(after)[:_OBJECT_WINDOW_WORDS]:
                if tok in served_vocab or tok in seen:
                    continue
                seen.add(tok)
                unsupported.append(tok)
    return unsupported
