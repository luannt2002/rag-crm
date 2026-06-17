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
# Reason: Zero production imports. Self-referencing only.
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

# """BoilerplateResolver — per-tenant + per-language boilerplate pattern chain.

# Resolves the boilerplate-removal regex pattern list for a bot using the
# canonical 3-tier fallback chain documented in
# ``tests/unit/test_domain_neutral_multitenant.py``:

#     1. ``bot.custom_vocabulary["boilerplate_patterns"]`` — per-bot override (highest)
#     2. ``system_config.boilerplate_removal_patterns_by_language.<lang>`` — tenant-global
#        per-language map, fetched via
#        ``config_service.get_by_language(language)``
#     3. ``shared/constants.DEFAULT_BOILERPLATE_PATTERNS_*`` — boot-fallback SEED

# This mirrors the Strategy + DI mindset (CLAUDE.md): the resolver takes a
# ``config_service`` port via constructor injection rather than reaching into
# infrastructure modules directly, so unit tests inject a ``MagicMock``
# without spinning up Redis or Postgres.

# Domain-neutral by construction: no brand / industry literal lives in this
# file; the only language-specific SEED reference is ``vi`` (handled via a
# language → constant attribute map so adding ``en`` / ``th`` / ``ja`` is a
# constants.py edit, not a resolver edit — Open-Closed).
# """

# from __future__ import annotations

# from typing import Any, Iterable

# import structlog

# from ragbot.shared import constants

# logger = structlog.get_logger(__name__)


# Language → SEED-tuple attribute on ``shared/constants``. Adding a new
# language requires (1) seeding the constant + (2) one line here — no edit
# to the resolver body (Open-Closed). Missing-language fallback returns
# the empty tuple so callers degrade to no-op stripping rather than raising.
# _SEED_ATTRS_BY_LANGUAGE: dict[str, str] = {
#     "vi": "DEFAULT_BOILERPLATE_PATTERNS_VI",
# }


# def _seed_for_language(language: str) -> tuple[str, ...]:
#     """Return the SEED tuple for ``language`` or ``()`` if unknown."""
#     attr = _SEED_ATTRS_BY_LANGUAGE.get(language)
#     if attr is None:
#         return ()
#     return tuple(getattr(constants, attr, ()))


# class BoilerplateResolver:
#     """Resolve boilerplate-removal regex patterns for a (bot, language) pair.

#     Constructor injection of ``config_service`` keeps the resolver
#     transport-agnostic — production wiring passes
#     :class:`ragbot.application.services.system_config_service.SystemConfigService`,
#     tests pass a ``MagicMock``.
#     """

#     def __init__(self, *, config_service: Any) -> None:
#         self._config_service = config_service

#     async def resolve(
#         self,
#         *,
#         bot: Any,
#         language: str,
#     ) -> list[str]:
#         """Return the merged boilerplate pattern list for ``(bot, language)``.

#         Resolution order (later layers WIN; first non-empty layer wins):

#             1. ``bot.custom_vocabulary["boilerplate_patterns"]``
#             2. ``config_service.get_by_language(language)``
#             3. SEED tuple from ``shared/constants``

#         Per-bot override is treated as a *replace*, not a merge — bot owners
#         who set their own patterns expect their list to be authoritative.
#         Same semantics for the tenant-wide ``system_config`` row.
#         """
        # Layer 1 — per-bot override (highest priority).
#         bot_patterns = self._extract_bot_patterns(bot)
#         if bot_patterns:
#             return list(bot_patterns)

        # Layer 2 — tenant-global per-language row from system_config.
#         tenant_patterns = await self._load_tenant_patterns(language)
#         if tenant_patterns:
#             return list(tenant_patterns)

        # Layer 3 — boot-fallback SEED.
#         return list(_seed_for_language(language))

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
#     @staticmethod
#     def _extract_bot_patterns(bot: Any) -> list[str]:
#         """Pull ``custom_vocabulary["boilerplate_patterns"]`` off the bot row.

#         Tolerates both attribute access (SQLAlchemy mapped object) and dict
#         access (raw row / SimpleNamespace) so unit tests using
#         ``SimpleNamespace(custom_vocabulary={...})`` exercise the same code
#         path as production.
#         """
#         if bot is None:
#             return []
#         vocab = getattr(bot, "custom_vocabulary", None)
#         if vocab is None and isinstance(bot, dict):
#             vocab = bot.get("custom_vocabulary")
#         if not isinstance(vocab, dict):
#             return []
#         patterns = vocab.get("boilerplate_patterns")
#         return _coerce_pattern_list(patterns)

#     async def _load_tenant_patterns(self, language: str) -> list[str]:
#         """Best-effort fetch of the tenant-wide map row.

#         Graceful degradation: any port error degrades to an empty list so
#         the caller falls through to the SEED tier rather than raising in
#         the hot path.
#         """
#         get_by_language = getattr(self._config_service, "get_by_language", None)
#         if get_by_language is None:
#             return []
#         try:
#             row = await get_by_language(language)
#         except Exception as exc:  # noqa: BLE001 — graceful degrade to SEED tier
#             logger.debug(
#                 "boilerplate_tenant_fetch_failed",
#                 language=language,
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#             )
#             return []
#         return _coerce_pattern_list(row)


# def _coerce_pattern_list(raw: Any) -> list[str]:
#     """Best-effort coercion of an iterable of strings; empty list otherwise."""
#     if raw is None:
#         return []
#     if isinstance(raw, str):
        # A single string is ambiguous — treat as a single-pattern list so
        # config_service rows like ``"pattern"`` (vs ``["pattern"]``) work.
#         stripped = raw.strip()
#         return [stripped] if stripped else []
#     if isinstance(raw, Iterable):
#         return [str(p) for p in raw if isinstance(p, str) and p]
#     return []


# __all__ = ["BoilerplateResolver"]
