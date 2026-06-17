"""Self-RAG reflection node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. The DI dependencies that
were closure-captured locals of ``build_graph`` become explicit keyword
parameters with the SAME names, so the body is byte-identical to its former
nested-closure form (pure relocation — no logic, prompt, LLM call, state key,
ordering or log-event change).

Shared helper closures (``_invoke_llm_node``, ``_invoke_structured_llm_node``,
``_so_usage``) are threaded in as kwargs; ``build_graph`` still defines them as
closures and passes them via the partial. Module-level helpers ``_pcfg`` /
``_lang`` are likewise passed in (they live in ``query_graph`` and importing
them here would create a circular import).
"""

from __future__ import annotations

import json as _json_mod
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import ReflectOutput
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_MAX_REFLECT_RETRIES,
    DEFAULT_REFLECT_ANSWER_PREVIEW_CHARS,
    DEFAULT_REFLECT_CONTEXT_CHUNK_CAP,
    DEFAULT_REFLECT_CONTEXT_CHUNK_CHARS,
    DEFAULT_REFLECT_SKIP_IF_GROUNDED,
    DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
    DEFAULT_REFLECT_USE_STRUCTURED_OUTPUT,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
)
from ragbot.shared.errors import InvariantViolation

logger = structlog.get_logger(__name__)


async def reflect(
    state: GraphState,
    *,
    llm: Any = None,
    model_resolver: Any = None,
    _pcfg: Any,
    _lang: Any,
    _invoke_llm_node: Any,
    _invoke_structured_llm_node: Any,
    _so_usage: Any,
) -> dict:
    """Self-RAG reflection: judge whether to keep or rewrite the answer."""
    # Wave M3.7-P2 — name reflect step ctx so the Self-RAG critique
    # LLM cost (structured or plain text path) is attributed to the
    # request_steps reflect row.
    async with state["step_tracker"].step("reflect") as reflect_ctx:
        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=reflection")
        answer = state.get("answer", "")
        query = state.get("rewritten_query") or state["query"]

        _reflect_preview = _pcfg(
            state,
            "reflect_answer_preview",
            DEFAULT_REFLECT_ANSWER_PREVIEW_CHARS,
        )
        # Reflect with the retrieved <documents> so the reflector can detect
        # facts present in the chunks but DROPPED from the answer (the
        # dominant drop-fact failure). Without chunks the reflector judges
        # completeness blind. Capped per-chunk to bound the prompt.
        _refl_chunks = state.get("graded_chunks") or []
        _refl_ctx = "\n\n".join(
            (c.get("content") or c.get("text") or "")[:DEFAULT_REFLECT_CONTEXT_CHUNK_CHARS]
            for c in _refl_chunks[:DEFAULT_REFLECT_CONTEXT_CHUNK_CAP]
            if isinstance(c, dict)
        )
        _refl_user = f"<question>{query}</question>\n"
        if _refl_ctx:
            _refl_user += f"<documents>\n{_refl_ctx}\n</documents>\n"
        _refl_user += f"<answer>{answer[:_reflect_preview]}</answer>"
        messages = [
            {"role": "system", "content": _lang(state).prompt_reflector},
            {"role": "user", "content": _refl_user},
        ]

        so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
        so_reflect = _pcfg(state, "reflect_use_structured_output", DEFAULT_REFLECT_USE_STRUCTURED_OUTPUT)
        should_retry: bool | None = None
        if bool(so_master) and bool(so_reflect):
            parsed, ctx = await _invoke_structured_llm_node(
                state,
                purpose="reflection",
                messages=messages,
                user_prompt=f"{query}\n{answer[:_reflect_preview]}",
                schema=ReflectOutput,
            )
            # Wave M3.7-P2 — record structured-LLM cost regardless of
            # parse outcome (model burned tokens either way).
            if ctx is not None:
                _rf_usage = _so_usage(ctx)
                reflect_ctx.record_llm(
                    model_used=str(getattr(ctx, "model_id", "") or "") or None,
                    prompt_tokens=_rf_usage["prompt_tokens"],
                    completion_tokens=_rf_usage["completion_tokens"],
                    cost_usd=_rf_usage["cost_usd"],
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
                should_retry = parsed.action == "rewrite"

        if should_retry is None:
            payload, _ctx = await _invoke_llm_node(
                state,
                purpose="reflection",
                messages=messages,
                user_prompt=f"{query}\n{answer[:_reflect_preview]}",
            )
            # Wave M3.7-P2 — record plain-text fallback LLM cost.
            # Additive — if structured path above already recorded,
            # this sums to the same row (one step covers both paths
            # when fallback fires after structured fail).
            reflect_ctx.record_llm(
                model_used=str(payload.get("model_name") or "") or None,
                prompt_tokens=int(payload.get("prompt_tokens") or 0),
                completion_tokens=int(payload.get("completion_tokens") or 0),
                cost_usd=float(payload.get("cost_usd") or 0.0),
            )
            verdict = payload["text"].strip().lower()
            should_retry = "rewrite" in verdict and "keep" not in verdict

        if should_retry:
            retries = state.get("reflect_retries", 0)
            if retries < _pcfg(state, "max_reflect_retries", DEFAULT_MAX_REFLECT_RETRIES):
                # Smart-skip (T2 perf): when the answer is grounded
                # (no llm_grounding_fail flag from guard_output) AND
                # the pass-1 top retrieval score clears the floor,
                # honour the existing answer rather than burning a
                # second generate + guard pass (~5-6s). Default OFF
                # keeps legacy retry behaviour.
                _skip_if_grounded = bool(_pcfg(
                    state,
                    "reflect_skip_if_grounded",
                    DEFAULT_REFLECT_SKIP_IF_GROUNDED,
                ))
                if _skip_if_grounded and retries == 0:
                    _grounding_failed = any(
                        f.get("rule_id") == "llm_grounding_fail"
                        for f in state.get("guardrail_flags", [])
                    )
                    if not _grounding_failed:
                        _floor = float(_pcfg(
                            state,
                            "reflect_skip_top_score_floor",
                            DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
                        ))
                        _pool = state.get("graded_chunks") or state.get("reranked_chunks") or []
                        _top_score = 0.0
                        for c in _pool:
                            s = float(c.get("score", 0) or 0)
                            if s > _top_score:
                                _top_score = s
                        if _top_score >= _floor:
                            logger.info(
                                "reflect_retry_smart_skip",
                                top_score=round(_top_score, 4),
                                floor=_floor,
                            )
                            return {}
                return {
                    "answer": "",
                    "reflect_retries": retries + 1,
                }

        return {}


__all__ = ["reflect"]
