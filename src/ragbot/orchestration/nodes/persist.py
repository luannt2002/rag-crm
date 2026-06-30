"""Terminal persist node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body. The inner ``_bg_cache_write`` fire-and-forget helper moves
in as a nested function of this node (unchanged), so the ``asyncio.create_task``
fan-out semantics are preserved exactly.

Shared helper closures (``_audit``, ``_resolve_corpus_version``,
``_embed_query``) and the module-level helpers (``_pcfg``,
``_compute_bot_cache_version``, ``_resolved_oos_template``) are threaded in as
kwargs; ``build_graph`` still owns them and passes them through the partial
(importing the query_graph-local helpers here would create a circular import).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.ports.cache_port import CachedResponse
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
    DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
    DEFAULT_SEMANTIC_CACHE_TTL,
    _REFUSE_ANSWER_TYPES,
)

logger = structlog.get_logger(__name__)

# Strong references to in-flight fire-and-forget cache-write tasks. ``asyncio``
# only holds a WEAK reference to a bare ``create_task`` result, so without this
# set the GC can collect the task mid-write → the semantic-cache entry is
# silently lost (hit-rate leak). Each task removes itself on completion.
_BG_CACHE_TASKS: set[asyncio.Task[None]] = set()


async def persist(
    state: GraphState,
    *,
    semantic_cache: Any = None,
    _audit: Any,
    _resolve_corpus_version: Any,
    _embed_query: Any,
    _pcfg: Any,
    _compute_bot_cache_version: Any,
    _resolved_oos_template: Any,
) -> dict:
    """Emit terminal audit + build _persist_meta; schedule cache write in bg.

    Execution order (critical-path is synchronous):
    1. ``_audit("query_completed")`` — forensic trail, MUST be synchronous
       per CLAUDE.md Async Rule #8 (ordering-sensitive).
    2. ``_persist_meta`` computation — returned to LangGraph state, must
       resolve before ``graph.ainvoke`` returns.
    3. Semantic-cache write — best-effort, no caller depends on its result,
       so it is scheduled as a fire-and-forget background task.  Failures
       are logged with full tenant context (never silently swallowed).
    """
    async def _bg_cache_write(
        *,
        query: str,
        answer: str,
        citations: list,
        chunks_snapshot: tuple,
        model_used: str,
        cache_ttl: int,
        bot_cache_version: str,
        corpus_version_w: str,
        record_tenant_id: object,
        record_bot_id: object,
        workspace_id: str,
        embedding_column: str | None,
        numeric: bool = False,
    ) -> None:
        """Fire-and-forget: embed query + store in semantic cache.

        Numeric answers (e.g. prices) cache via the EXACT-HASH path only:
        we store with a NULL embedding so an IDENTICAL query text still
        hits (identical text → identical number → zero stale risk), while
        the cosine path — which filters ``WHERE embedding IS NOT NULL`` —
        can never surface them for a near-duplicate query that differs only
        in a number. This restores price-query caching without the stale-
        wrong-number HALLU risk the blanket skip was guarding against.
        """
        try:
            query_embedding = [] if numeric else await _embed_query(query, state)
            if query_embedding or numeric:
                await semantic_cache.store(  # type: ignore[union-attr]
                    query=query,
                    query_embedding=query_embedding,
                    response=CachedResponse(
                        answer=answer,
                        citations=citations,
                        model_name=model_used,
                        cached_at_ts=int(time.time()),
                        chunks=chunks_snapshot,
                    ),
                    record_tenant_id=record_tenant_id,
                    record_bot_id=record_bot_id,
                    workspace_id=workspace_id,
                    bot_version=bot_cache_version,
                    corpus_version=corpus_version_w,
                    ttl_s=cache_ttl,
                    embedding_column=embedding_column,
                )
                logger.debug(
                    "semantic_cache_write_ok",
                    query=query[:80],
                    bot_id=str(record_bot_id),
                )
        except (SQLAlchemyError, ValueError, TypeError, OSError) as _cache_exc:
            logger.warning(
                "semantic_cache_write_bg_failed",
                error=str(_cache_exc)[:200],
                error_type=type(_cache_exc).__name__,
                record_tenant_id=str(record_tenant_id),
                record_bot_id=str(record_bot_id),
                exc_info=True,
            )
        except Exception as _cache_exc:  # noqa: BLE001 — background task wrapper: log + continue
            logger.error(
                "semantic_cache_write_bg_unexpected",
                error=str(_cache_exc)[:200],
                error_type=type(_cache_exc).__name__,
                record_tenant_id=str(record_tenant_id),
                record_bot_id=str(record_bot_id),
                exc_info=True,
            )

    async with state["step_tracker"].step("persist"):
        _ans_type = state.get("answer_type") or ""
        # Skip caching numeric answers — the cosine cache can return a
        # near-duplicate query differing only in a number, yielding a stale
        # wrong-number answer. Non-numeric answers still cache (hit-rate kept).
        _skip_numeric_cache = False
        if bool(_pcfg(
            state, "semantic_cache_skip_numeric", DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
        )) and state.get("answer"):
            # Static pure-function utility — no Port wrap needed (no I/O / state),
            # same precedent as the OutputGuardrail static import. Used only to
            # DECIDE whether to cache a numeric answer (never to alter the answer).
            from ragbot.infrastructure.guardrails.math_lockdown import (
                extract_numeric_claims,
            )
            _skip_numeric_cache = bool(extract_numeric_claims(state.get("answer", "")))
        # Numeric answers are no longer skipped entirely — they are written
        # with a NULL embedding (exact-hash cacheable, cosine-invisible) so
        # identical price queries hit without the stale-number HALLU risk.
        # Do not WRITE the cache for multi-turn turns either — a context-
        # dependent follow-up answer keyed on the raw query would poison the
        # cache for the same text in a different conversation. Mirror the
        # cache_check skip so write+read stay symmetric.
        _skip_multi_turn = bool(_pcfg(
            state, "semantic_cache_skip_multi_turn", DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
        )) and bool(state.get("conversation_history") or [])
        if (
            semantic_cache is not None
            and state.get("answer")
            and state.get("cache_status") != "hit"
            and _ans_type not in _REFUSE_ANSWER_TYPES
            and not _skip_multi_turn
        ):
            # Snapshot all state values needed by the background task now —
            # the LangGraph state dict may be mutated after this node returns.
            _query = state.get("original_query") or state["query"]
            _cache_ttl = int(_pcfg(state, "semantic_cache_ttl_s", DEFAULT_SEMANTIC_CACHE_TTL))
            # Reuse the version computed in check_cache; recompute only if that node was skipped.
            _bot_cache_version = state.get(
                "_bot_cache_version"
            ) or _compute_bot_cache_version(
                state.get("bot_system_prompt", ""), _resolved_oos_template(state),
            )
            # MUST match the corpus_version used at cache_check time
            # (memoised on state) so a write+read pair share one key.
            # Fresh resolve only when cache_check was skipped.
            _corpus_version_w = state.get(
                "_corpus_version"
            ) or await _resolve_corpus_version(state)
            # Compact graded_chunks snapshot for cache restore. Keep
            # fields the API ``_build_sources`` reads (document_name,
            # source_url, chunk_index, score, content). Strip embeddings
            # / metadata blobs to keep row size bounded.
            _graded = state.get("graded_chunks") or []
            _chunks_snap: tuple = tuple(
                {
                    "document_name": c.get("document_name")
                    or (c.get("metadata") or {}).get("document_title")
                    or "",
                    "source_url": c.get("source_url"),
                    "chunk_index": c.get("chunk_index", 0),
                    "score": float(c.get("score", 0) or 0),
                    "content": (c.get("content") or c.get("text") or "")[:2000],
                }
                for c in _graded[:8]
            )
            _cache_task = asyncio.create_task(
                _bg_cache_write(
                    query=_query,
                    answer=state["answer"],
                    citations=list(state.get("citations") or []),
                    chunks_snapshot=_chunks_snap,
                    model_used=state.get("model_used", ""),
                    cache_ttl=_cache_ttl,
                    bot_cache_version=_bot_cache_version,
                    corpus_version_w=_corpus_version_w,
                    record_tenant_id=state.get("record_tenant_id"),
                    record_bot_id=state.get("record_bot_id"),
                    workspace_id=state["workspace_id"],
                    embedding_column=state.get("embedding_column"),
                    numeric=_skip_numeric_cache,
                ),
                name="persist_cache_write",
            )
            # Hold a strong ref until the write finishes (anti-GC-drop).
            _BG_CACHE_TASKS.add(_cache_task)
            _cache_task.add_done_callback(_BG_CACHE_TASKS.discard)

        graded = state.get("graded_chunks") or []
        # Terminal trace event fires for every request regardless of outcome.
        # Audit is forensic trail — stays synchronous (CLAUDE.md Async Rule #8).
        _toks = state.get("tokens") or {}
        _scores_final = [float(c.get("score", 0) or 0) for c in graded]
        await _audit(
            state,
            "query_completed",
            {
                "answer_type": state.get("answer_type") or "",
                "answer_chars": len(state.get("answer") or ""),
                "model_used": state.get("model_used") or "",
                "intent": state.get("intent") or "",
                "graded_chunks": len(graded),
                "top_score": round(max(_scores_final), 6) if _scores_final else 0,
                "tokens_prompt": int(_toks.get("prompt", 0) or 0),
                "tokens_completion": int(_toks.get("completion", 0) or 0),
                "cost_usd": float(state.get("cost_usd", 0) or 0),
            },
        )
        if graded:
            context_chars = sum(c.get("chunk_chars", len(c.get("content", ""))) for c in graded)
            context_count = len(graded)
            state_meta = state.get("_persist_meta") or {}
            state_meta["context_chars"] = context_chars
            state_meta["context_chunks"] = context_count
            return {"_persist_meta": state_meta}

        return {}


__all__ = ["persist"]
