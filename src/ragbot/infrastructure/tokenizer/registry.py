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

# """Per-language tokenizer strategy registry — DI factory keyed by language.

# Adding a new language = add a file here and register it; no edits to
# ingest service or query graph. NEW callers opt in by accepting a
# ``TokenizerPort`` argument and resolving via ``build_tokenizer``.
# Legacy callers still use ``vi_tokenizer.segment_vi_compounds`` directly.
# """

# from __future__ import annotations

# from typing import TYPE_CHECKING

# import structlog

# from ragbot.infrastructure.tokenizer.null_tokenizer import NullTokenizer
# from ragbot.infrastructure.tokenizer.simple_tokenizer import SimpleTokenizer
# from ragbot.infrastructure.tokenizer.vi_tokenizer import ViTokenizer
# from ragbot.shared.constants import DEFAULT_LANGUAGE

# if TYPE_CHECKING:
#     from ragbot.application.ports.tokenizer_port import TokenizerPort

# logger = structlog.get_logger(__name__)


# Language → strategy class. Keep this sorted alphabetically by code so
# diffs that add a language stay reviewable.
#
# Strategy choices (multi-industry / multi-language safe defaults):
# - ``vi`` → ViTokenizer (underthesea). Falls back to simple if backend
#   not installed.
# - ``en`` / ``zh`` / ``ja`` / ``ko`` / ``th`` / ``ar`` → SimpleTokenizer.
#   Whitespace + punctuation works for English / Chinese (one char ≈ token,
#   acceptable for BM25 + length budgeting) / Japanese / Korean / Thai /
#   Arabic at platform-launch quality. Real per-language adapters
#   (sudachi-py, mecab, kkma, pythainlp) can be added in their own
#   strategy file later without touching this map's existing entries.
# _REGISTRY: dict[str, type] = {
#     "ar": SimpleTokenizer,
#     "en": SimpleTokenizer,
#     "ja": SimpleTokenizer,
#     "ko": SimpleTokenizer,
#     "th": SimpleTokenizer,
#     "vi": ViTokenizer,
#     "zh": SimpleTokenizer,
# }


# def build_tokenizer(
#     language: str | None = None,
#     **kwargs,
# ) -> "TokenizerPort":
#     """Construct the tokenizer matching ``language``.

#     @param language: ISO-639-1 code. ``None`` / unknown / empty →
#         :class:`NullTokenizer` (warned). ``DEFAULT_LANGUAGE`` is used when
#         the input is whitespace.
#     """
#     raw = (language or "").strip().lower()
#     if not raw:
#         raw = DEFAULT_LANGUAGE
#     cls = _REGISTRY.get(raw)
#     if cls is None:
#         logger.warning(
#             "tokenizer_unknown_language_fallback_null",
#             requested=language,
#             registered=sorted(_REGISTRY.keys()),
#         )
#         return NullTokenizer(language=raw, **kwargs)
#     try:
#         return cls(language=raw, **kwargs)  # type: ignore[return-value]
#     except (ImportError, NotImplementedError) as exc:
#         logger.error(
#             "tokenizer_strategy_not_installed",
#             requested=raw,
#             error=str(exc),
#         )
#         return NullTokenizer(language=raw, **kwargs)


# def list_languages() -> list[str]:
#     """Return registered language codes (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_tokenizer", "list_languages"]
