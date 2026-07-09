"""Deterministic brand-scope denial detector — OBSERVE by default (002-B1).

Finds the brand a bot claims it does NOT distribute, so the caller can check
that claim against the structured index. A "chưa phân phối hãng <Brand>" answer
is a FALSE refusal when the corpus actually stocks that brand (truth-audit
step20: BrandX denied while 50+ BrandX SKUs exist). numeric-fidelity cannot see
this — the claim carries no number.

Pure string/regex — no I/O, no DB, no model. Domain/language-neutral by
construction:
  * ``negation_phrases`` are INJECTED (from config / language pack), never
    hardcoded here — a locale with no phrases makes the detector a silent no-op.
  * the brand token is extracted by PROPER-NOUN shape, not a brand vocabulary.

This module only DETECTS the denied brand; whether it is actually stocked (and
therefore whether the answer is a false refusal) is the caller's DSI query.
"""
from __future__ import annotations

import re

# A brand token: starts with an uppercase letter (incl. Vietnamese), ≥3 chars,
# letters/digits only. A bare size/number ("205") cannot match — it needs a
# leading letter. Shape-only, no brand literal.
_BRAND_TOKEN_RE = re.compile(r"[A-ZÀ-Ỹ][A-Za-zÀ-ỹ0-9]{2,}")

# How many characters after a negation phrase to scan for the brand token — a
# brand name follows the phrase closely ("chưa phân phối hãng BrandX ạ").
_SCAN_WINDOW = 48


def detect_denied_brand(
    answer: str,
    *,
    negation_phrases: tuple[str, ...] | list[str],
    question: str = "",
) -> str | None:
    """Return the brand token the answer claims is NOT distributed, else None.

    ``negation_phrases``: locale phrases that signal a distribution denial
    (e.g. "chưa phân phối hãng"); supplied by the caller from config. Empty →
    None (silent no-op).

    When several proper-noun tokens follow the phrase, prefer the one that also
    appears in ``question`` (the brand the user actually asked about).
    """
    if not answer or not negation_phrases:
        return None
    low = answer.lower()
    q_low = (question or "").lower()
    for phrase in negation_phrases:
        p = (phrase or "").strip().lower()
        if not p:
            continue
        idx = low.find(p)
        if idx < 0:
            continue
        after = answer[idx + len(p): idx + len(p) + _SCAN_WINDOW]
        candidates = [m.group(0) for m in _BRAND_TOKEN_RE.finditer(after)]
        if not candidates:
            continue
        if q_low:
            for tok in candidates:
                if tok.lower() in q_low:
                    return tok
        return candidates[0]
    return None


__all__ = ["detect_denied_brand"]
