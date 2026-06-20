"""LangGraph conditional-edge routing deciders for the RAG chat pipeline.

These are pure ``state -> next_node_name`` functions: they read only the
``GraphState`` dict plus module-level config/constants — they capture NO
``build_graph`` di_kwargs. Extracted from ``query_graph.build_graph`` so the
graph builder shrinks; ``query_graph`` imports them back and registers them
by reference in ``add_conditional_edges`` (their ``__name__`` is preserved,
which the route-function test fixture relies on).
"""
from __future__ import annotations

import contextlib

import structlog

from ragbot.orchestration.query_graph_helpers import _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_ADAPTIVE_ROUTER_L1_ENABLED,
    DEFAULT_CRAG_MAX_GRADE_RETRIES,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_DECOMPOSE_CONFIDENCE_GATE,
    DEFAULT_DECOMPOSE_MIN_TOKENS,
    DEFAULT_INTENT_CONFIDENCE_FALLBACK,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS,
    DEFAULT_REFLECTION_ENABLED,
    DEFAULT_SKIP_REFLECT_INTENTS,
    DEFAULT_SKIP_REWRITE_INTENTS,
    INTENT_COMPARISON,
    INTENT_MULTI_HOP,
    INTENT_SYNTHESIS,
)

logger = structlog.get_logger(__name__)

try:  # metrics optional in tests
    from ragbot.infrastructure.observability.metrics import (
        decompose_skipped_low_confidence_total,
    )
except ImportError:
    decompose_skipped_low_confidence_total = None  # type: ignore[assignment]

# Minimum sub-query count for the adaptive-router L1 divert: fewer than two
# means the query is atomic, not multi-entity — structural, not tunable.
_MIN_MULTI_ENTITY_SUBQUERIES = 2


def _input_blocked(state: GraphState) -> str:
    for f in state.get("guardrail_flags", []):
        if f.get("stage") == "input" and f.get("blocked"):
            return "persist"
    return "check_cache"


def _cache_route(state: GraphState) -> str:
    """If cache hit produced an answer, skip to persist."""
    if state.get("cache_status") == "hit" and state.get("answer"):
        return "persist"
    if _pcfg(state, "merge_condense_router", True):
        return "understand_query"
    return "condense_question"


def _understand_query_route(state: GraphState) -> str:
    """Route after understand_query (merged condense+router).

    Adaptive Router L1: when ``adaptive_router_l1_enabled`` is True
    and the LLM-emitted intent is NOT already ``multi_hop`` (which
    already triggers the legacy decompose path with its own
    confidence gate), divert into the domain-neutral regex/heuristic
    classifier. The classifier is microseconds; on "complex" it
    seeds ``sub_queries`` via ``adaptive_decompose`` so the existing
    retrieve / fanout (S2 bypass) consumes them. On "simple" it
    falls back to the legacy router so the byte-identical path is
    preserved for all non-multi-entity questions.
    """
    if _pcfg(
        state, "adaptive_router_l1_enabled", DEFAULT_ADAPTIVE_ROUTER_L1_ENABLED,
    ):
        existing_subs = [
            s for s in (state.get("sub_queries") or [])
            if isinstance(s, str) and s.strip()
        ]
        if (
            state.get("intent") != INTENT_MULTI_HOP
            and len(existing_subs) < _MIN_MULTI_ENTITY_SUBQUERIES
        ):
            return "query_complexity"
    return _router_route(state)


def _complexity_route(state: GraphState) -> str:
    """Route after Layer 1 classifier: complex → L3 decomposer, else legacy router."""
    if state.get("complexity_label") == "complex":
        return "adaptive_decompose"
    return _router_route(state)


def _router_route(state: GraphState) -> str:
    """Adaptive query routing: skip nodes based on intent.

    All intents flow through retrieve → generate.
    """
    intent = state.get("intent", DEFAULT_INTENT_FALLBACK)
    # Comparison ("so sánh X với Y") is multi-entity: a single embedding of the
    # whole sentence dilutes (token overlap pulls a co-occurring service, e.g.
    # "trẻ hóa" -> Ultherapy, not the two compared entities). Route it to
    # decompose so each entity becomes its own sub-query and both sides get
    # retrieved (paired with the stats-route skip for comparison in retrieve).
    if intent in (INTENT_MULTI_HOP, INTENT_COMPARISON) and _pcfg(
        state, "decompose_enabled", True,
    ):
        _query_text = (state.get("query") or "").strip()
        _decompose_min = int(_pcfg(
            state, "decompose_min_tokens", DEFAULT_DECOMPOSE_MIN_TOKENS,
        ))
        if len(_query_text.split()) >= _decompose_min:
            # Confidence gate. Skip decompose (5 sub-query fan-out is
            # wasteful) when the classifier reports low confidence. Bot
            # owner can override via per-bot
            # ``decompose_confidence_gate`` pipeline_config slot.
            _conf_gate = float(_pcfg(
                state,
                "decompose_confidence_gate",
                DEFAULT_DECOMPOSE_CONFIDENCE_GATE,
            ))
            _conf = float(state.get(
                "intent_confidence",
                DEFAULT_INTENT_CONFIDENCE_FALLBACK,
            ))
            if _conf < _conf_gate:
                if decompose_skipped_low_confidence_total is not None:
                    with contextlib.suppress(ValueError, KeyError, AttributeError):
                        decompose_skipped_low_confidence_total.labels(
                            intent=str(intent),
                        ).inc()
                logger.info(
                    "decompose_skipped_low_confidence",
                    intent=intent,
                    confidence=_conf,
                    gate=_conf_gate,
                )
            else:
                return "decompose"
    skip_rewrite = _pcfg(state, "skip_rewrite_intents", DEFAULT_SKIP_REWRITE_INTENTS)
    if intent in skip_rewrite:
        return "retrieve"
    return "rewrite"


def _grade_route(state: GraphState) -> str:
    """Route after grade: rewrite_retry while retries left, else generate.

    Smart-skip (S1 Pipeline-Opt, T1-Smartness): the grade node itself
    short-circuits when ``crag_skip_retry_above_score`` is exceeded by
    pass-1 top score (see early-exit block in ``grade``). That path
    sets ``crag_skip_retry=True`` and ``retrieval_adequate=True`` so
    this router falls through to ``generate`` without an LLM call.

    Legacy belt-and-suspenders gate retained: if pass-1 grading marked
    the result inadequate but the rerank score still clears the floor,
    bypass rewrite_retry. The grounding_check guardrail downstream
    enforces HALLU=0 regardless of which path was taken.
    """
    # Fast path: skip flag was set in the grade node's early-exit.
    if state.get("crag_skip_retry"):
        return "generate"
    if not state.get("retrieval_adequate", True):
        retries = state.get("grade_retries", 0)
        if retries < _pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES):
            skip_floor = float(_pcfg(
                state,
                "crag_skip_retry_above_score",
                DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
            ))
            if skip_floor > 0 and retries == 0:
                # Inspect pass-1 reranked chunks (graded_chunks may already
                # be filtered to relevant+ambiguous, so prefer the upstream
                # pool which carries the rerank score distribution).
                pool = state.get("reranked_chunks") or state.get("graded_chunks") or []
                top_score = 0.0
                for c in pool:
                    s = float(c.get("score", 0) or 0)
                    top_score = max(top_score, s)
                if top_score >= skip_floor:
                    logger.info(
                        "crag_retry_smart_skip",
                        top_score=round(top_score, 4),
                        skip_floor=skip_floor,
                    )
                    return "generate"
            return "rewrite_retry"
    return "generate"


def _output_blocked(state: GraphState) -> str:
    """Route after guard_output: persist when blocked, when bot did not
    opt into reflect, or when intent is in skip_reflect; else reflect.

    Reflect-gate (added 2026-05-18): bot owners opt in via
    ``plan_limits.reflection_enabled``. The default is False — matching
    ``shared/bot_limits.PLAN_LIMIT_SCHEMA``. Production audit
    (req 9cf611b5) found reflect firing 2x per turn (3.57s wasted) on
    bots that never enabled it. Gating here saves the round-trip
    without touching the reflect node implementation.
    """
    for f in state.get("guardrail_flags", []):
        if f.get("stage") == "output" and f.get("blocked"):
            return "persist"
    if not _pcfg(state, "reflection_enabled", DEFAULT_REFLECTION_ENABLED):
        return "persist"
    skip_reflect = _pcfg(state, "skip_reflect_intents", DEFAULT_SKIP_REFLECT_INTENTS)
    if state.get("intent") in skip_reflect:
        return "persist"
    return "reflect"


def _retrieve_route(state: GraphState) -> str:
    """Route after retrieve: skip pipeline when 0 chunks, else check GraphRAG."""
    # Stream D (RAGO Pareto): early exit when retrieval returned 0 chunks.
    # Skipping rerank→mmr→grade→rewrite_retry saves 3-4 API calls per turn.
    chunks = state.get("retrieved_chunks") or []
    if not chunks:
        return "generate"
    # Stats/aggregation route: chunks are authoritative SQL results
    # (price-range / superlative) and graded_chunks is already seeded.
    # Skip rerank→mmr→grade — the fuzzy reranker/grader otherwise rescore
    # the synthetic price list low and drop it, making the bot refuse a
    # numeric question the SQL already answered. grounding_check at
    # guard_output still enforces HALLU=0.
    if str(state.get("retrieve_mode") or "").startswith("stats"):
        return "generate"
    graph_mode = _pcfg(state, "graph_rag_mode", "disabled")
    if graph_mode == "disabled":
        return "rerank"
    if graph_mode == "adaptive":
        intent = state.get("intent", DEFAULT_INTENT_FALLBACK)
        if intent not in INTENT_SYNTHESIS:
            return "rerank"
    return "graph_retrieve"


def _reflect_route(state: GraphState) -> str:
    total_iters = state.get("_total_graph_iterations", 0)
    max_iters = int(_pcfg(state, "max_total_graph_iterations", DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS))
    if total_iters >= max_iters:
        logger.warning("graph_iteration_cap_reached", iterations=total_iters)
        return "persist"
    if not state.get("answer"):
        return "generate"
    return "persist"
