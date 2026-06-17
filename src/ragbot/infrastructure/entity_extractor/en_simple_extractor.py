"""EnSimpleExtractor — Lightweight English entity extractor.

Pure-stdlib heuristic — no spaCy / transformers dependency. Picks:

1. **Capitalized multi-word spans** — sequences of two-or-more
   adjacent tokens whose first character is uppercase
   (``"New York"``, ``"Acme Corp"``, ``"Open AI"``). These are
   typically named entities (proper nouns / org names / city names)
   in English and form excellent BM25 anchors.

2. **All-caps tokens of length >= 2** — brand-style identifiers
   (``"USA"``, ``"NASA"``, ``"ABC123"``).

3. **Numeric tokens** — phone numbers, dates, IDs (``"0901234567"``,
   ``"2024"``, ``"v3.5"``). Numerics are rarely paraphrased away by
   LLM rewriters but we emit them anyway as a BM25 verbatim anchor
   for robustness.

Vertical-agnostic: NO industry / brand / product literals baked in.
The same heuristic serves all English-language tenants regardless of
domain.

Limitations (acceptable for a lightweight strategy):
- Capital-at-sentence-start false positives (``"How are you"`` → ``"How"``).
  Mitigated by requiring **two-or-more** adjacent capitalised tokens
  for the multi-word path; single-word capitals only emitted via the
  all-caps path.
- No POS tagging — unlike ``ViUnderthesseaExtractor``, we cannot tell
  ``Apple`` (org) from ``apple`` (fruit) when both appear capitalised.
  Bot owners requiring full English NER should swap in a future
  ``EnSpacyExtractor`` strategy via the registry without touching
  any orchestrator code.
"""

from __future__ import annotations

import re
from typing import Iterable

import structlog

logger = structlog.get_logger(__name__)


# Languages this strategy serves. Caller (registry / DI) routes by the
# bot's ``language`` field; we additionally guard inside ``extract`` so
# a misconfig doesn't blow up the call.
_EN_LANGUAGES: frozenset[str] = frozenset({"en"})


# Token splitter — keep alphanumerics + a few internal-punctuation
# characters (period for ``v3.5``, hyphen for ``co-op``). Whitespace
# always splits.
_TOKEN_RE: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")

# Capitalised-token detector (first char is ASCII uppercase letter).
_CAP_RE: re.Pattern[str] = re.compile(r"^[A-Z][A-Za-z0-9.\-]*$")

# All-caps token detector (>= 2 chars, all uppercase or digits).
_ALL_CAPS_RE: re.Pattern[str] = re.compile(r"^[A-Z0-9][A-Z0-9.\-]+$")

# Numeric-with-optional-decimal/version detector.
_NUMERIC_RE: re.Pattern[str] = re.compile(r"^\d[\d.\-]*$")


def _stitch_capitalised(tokens: list[str]) -> list[str]:
    """Stitch contiguous-capitalised runs of length >= 2.

    Single capitalised tokens are intentionally NOT emitted from this
    pass (too noisy due to sentence-start capitals). They can still
    be picked up by the all-caps pass when applicable.
    """
    out: list[str] = []
    current: list[str] = []
    for tok in tokens:
        if _CAP_RE.match(tok):
            current.append(tok)
        else:
            if len(current) >= 2:
                out.append(" ".join(current))
            current = []
    if len(current) >= 2:
        out.append(" ".join(current))
    return out


def _collect_all_caps(tokens: Iterable[str]) -> list[str]:
    """Emit tokens that are all-caps (acronyms / brand-codes)."""
    return [t for t in tokens if _ALL_CAPS_RE.match(t)]


def _collect_numerics(tokens: Iterable[str]) -> list[str]:
    """Emit numeric tokens (phone, ID, year, version)."""
    return [t for t in tokens if _NUMERIC_RE.match(t)]


def _dedup_preserve_order(items: Iterable[str]) -> list[str]:
    """Case-fold dedup preserving first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        if not raw:
            continue
        norm = " ".join(raw.split()).strip()
        key = norm.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


class EnSimpleExtractor:
    """Lightweight English entity extractor (regex heuristics, zero deps)."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "en_simple"

    async def extract(self, query: str, *, language: str) -> list[str]:
        if not query or not query.strip():
            return []
        if language not in _EN_LANGUAGES:
            return []
        tokens = _TOKEN_RE.findall(query)
        if not tokens:
            return []
        # Order matters: multi-word capitalised first (highest signal),
        # then all-caps acronyms, then numerics. Dedup preserves first.
        merged = _dedup_preserve_order(
            [
                *_stitch_capitalised(tokens),
                *_collect_all_caps(tokens),
                *_collect_numerics(tokens),
            ]
        )
        return merged


__all__ = ["EnSimpleExtractor"]
