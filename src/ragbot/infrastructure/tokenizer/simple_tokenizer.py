# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: infrastructure/tokenizer/ never wired.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """SimpleTokenizer — whitespace + punctuation split.

# Catch-all fallback that works for English, Chinese, Japanese, Korean,
# Arabic, Thai and any other language where the platform does not ship a
# locale-specific strategy. Multi-industry / multi-language safe default
# — no extra dependencies, no surprising side effects.
# """

# from __future__ import annotations

# import re

# Compiled once at import time. The character class matches Unicode word
# characters (letters, digits, ``_``) plus apostrophe / hyphen so common
# English contractions (``don't``, ``state-of-the-art``) survive intact.
# CJK / Arabic / Thai scripts have no native word boundary in plain text;
# downstream BM25 / length budgeting still benefits from the resulting
# whitespace-grouped tokens.
# _TOKEN_RE = re.compile(r"[\w'\-]+", re.UNICODE)


# class SimpleTokenizer:
#     """Whitespace + punctuation tokenizer — language-agnostic fallback."""

#     def __init__(self, *, language: str = "_simple", **_: object) -> None:
        # Accept arbitrary kwargs so the registry can pass language= safely.
#         self._language = language

#     def tokenize(self, text: str) -> list[str]:
#         if not text or not text.strip():
#             return []
#         return _TOKEN_RE.findall(text)

#     def count_tokens(self, text: str) -> int:
#         if not text or not text.strip():
#             return 0
        # Iterate without materialising the list — saves an alloc on big inputs.
#         return sum(1 for _ in _TOKEN_RE.finditer(text))

#     def get_language(self) -> str:
#         return self._language


# __all__ = ["SimpleTokenizer"]
