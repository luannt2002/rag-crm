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

# """NullTokenizer — Null Object pattern for the tokenizer strategy.

# Default selection when an unknown / missing language is requested. Delegates
# internally to :class:`SimpleTokenizer` so callers always get a usable token
# list — no surprise ``NotImplementedError`` on the hot path. Unknown languages
# should still produce reasonable BM25-friendly tokens rather than blowing up
# the ingest pipeline.

# Reports its own ``language`` as ``"_null"`` so observability dashboards can
# distinguish a deliberate fallback from a real strategy match.
# """

# from __future__ import annotations

# from ragbot.infrastructure.tokenizer.simple_tokenizer import SimpleTokenizer


# class NullTokenizer:
#     """Catch-all fallback — defers to SimpleTokenizer."""

#     def __init__(self, *, language: str = "_null", **_: object) -> None:
#         self._language = language
#         self._inner = SimpleTokenizer(language=language)

#     def tokenize(self, text: str) -> list[str]:
#         return self._inner.tokenize(text)

#     def count_tokens(self, text: str) -> int:
#         return self._inner.count_tokens(text)

#     def get_language(self) -> str:
#         return self._language


# __all__ = ["NullTokenizer"]
