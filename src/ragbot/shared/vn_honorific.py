"""Vietnamese honorific detection — emit a label, NEVER override answer.

Bot owner consumes the emitted label inside their own ``system_prompt`` clause
to set tone (e.g. address user as "anh" / "chị"). The platform stays neutral:
this module never injects text into the LLM prompt and never rewrites the
answer (Quality Gate #10).

Domain-neutral — pure VN language particles, no brand / industry vocabulary.
"""
from __future__ import annotations

import re

from ragbot.shared.constants import VN_HONORIFIC_LABELS

# Word-boundary scan over the whole text. ``\w`` in Python's ``re`` (no
# ``re.ASCII`` flag) treats Vietnamese accented letters as word characters,
# so ``\b`` correctly rejects substring matches like ``ngân hàng`` (no token
# boundary before ``hàng`` because ``g`` is a word char). Sorted longest-first
# keeps the alternation deterministic; with ``\b`` boundaries on both sides
# every label is a standalone token so ordering is for readability only.
_HONORIFIC_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(VN_HONORIFIC_LABELS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE | re.UNICODE,
)


def detect_honorific(text: str) -> str | None:
    """Return the first VN honorific label found in ``text`` or ``None``.

    Output is one of ``VN_HONORIFIC_LABELS`` in canonical lowercase form.
    First-occurrence semantics: if the user wrote "Chào anh, em hỏi tí" the
    leading addressee ("anh") wins — that's who they're speaking to.

    Returns ``None`` for empty input or text without a standalone honorific
    token (substring matches like "ngân hàng" are rejected by ``\\b``).
    """
    if not text:
        return None
    match = _HONORIFIC_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1).lower()
