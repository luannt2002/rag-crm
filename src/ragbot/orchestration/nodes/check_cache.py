"""Semantic-cache lookup node — short-circuits the pipeline on a cache hit.

Extracted from ``query_graph.build_graph``. di_kwargs (``semantic_cache``,
``redis_client``) and builder infra-closures (``_audit``,
``_resolve_corpus_version``, ``_embed_query``, ``_resolved_oos_template``) are
threaded in as kwargs, bound via ``functools.partial`` in the graph builder.
``_pcfg`` and ``_compute_bot_cache_version`` are pure helpers imported directly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from ragbot.orchestration.query_graph_helpers import _compute_bot_cache_version, _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_CACHE_SIMILARITY_THRESHOLD,
    DEFAULT_QUERY_RECEIVED_AUDIT_PREVIEW_CHARS,
    DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
)

logger = structlog.get_logger(__name__)


async def check_cache(
    state: GraphState,
    *,
    semantic_cache: Any,
    redis_client: Any,
    _audit: Any,
    _resolve_corpus_version: Any,
    _embed_query: Any,
    _resolved_oos_template: Any,
) -> dict:
    """Lookup semantic cache; short-circuit on hit."""
    async with state["step_tracker"].step("cache_check") as cc_ctx:
        await _audit(
            state,
            "query_received",
            {
                "question": (state.get("query") or "")[:DEFAULT_QUERY_RECEIVED_AUDIT_PREVIEW_CHARS],
                "tenant_id": str(state.get("record_tenant_id") or ""),
                "channel_type": state.get("channel_type"),
                "message_id": state.get("message_id"),
                "history_messages": len(state.get("conversation_history") or []),
            },
        )
        if state.get("bypass_cache"):
            await _audit(state, "cache_check", {"hit": False, "reason": "bypass_test_mode"})
            cc_ctx.set_metadata(hit=False, reason="bypass_test_mode", bypass=True)
            return {"cache_status": "bypassed", "cache_hit": False}
        # Multi-turn follow-ups are context-dependent: the lookup runs on
        # the RAW query (before condense), so a pronoun query like "nó làm
        # những bước nào" is identical text across conversations but refers
        # to a DIFFERENT subject each time. A cosine hit would return a
        # stale cross-context answer. Skip the cache entirely when there is
        # conversation history (correctness > hit-rate; single-turn caches).
        if _pcfg(
            state, "semantic_cache_skip_multi_turn", DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
        ) and (state.get("conversation_history") or []):
            await _audit(state, "cache_check", {"hit": False, "reason": "skip_multi_turn"})
            cc_ctx.set_metadata(hit=False, reason="skip_multi_turn", bypass=False)
            return {"cache_status": "skip_multi_turn", "cache_hit": False}
        if semantic_cache is None:
            await _audit(state, "cache_check", {"hit": False, "reason": "no_semantic_cache"})
            cc_ctx.set_metadata(hit=False, reason="no_semantic_cache", bypass=False)
            return {}
        bot_cache_version = _compute_bot_cache_version(
            state.get("bot_system_prompt", ""), _resolved_oos_template(state),
            # Owner-taught synonym map feeds retrieval expansion → answer; an
            # edit must bust the key or a stale answer is served (M19). Read
            # via _pcfg so an absent map degrades to {} (legacy key unchanged).
            custom_vocabulary=_pcfg(state, "bot_custom_vocabulary", {}),
        )
        try:
            query_text = state["query"]
            # corpus_version (memoised per turn via state["_corpus_version"],
            # reused at cache_store so write+read see an identical key) and
            # query_embedding are independent — gather them. corpus_version
            # reads tenant/bot; embed reads query_text; neither feeds the
            # other, both feed the cache lookup below. _resolve_corpus_version
            # degrades internally (never raises) so it is safe inside the try.
            corpus_version, query_embedding = await asyncio.gather(
                _resolve_corpus_version(state),
                _embed_query(query_text, state),
            )
            if not query_embedding:
                await _audit(state, "cache_check", {"hit": False, "reason": "no_embedding"})
                cc_ctx.set_metadata(hit=False, reason="no_embedding", bypass=False)
                return {
                    "_bot_cache_version": bot_cache_version,
                    "_corpus_version": corpus_version,
                }
            cached = await semantic_cache.find_similar_with_text(
                query_embedding=query_embedding,
                query_text=query_text,
                record_tenant_id=state.get("record_tenant_id"),
                record_bot_id=state.get("record_bot_id"),
                bot_version=bot_cache_version,
                corpus_version=corpus_version,
                threshold=_pcfg(
                    state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD,
                ),
                step_tracker=state["step_tracker"],
                # Routes the cache lookup to the column matching this bot's embedding dim.
                embedding_column=state.get("embedding_column"),
                # Cross-process single-flight: two uvicorn workers serving
                # the same hot query must not both compute. Falls back to
                # in-process asyncio.Lock when redis_client is None.
                redis_client=redis_client,
            )
            if cached is not None:
                await _audit(
                    state,
                    "cache_check",
                    {
                        "hit": True,
                        "threshold": _pcfg(
                            state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD,
                        ),
                    },
                )
                cached_total = (getattr(cached, "prompt_tokens", 0) or 0) + (
                    getattr(cached, "completion_tokens", 0) or 0
                )
                # Wave M3.6-G3 — emit hit-rate event + capture cosine
                # similarity for cache-effectiveness dashboard.
                # WHY: pre-fix the cache hit/miss outcome was logged
                # via _audit only (forensic) without a structured
                # ``semantic_cache_outcome`` event that aggregates into
                # hit-rate metrics. With this event, ops can compute
                # hit-rate / day from log analytics without touching
                # audit_log tables (kept lean per CLAUDE.md observability
                # rule). Cosine similarity exposed so a future
                # ``cache_similarity_threshold`` tune has empirical data.
                _cache_sim = float(getattr(cached, "similarity", 0.0) or 0.0)
                _cache_threshold = float(
                    _pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD)
                )
                logger.info(
                    "semantic_cache_outcome",
                    hit=True,
                    similarity=round(_cache_sim, 4),
                    threshold=_cache_threshold,
                    record_bot_id=str(state.get("record_bot_id") or ""),
                    intent=state.get("intent") or "",
                )
                cc_ctx.set_metadata(
                    hit=True,
                    reason="cosine_sim",
                    bypass=False,
                    similarity=round(_cache_sim, 4),
                    threshold=_cache_threshold,
                )
                # 2026-05-27 — restore graded_chunks from cache snapshot
                # so /chat API ``_build_sources`` can rebuild sources on
                # cache_hit (RAGAS judge, audit tools, evaluator). When
                # snapshot empty (legacy row) sources stays [].
                _restored_chunks = [dict(c) for c in (cached.chunks or ())]
                return {
                    "answer": cached.answer,
                    "answer_type": "cache_hit",
                    "answer_reason": "Semantic cache hit",
                    "citations": list(cached.citations),
                    "graded_chunks": _restored_chunks,
                    "model_used": cached.model_name or "",
                    "cache_status": "hit",
                    "tokens": {"prompt": 0, "completion": 0, "cached": cached_total},
                    "cost_usd": 0.0,
                    "_bot_cache_version": bot_cache_version,
                    "_corpus_version": corpus_version,
                }
        except (AttributeError, TypeError, KeyError):
            # Programmer errors (renamed column, missing DI, bad state key) must surface.
            raise
        except (TimeoutError, OSError, RuntimeError, ValueError):
            # Cache lookup transport / shape failure → degrade silent.
            logger.warning("semantic_cache_check_failed", exc_info=True)
        await _audit(state, "cache_check", {"hit": False, "reason": "miss_or_error"})
        # Wave M3.6-G3 — miss path emits the same event shape so the
        # ``semantic_cache_outcome`` aggregator sees hit + miss in one
        # query (avoid forensic-only audit_log scan). Similarity 0.0
        # because no cached row was returned by ``find_similar_with_text``.
        logger.info(
            "semantic_cache_outcome",
            hit=False,
            similarity=0.0,
            threshold=float(
                _pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD)
            ),
            record_bot_id=str(state.get("record_bot_id") or ""),
            intent=state.get("intent") or "",
        )
        cc_ctx.set_metadata(hit=False, reason="miss_or_error", bypass=False)
        return {
            "_bot_cache_version": bot_cache_version,
            "_corpus_version": corpus_version,
        }
