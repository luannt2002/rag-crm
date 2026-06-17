"""Text utilities for Vietnamese query normalization."""
from __future__ import annotations

import re
from typing import Iterable

# Default Vietnamese filler tokens that hurt BM25 AND-of-N tsquery match.
# Bot owners override per-tenant via system_config key 'vn_filler_tokens'
# (JSON array). New languages = add config row, no code change.
_DEFAULT_VN_FILLER_TOKENS: tuple[str, ...] = (
    "nói gì", "nói về gì", "có gì", "có những gì",
    "là sao", "ra sao", "thế nào", "như thế nào",
    "là gì", "có không", "không", "ạ", "nhé", "đi",
    "cho", "với", "ơi", "à", "ư", "ư?",
)


def strip_vn_filler_tokens(
    query: str,
    *,
    filler_tokens: Iterable[str] | None = None,
) -> str:
    """Return query with VN filler tokens removed.

    Used for BM25 sparse branch where ``websearch_to_tsquery`` builds
    AND-of-N tokens — fillers like 'nói gì' force every chunk to also
    contain those tokens, dropping recall to ~0 for natural queries.
    Dense/embedding branch keeps the original query (semantic value).

    Empty/whitespace input returns ''. Tokens are matched case-insensitive
    on word boundaries. Returns trimmed whitespace-normalised string.

    Examples:
        strip_vn_filler_tokens('Chương 3 nói gì') == 'Chương 3'
        strip_vn_filler_tokens('Điều 55 ra sao ạ') == 'Điều 55'
        strip_vn_filler_tokens('Hello world') == 'Hello world'
    """
    if not query or not isinstance(query, str):
        return ""
    tokens = tuple(filler_tokens) if filler_tokens is not None else _DEFAULT_VN_FILLER_TOKENS
    out = query
    # Apply longest tokens first to handle 'nói về gì' before 'nói'.
    for tok in sorted(tokens, key=len, reverse=True):
        if not tok:
            continue
        pattern = re.compile(
            rf"(?<![\w]){re.escape(tok)}(?![\w])",
            re.IGNORECASE,
        )
        out = pattern.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


__all__ = ["strip_vn_filler_tokens"]
