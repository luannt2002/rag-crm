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
# Reason: CAG infra never wired in bootstrap or graph.
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

# """AnthropicCAGService — corpus-injection CAG strategy with prompt-cache.

# Cache-Augmented Generation.

# Citation
# --------
# Chan et al. 2024 — "Don't Do RAG: When Cache-Augmented Generation is All
# You Need for Knowledge Tasks", https://arxiv.org/abs/2412.15605
# (ACM Web 2025 peer-reviewed). Reported benchmark: 10.9-40.5x lower
# end-to-end latency vs RAG on small corpora that fit inside the model
# context window, with no recall drop because the LLM sees every chunk
# every turn.

# Strategy
# --------
# 1. Operator/bot owner flips ``cag_mode_enabled`` (system_config / plan_limits).
# 2. On every turn, ``should_engage`` reads the bot's measured corpus token
#    count via the injected ``corpus_loader`` callback.
# 3. If the count is at-or-below ``cag_max_corpus_tokens``, the whole corpus
#    is returned as a single ``CAGPayload`` with ``cache_breakpoint=True``
#    so the LLM adapter wraps it in Anthropic's ``cache_control: ephemeral``
#    marker — the corpus is then served from prompt cache on every turn
#    after the first, collapsing retrieval latency to ~0.
# 4. If the corpus exceeds the ceiling, ``should_engage`` returns False and
#    the query graph falls back to standard RAG retrieve/rerank.

# HALLU=0 sacred
# --------------
# The adapter never lets a query run without ground truth:
# - empty corpus → ``should_engage=False`` (RAG fallback handles the empty
#   case via its own refuse path).
# - loader exception → ``should_engage=False`` (degrade silent).
# - corpus over ceiling → ``should_engage=False`` (RAG handles).

# The LLM never sees an "answer from memory" prompt — it always sees either
# the full corpus block (CAG) or the retrieved chunks (RAG).

# Domain-neutral
# --------------
# The adapter has no industry / brand literals. The corpus text is whatever
# the bot owner uploaded; the prompt envelope tags are platform-neutral
# ("corpus" block).
# """

# from __future__ import annotations

# from collections.abc import Awaitable, Callable
# from dataclasses import dataclass

# import structlog

# from ragbot.application.ports.cag_port import CAGPayload
# from ragbot.shared.errors import RepositoryError, RetrievalError
# from ragbot.shared.types import TenantId

# logger = structlog.get_logger(__name__)


# @dataclass(frozen=True, slots=True)
# class CorpusSnapshot:
#     """Result of the injected ``corpus_loader`` callback.

#     The loader is the integration seam between this strategy and the
#     bot's document/chunk repository — keeping it as a callback (vs
#     hard-coding a ``DocumentRepositoryPort`` dependency) lets the unit
#     test inject a fake without standing up the persistence layer, and
#     leaves the orchestrator free to swap loaders per-tenant (e.g.
#     bot-scoped concatenation vs workspace-aggregate later).

#     @param text: the concatenated corpus text. Empty string ``""`` signals
#         "no corpus available" — the strategy treats this as a hard no-engage.
#     @param tokens: pre-computed token count (the loader is best-placed to
#         compute this — it knows the tokenizer and may cache between turns).
#     """

#     text: str
#     tokens: int


# CorpusLoader = Callable[[TenantId, str], Awaitable[CorpusSnapshot]]


# class AnthropicCAGService:
#     """CAG strategy backed by Anthropic prompt-cache.

#     @param corpus_loader: async callback ``(record_tenant_id, record_bot_id)
#         -> CorpusSnapshot``. The orchestrator binds this at construction
#         time (see ``bootstrap`` wiring); pure-test paths inject a stub.
#     @param enabled: feature-flag value at construction time. Captured from
#         ``system_config.cag_mode_enabled`` (or per-bot plan_limits override)
#         by the caller before building this strategy. The strategy does NOT
#         re-read the flag — the registry rebuilds the adapter when ops flip
#         config so the runtime state stays consistent.
#     @param max_corpus_tokens: ceiling above which engagement is refused.
#         Resolved from ``DEFAULT_CAG_MAX_CORPUS_TOKENS`` via system_config.
#     """

#     def __init__(
#         self,
#         *,
#         corpus_loader: CorpusLoader,
#         enabled: bool,
#         max_corpus_tokens: int,
#     ) -> None:
#         self._corpus_loader = corpus_loader
#         self._enabled = enabled
#         self._max_corpus_tokens = max_corpus_tokens

#     @staticmethod
#     def get_provider_name() -> str:
#         return "anthropic"

#     async def should_engage(
#         self,
#         *,
#         record_tenant_id: TenantId,
#         record_bot_id: str,
#     ) -> bool:
#         """Gate decision: True only when flag is ON and corpus fits.

#         Loader errors degrade silent to False so the caller falls back
#         to RAG instead of fabricating an answer from parametric memory.
#         """
#         if not self._enabled:
#             logger.debug(
#                 "cag_lookup_disabled_flag_off",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 engaged=False,
#             )
#             return False

#         try:
#             snapshot = await self._corpus_loader(record_tenant_id, record_bot_id)
#         except (RepositoryError, RetrievalError, OSError, ValueError) as exc:
            # Degrade silent — CAG miss falls back to RAG. HALLU=0 sacred.
#             logger.warning(
#                 "cag_lookup_corpus_loader_failure",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#                 engaged=False,
#             )
#             return False

#         if not snapshot.text:
#             logger.info(
#                 "cag_lookup_empty_corpus",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 corpus_tokens=0,
#                 engaged=False,
#             )
#             return False

#         if snapshot.tokens > self._max_corpus_tokens:
            # Cross-over point — RAG retrieval is cheaper here than
            # re-priming a multi-K cache breakpoint on every miss.
#             logger.info(
#                 "cag_lookup_over_ceiling_fallback_rag",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 corpus_tokens=snapshot.tokens,
#                 ceiling_tokens=self._max_corpus_tokens,
#                 engaged=False,
#             )
#             return False

#         logger.info(
#             "cag_lookup_engaged",
#             step_name="cag_lookup",
#             feature_flag="cag_mode_enabled",
#             record_tenant_id=str(record_tenant_id),
#             record_bot_id=record_bot_id,
#             corpus_tokens=snapshot.tokens,
#             ceiling_tokens=self._max_corpus_tokens,
#             engaged=True,
#         )
#         return True

#     async def build_corpus_payload(
#         self,
#         *,
#         record_tenant_id: TenantId,
#         record_bot_id: str,
#     ) -> CAGPayload | None:
#         """Load + return the corpus payload for prompt injection.

#         Returns None on any failure / over-ceiling / empty path so the
#         caller can fall back to RAG without an extra try/except wrapper.

#         The caller is expected to have called ``should_engage`` first;
#         the duplicate ceiling check here is defensive — it costs O(1)
#         and guarantees the LLM never sees an over-sized cache block
#         even if a buggy caller skipped the gate.
#         """
#         if not self._enabled:
#             return None

#         try:
#             snapshot = await self._corpus_loader(record_tenant_id, record_bot_id)
#         except (RepositoryError, RetrievalError, OSError, ValueError) as exc:
#             logger.warning(
#                 "cag_lookup_payload_loader_failure",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#             )
#             return None

#         if not snapshot.text or snapshot.tokens > self._max_corpus_tokens:
#             return None

#         return CAGPayload(
#             corpus_text=snapshot.text,
#             corpus_tokens=snapshot.tokens,
#             cache_breakpoint=True,
#         )


# __all__ = ["AnthropicCAGService", "CorpusLoader", "CorpusSnapshot"]
