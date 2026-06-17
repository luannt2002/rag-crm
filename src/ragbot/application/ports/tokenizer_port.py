"""Tokenizer Protocol — Strategy Pattern for per-language tokenization.

Strategy port for swap-able tokenizers (Vietnamese underthesea, simple
whitespace+punctuation for EN/JP/KO/ZH/AR/TH fallback, future locale-
aware variants).

Default is :class:`NullTokenizer` (whitespace). Heavy adapters (underthesea
for VN, etc.) opt in via the language registry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenizerPort(Protocol):
    """Strategy interface for per-language tokenisers.

    Implementations must:
    - NOT raise on empty / whitespace-only input — return ``[]`` and ``0``.
    - Be deterministic (same input → same output) for a given process.
    - Avoid any hot-path I/O (cloud calls, disk reads beyond first init).
    """

    def tokenize(self, text: str) -> list[str]:
        """Split *text* into tokens for downstream use (BM25, length-budget,
        entity windowing). Returns an empty list for empty / whitespace input.
        """
        ...

    def count_tokens(self, text: str) -> int:
        """Return the token count for *text* — equivalent to
        ``len(self.tokenize(text))``. Implementations may override with a
        cheaper path (e.g. tiktoken byte-pair count) when available.
        """
        ...

    def get_language(self) -> str:
        """Return the ISO-639-1 language code this tokenizer targets
        (``"vi"`` / ``"en"`` / ``"_simple"`` for the catch-all fallback).
        """
        ...


__all__ = ["TokenizerPort"]
