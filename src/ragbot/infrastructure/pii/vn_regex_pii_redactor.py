"""VnRegexPiiRedactor — light-weight VN-focused regex Strategy.

Patterns flow from :mod:`ragbot.shared.constants` per the zero-hardcode
rule. Each match is emitted as ``{"type": ..., "start": int, "end": int}``
where the offsets reference the *input* text. The redacted text replaces
each match with ``[<TYPE>]`` (e.g. ``[CCCD]``).

Overlap resolution
------------------
CCCD (12 digits) and VN phone (10–11 digits starting with ``0``) share the
same alphabet, so a CCCD beginning with ``0`` (``012345678901``) would also
match the PHONE regex on its 10-digit prefix. We collect every span
unconditionally then sort by ``(start, -length)`` so the **longer** span
wins at any given start offset. The single-pass emit-loop below skips any
span whose start is inside the previously-emitted region, which removes
the shorter PHONE prefix when a 12-digit CCCD covers it.

Space-separated variants (``"0123 4567 8901"``, ``"0901 234 567"``) are
covered by their own patterns so the human-friendly formats people paste
into chat are masked too. The contiguous patterns still run first via
priority/offset ordering, so a digit-only string hits the canonical type.
"""

from __future__ import annotations

import re

from ragbot.shared.constants import (
    PII_REGEX_API_KEY_GENERIC,
    PII_REGEX_API_KEY_PROVIDER,
    PII_REGEX_CCCD,
    PII_REGEX_CCCD_SPACED,
    PII_REGEX_CREDIT_CARD,
    PII_REGEX_DB_DSN,
    PII_REGEX_EMAIL,
    PII_REGEX_JWT,
    PII_REGEX_PHONE_VN,
    PII_REGEX_PHONE_VN_SPACED,
    PII_REGEX_VN_PLATE,
)


# Order is informational only — overlap resolution is offset+length based,
# not list-position based. Both contiguous and space-separated variants are
# emitted under the same canonical type code (CCCD / PHONE / etc.).
#
# Y4-2026-05-01: extended with high-impact secret patterns (API_KEY, JWT,
# DSN, CARD, VN_PLATE). Higher-specificity classes (DSN, JWT, API key
# shapes) come BEFORE generic numeric ones (CCCD, PHONE, CARD) so the
# (start, -length) sort prefers the specific type when offsets overlap.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DSN", re.compile(PII_REGEX_DB_DSN)),
    ("JWT", re.compile(PII_REGEX_JWT)),
    ("API_KEY", re.compile(PII_REGEX_API_KEY_PROVIDER)),
    ("API_KEY", re.compile(PII_REGEX_API_KEY_GENERIC)),
    ("CCCD", re.compile(PII_REGEX_CCCD)),
    ("CCCD", re.compile(PII_REGEX_CCCD_SPACED)),
    ("CARD", re.compile(PII_REGEX_CREDIT_CARD)),
    ("VN_PLATE", re.compile(PII_REGEX_VN_PLATE)),
    ("PHONE", re.compile(PII_REGEX_PHONE_VN)),
    ("PHONE", re.compile(PII_REGEX_PHONE_VN_SPACED)),
    ("EMAIL", re.compile(PII_REGEX_EMAIL)),
)


class VnRegexPiiRedactor:
    """Vietnamese-focused regex PII redactor."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "vn_regex"

    def redact(self, text: str) -> tuple[str, list[dict]]:
        if not text:
            return text, []

        # 1) Collect all spans against the original input first so the
        #    returned offsets always reference the unredacted text.
        entities: list[dict] = []
        for kind, pat in _PATTERNS:
            for m in pat.finditer(text):
                entities.append({
                    "type": kind,
                    "start": m.start(),
                    "end": m.end(),
                })

        # Sort by ``(start, -length)`` so the longer span at any given start
        # offset appears first. The downstream emit-loop drops any later span
        # whose start is inside the previously-emitted region, which is how
        # we stop a 10-digit PHONE prefix from masking a 12-digit CCCD that
        # happens to start with ``0`` (collision class). Equal-length spans
        # at identical offsets keep insertion order (Python's sort is
        # stable), which preserves the CCCD → PHONE → EMAIL priority from
        # ``_PATTERNS``.
        entities.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))

        # 2) Build redacted output via a single left-to-right pass over
        #    the entity list — avoids overlapping double-replace.
        if not entities:
            return text, []

        out_parts: list[str] = []
        cursor = 0
        emitted: list[dict] = []
        for ent in entities:
            if ent["start"] < cursor:
                # Overlaps with a previously emitted span (e.g. PHONE inside
                # CCCD digits) — skip the duplicate so we don't doubly-mask.
                continue
            out_parts.append(text[cursor:ent["start"]])
            out_parts.append(f"[{ent['type']}]")
            cursor = ent["end"]
            emitted.append(ent)
        out_parts.append(text[cursor:])

        return "".join(out_parts), emitted


__all__ = ["VnRegexPiiRedactor"]
