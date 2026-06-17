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
# Reason: infrastructure/tokenizer/vi_tokenizer.py never wired. shared/vi_tokenizer.py is the live one.
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

# """ViTokenizer — Vietnamese strategy backed by underthesea.

# Wraps the existing :func:`ragbot.shared.vi_tokenizer.tokenize_vi` and
# :func:`segment_vi_compounds` helpers behind a :class:`TokenizerPort` so
# the registry can hand the right strategy to multi-language ingest /
# retrieval paths. Falls back to whitespace tokenisation when the
# underthesea backend is unavailable (mirrors the legacy module behaviour).
# """

# from __future__ import annotations

# from typing import Any

# from ragbot.infrastructure.tokenizer.simple_tokenizer import SimpleTokenizer
# from ragbot.shared import vi_tokenizer as _legacy


# class ViTokenizer:
#     """Vietnamese tokenizer — underthesea word_tokenize when available."""

#     def __init__(
#         self,
#         *,
#         language: str = "vi",
#         config_service: Any = None,
#         **_: object,
#     ) -> None:
#         self._language = language
#         self._config_service = config_service
        # Lazily resolved on first call so import-time stays cheap.
#         self._fallback = SimpleTokenizer(language=language)

#     def _backend(self):
#         """Return the underthesea backend or ``None``.

#         Reaches into the legacy module so we share state with the existing
#         ``segment_vi_compounds`` / ``tokenize_vi`` helpers. The legacy
#         module owns the lazy-load lock and the singleton lifecycle —
#         duplicating that here would create two copies of the heavy
#         underthesea state.
#         """
        # The legacy module exposes ``_init_tokenizer`` (idempotent) plus
        # ``_tokenize_fn`` after the first call. We treat both as private
        # but stable within this monorepo — protocol enforced by tests.
#         init = getattr(_legacy, "_init_tokenizer", None)
#         if init is not None:
#             init()
#         return getattr(_legacy, "_tokenize_fn", None)

#     def tokenize(self, text: str) -> list[str]:
#         if not text or not text.strip():
#             return []
#         fn = self._backend()
#         if fn is None:
#             return self._fallback.tokenize(text)
#         try:
#             result = fn(text)
#         except Exception:  # noqa: BLE001 — fail-soft to fallback tokeniser
#             return self._fallback.tokenize(text)
#         if isinstance(result, list):
#             return [str(t) for t in result]
#         if isinstance(result, str):
            # underthesea ``format="text"`` (or a custom override) — split
            # on whitespace to recover the token list shape.
#             return result.split()
#         return self._fallback.tokenize(text)

#     def count_tokens(self, text: str) -> int:
#         return len(self.tokenize(text))

#     def get_language(self) -> str:
#         return self._language

#     def get_abbreviations(
#         self,
#         *,
#         bot: Any = None,
#         language: str | None = None,
#     ) -> dict[str, str]:
#         """Resolve the merged abbreviation map for ``(bot, language)``.

#         3-tier merge — later layers WIN on key collision:

#             1. ``shared.vi_tokenizer._VI_ABBREVIATIONS_SEED`` (boot-fallback,
#                only when the effective language is ``vi``).
#             2. ``config_service.get_by_language(language)`` — tenant-wide
#                per-language row from ``system_config``.
#             3. ``bot.custom_vocabulary["abbreviations"]`` — per-bot override
#                (highest priority).

#         This is the sync companion to the async
#         :func:`ragbot.shared.vi_tokenizer.get_abbreviations` which already
#         owns the DB-backed resolve path. The sync variant is for callers
#         that already have the bot row + a sync ``config_service`` in hand
#         (e.g. ingest-time normalisation where the row is held in memory).

#         Graceful degradation: any port error or wrong-shaped row degrades
#         to the next tier rather than raising — the hot path must never
#         crash on a flaky lookup.
#         """
#         lang = language if language is not None else self._language

        # Layer 1 — boot-fallback SEED (Vietnamese-only by design; the seed
        # contains bare ASCII tokens that would corrupt non-VN queries).
#         if lang == "vi":
#             merged: dict[str, str] = dict(_legacy._VI_ABBREVIATIONS_SEED)
#         else:
#             merged = {}

        # Layer 2 — tenant-wide per-language map.
#         tenant_map = self._load_tenant_abbreviations(lang)
#         if tenant_map:
#             merged.update(tenant_map)

        # Layer 3 — per-bot override (last wins on key collision).
#         bot_map = _extract_bot_abbreviations(bot)
#         if bot_map:
#             merged.update(bot_map)

#         return merged

#     def _load_tenant_abbreviations(self, language: str) -> dict[str, str]:
#         """Sync best-effort fetch of the tenant-wide per-language row."""
#         cfg = self._config_service
#         if cfg is None:
#             return {}
#         get_by_language = getattr(cfg, "get_by_language", None)
#         if get_by_language is None:
#             return {}
#         try:
#             row = get_by_language(language)
#         except Exception:  # noqa: BLE001 — graceful degrade to SEED tier
#             return {}
#         if not isinstance(row, dict):
#             return {}
#         return {str(k): str(v) for k, v in row.items() if isinstance(k, str) and isinstance(v, str)}


# def _extract_bot_abbreviations(bot: Any) -> dict[str, str]:
#     """Pull ``custom_vocabulary["abbreviations"]`` off the bot row."""
#     if bot is None:
#         return {}
#     vocab = getattr(bot, "custom_vocabulary", None)
#     if vocab is None and isinstance(bot, dict):
#         vocab = bot.get("custom_vocabulary")
#     if not isinstance(vocab, dict):
#         return {}
#     abbr = vocab.get("abbreviations")
#     if not isinstance(abbr, dict):
#         return {}
#     return {str(k): str(v) for k, v in abbr.items() if isinstance(k, str) and isinstance(v, str)}


# __all__ = ["ViTokenizer"]
