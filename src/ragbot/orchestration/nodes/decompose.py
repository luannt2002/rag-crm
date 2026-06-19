"""Query-decompose node — splits a multi-hop query into 2-4 sub-questions.

Extracted from ``query_graph.build_graph``. Builder helpers (``_lang``,
``_invoke_llm_node``, ``_invoke_structured_llm_node``, ``_so_usage``) are threaded
in as kwargs (bound via ``functools.partial``); ``_pcfg`` and
``parse_decomposed_sub_queries`` are pure helpers imported directly.
"""
from __future__ import annotations

import json as _json_mod
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import DecomposeOutput
from ragbot.orchestration.query_graph_helpers import _pcfg, parse_decomposed_sub_queries
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_DECOMPOSE_USE_STRUCTURED_OUTPUT,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
)
from ragbot.shared.errors import InvariantViolation

logger = structlog.get_logger(__name__)

# A decomposition is structurally ≥2 sub-queries (fewer = atomic query).
_MIN_DECOMPOSITION_SUBQUERIES = 2


async def decompose(
    state: GraphState,
    *,
    _lang: Any,
    _invoke_llm_node: Any,
    _invoke_structured_llm_node: Any,
    _so_usage: Any,
) -> dict:
    """LLM-decompose multi-hop query into 2-4 sub-questions."""
    async with state["step_tracker"].step("decompose"):
        query = state.get("rewritten_query") or state["query"]
        messages = [
            {"role": "system", "content": _lang(state).prompt_decompose},
            {"role": "user", "content": query},
        ]
        so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
        so_node = _pcfg(
            state, "decompose_use_structured_output", DEFAULT_DECOMPOSE_USE_STRUCTURED_OUTPUT,
        )
        use_structured = bool(so_master) and bool(so_node)
        try:
            if use_structured:
                parsed, ctx = await _invoke_structured_llm_node(
                    state,
                    purpose="decompose",
                    messages=messages,
                    user_prompt=query,
                    schema=DecomposeOutput,
                )
                if parsed is not None:
                    if ctx is not None:
                        _u = _so_usage(ctx)
                        ctx.record(
                            response=_json_mod.dumps(parsed.model_dump()),
                            prompt_tokens=_u["prompt_tokens"],
                            completion_tokens=_u["completion_tokens"],
                            cost_usd=_u["cost_usd"],
                            finish_reason=_u["finish_reason"],
                        )
                    sub_queries = [s.strip() for s in (parsed.sub_queries or []) if s and s.strip()]
                    if len(sub_queries) >= _MIN_DECOMPOSITION_SUBQUERIES:
                        logger.info(
                            "query_decomposed",
                            original=query[:80],
                            sub_count=len(sub_queries),
                            source="structured_output",
                        )
                        return {"sub_queries": sub_queries, "original_query": query}
                    return {}
            payload, ctx = await _invoke_llm_node(
                state, purpose="decompose", messages=messages, user_prompt=query,
            )
            ctx.record(
                response=payload["text"],
                prompt_tokens=payload["prompt_tokens"],
                completion_tokens=payload["completion_tokens"],
                cost_usd=payload["cost_usd"],
                finish_reason=payload["finish_reason"],
            )
            sub_queries = parse_decomposed_sub_queries(payload["text"] or "")
            if sub_queries:
                logger.info("query_decomposed", original=query[:80], sub_count=len(sub_queries))
                return {"sub_queries": sub_queries, "original_query": query}
        except (TimeoutError, InvariantViolation, OSError, RuntimeError, ValueError, KeyError):
            # Decompose is opportunistic; failure leaves the original
            # query as a single-pass retrieve.
            logger.debug("decompose_skipped", query=query[:80])
        return {}
