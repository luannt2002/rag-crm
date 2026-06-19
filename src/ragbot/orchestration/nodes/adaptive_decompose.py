"""Adaptive Router L3 node — LLM-based query decomposer.

Extracted from ``query_graph.build_graph``. di_kwargs (``model_resolver``,
``llm``) are threaded in as kwargs (bound via ``functools.partial``); the
domain-neutral decomposer is imported directly.
"""
from __future__ import annotations

from typing import Any

import structlog

from ragbot.orchestration.nodes.query_decomposer import decompose_query as _decompose_query
from ragbot.orchestration.state import GraphState
from ragbot.shared.errors import InvariantViolation

logger = structlog.get_logger(__name__)

# A decomposition is structurally ≥2 sub-queries (fewer = atomic query).
_MIN_DECOMPOSITION_SUBQUERIES = 2


async def adaptive_decompose(
    state: GraphState,
    *,
    model_resolver: Any,
    llm: Any,
) -> dict:
    """Adaptive Router L3 — LLM-based query decomposer.

    Calls the domain-neutral decomposer with the bot-resolved LLM
    spec. On any failure (LLM down, JSON parse error, single-item
    return) the original query passes through unchanged so the
    retrieve path stays functional. Multi-item results seed
    ``sub_queries``; the S2 bypass in multi_query_fanout / retrieve
    consumes that contract.
    """
    # Wave M3.7-P2 — name step ctx so adaptive_decompose LLM cost
    # (typically 1.4s p50, 7% of turns) attributes to its row.
    # Decompose may call llm.complete once per L3 question split;
    # we accumulate per-call into _dc_agg and record at end-of-step.
    async with state["step_tracker"].step("adaptive_decompose") as dc_ctx:
        query_text = state.get("rewritten_query") or state.get("query") or ""
        if not query_text:
            return {}
        if model_resolver is None or llm is None:
            return {}

        _dc_agg: dict[str, Any] = {
            "model": "", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        }

        async def _llm_invoker(
            *, system: str, user: str, model: str, max_tokens: int,  # noqa: ARG001
        ) -> str:
            # ``model`` is part of the decompose_query llm_invoker contract but
            # intentionally unused — model selection is via the binding purpose
            # (see the block comment below), not a per-call override.
            # Bot owner picks the binding by purpose ("decompose"); the
            # decomposer model knob ("decomposer.model") tunes which
            # underlying provider model gets called via the binding.
            try:
                cfg = await model_resolver.resolve_runtime(
                    record_tenant_id=state.get("record_tenant_id"),
                    record_bot_id=state.get("record_bot_id"),
                    purpose="decompose",
                )
            except InvariantViolation as exc:
                logger.warning(
                    "model_resolver_no_binding",
                    purpose="decompose",
                    record_bot_id=str(state.get("record_bot_id")),
                    node="adaptive_decompose",
                    error=str(exc)[:200],
                )
                # decompose_query() catches and falls back to [query].
                raise
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            # ``model_override`` was previously forwarded here but the
            # underlying litellm call exposes it directly to providers
            # (OpenAI), which reject any unrecognised kwarg with a 400.
            # The runtime cfg already carries the correct model from
            # ``resolve_runtime`` (or its llm_primary fallback). The
            # ``decomposer.model`` config knob is consumed inside the
            # decompose_query module (selects which binding purpose to
            # request) and does NOT need to be re-passed to the LLM
            # call. See plans/260515-multi-query-audit-fix/issues/
            # issue-9-decompose-model-override-rejected.md.
            resp = await llm.complete(
                cfg,
                messages=messages,
                purpose="decompose",
                max_tokens=max_tokens,
            )
            # Wave M3.7-P2 — accumulate per-call cost.
            if isinstance(resp, dict):
                _dc_agg["model"] = resp.get("model_name") or _dc_agg["model"]
                _dc_agg["prompt_tokens"] += int(resp.get("prompt_tokens", 0) or 0)
                _dc_agg["completion_tokens"] += int(resp.get("completion_tokens", 0) or 0)
                _dc_agg["cost_usd"] += float(resp.get("cost_usd", 0.0) or 0.0)
            else:
                _dc_agg["model"] = getattr(resp, "model_name", "") or _dc_agg["model"]
                _dc_agg["prompt_tokens"] += int(getattr(resp, "prompt_tokens", 0) or 0)
                _dc_agg["completion_tokens"] += int(getattr(resp, "completion_tokens", 0) or 0)
                _dc_agg["cost_usd"] += float(getattr(resp, "cost_usd", 0.0) or 0.0)
            # ``LLMResponse`` and the legacy dict envelope both expose
            # the text under different attrs; coalesce so the
            # decomposer module stays envelope-agnostic.
            if isinstance(resp, dict):
                return str(resp.get("text") or resp.get("content") or "")
            return str(
                getattr(resp, "content", None)
                or getattr(resp, "text", None)
                or ""
            )

        sub_queries = await _decompose_query(
            query_text, llm_invoker=_llm_invoker,
        )
        # Wave M3.7-P2 — record aggregated decompose cost.
        if _dc_agg["prompt_tokens"] > 0:
            dc_ctx.record_llm(
                model_used=str(_dc_agg["model"] or "") or None,
                prompt_tokens=_dc_agg["prompt_tokens"],
                completion_tokens=_dc_agg["completion_tokens"],
                cost_usd=_dc_agg["cost_usd"],
            )
        cleaned = [s for s in sub_queries if isinstance(s, str) and s.strip()]
        if len(cleaned) >= _MIN_DECOMPOSITION_SUBQUERIES:
            logger.info(
                "adaptive_router_decomposed",
                original=query_text[:80],
                sub_count=len(cleaned),
                source="adaptive_router_l3",
            )
            return {"sub_queries": cleaned, "original_query": query_text}
        return {}
