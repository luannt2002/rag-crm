"""Condense-question node — folds conversation history into a standalone query.

Extracted from ``query_graph.build_graph``. The builder helpers ``_lang`` and
``_invoke_llm_node`` are threaded in as kwargs (bound via ``functools.partial``);
``_pcfg`` is a pure helper imported directly.
"""
from __future__ import annotations

from typing import Any

import structlog

from ragbot.orchestration.query_graph_helpers import _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.chunking import normalize_vn_section_numerals
from ragbot.shared.condense_gate import has_meaningful_history
from ragbot.shared.constants import (
    DEFAULT_CONDENSE_HISTORY_LIMIT,
    DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
    DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
)
from ragbot.shared.errors import InvariantViolation

logger = structlog.get_logger(__name__)


async def condense_question(
    state: GraphState,
    *,
    _lang: Any,
    _invoke_llm_node: Any,
) -> dict:
    """Condense conversation history + new question into a standalone query.

    Threshold lowered 2026-05-27: ``len(history) <= 2`` skipped follow-up
    after the very first turn (history = [user_T1, bot_T1] = 2 messages),
    which is when condense matters most (T2 may reference T1 entity with
    pronoun). Now ``< 2`` so the first follow-up triggers condense.
    Eval root-cause: "có ưu đãi gì k em" after "<a service-name query>"
    was reaching rewrite with raw query (no T1 context), routing to
    generic promo chunks. Condense now bridges that gap.
    """
    # Normalize Vietnamese legal section numerals (Roman → Arabic) so the
    # query matches chunks whose structural path is stored in arabic
    # canonical form. Pre-2026-05-27, "Chương III" query missed all
    # "Chương III" chunks because the chunker also stored Roman literally;
    # after ship-260527 the chunker stores arabic, this mirror keeps the
    # two sides aligned. No-op for queries without Chương|Mục|Phần.
    _raw_q = state.get("query", "") or ""
    _norm_q = normalize_vn_section_numerals(_raw_q) if _raw_q else _raw_q
    _query_patch: dict[str, str] = {}
    if _norm_q != _raw_q:
        _query_patch["query"] = _norm_q
        state["query"] = _norm_q
    history = state.get("conversation_history", [])
    # Shared predicate with the merged understand node (drift-proof — the
    # 2026-05-27 threshold semantics live in ONE place now).
    if not has_meaningful_history(
        history,
        min_turns=DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
        min_chars=DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
    ):
        return _query_patch
    # Wave M3.7-G1 — name the step context so the post-LLM payload
    # (model + tokens + cost) can be recorded onto request_steps.
    # WHY: M3.6 admin audit found request_steps.model_used /
    # cost_usd were NULL on 9/10 LLM-bound steps; only ``generate``
    # was wired (M3.2 Phase 1). Without per-step capture the cost
    # dashboard can not attribute spend to condense / understand /
    # multi_query / grade / etc. Recording is best-effort: if the
    # payload is missing keys we just skip (helper returns ints).
    async with state["step_tracker"].step("condense_question") as condense_ctx:
        _pack = _lang(state)
        _hist_limit = _pcfg(state, "condense_history_limit", DEFAULT_CONDENSE_HISTORY_LIMIT)
        history_text = "\n".join(
            f"{_pack.condense_user_role if m.get('role') == 'user' else _pack.condense_bot_role}: {m.get('content', '')}"  # noqa: E501
            for m in history[-_hist_limit:]
        )
        messages = [
            {"role": "system", "content": _pack.prompt_condense},
            {"role": "user", "content": (
                f"{_pack.condense_history_label}:\n{history_text}\n\n"
                f"{_pack.condense_new_question_label}: {state['query']}\n\n"
                f"{_pack.condense_standalone_label}:"
            )},
        ]
        try:
            payload, _ctx = await _invoke_llm_node(
                state, purpose="condensing", messages=messages, user_prompt=state["query"],
            )
            # Wave M3.7 — record model + tokens + cost into request_steps.
            condense_ctx.record_llm(
                model_used=str(payload.get("model_name") or "") or None,
                prompt_tokens=int(payload.get("prompt_tokens") or 0),
                completion_tokens=int(payload.get("completion_tokens") or 0),
                cost_usd=float(payload.get("cost_usd") or 0.0),
            )
            condensed = (payload["text"] or "").strip()
            if condensed:
                # Re-apply numeral normalize on the LLM-condensed output:
                # an LLM rewrite may introduce roman numerals even if the
                # incoming query was arabic (history could contain roman).
                condensed = normalize_vn_section_numerals(condensed)
                return {"query": condensed, "original_query": state["query"]}
        except (TimeoutError, InvariantViolation, OSError, RuntimeError, ValueError):
            # Condense is opportunistic; LLM/router/transport failure
            # falls through to the original query unchanged.
            logger.debug("condense_question_skipped")
        return _query_patch
