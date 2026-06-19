"""Query-rewrite node — reformulates the query into a standalone search query.

Extracted from ``query_graph.build_graph``. di_kwargs (``model_resolver``,
``llm``) and builder helpers (``_lang``, ``_invoke_llm_node``) are threaded in as
kwargs (bound via ``functools.partial``); ``_pcfg`` is imported directly.
"""
from __future__ import annotations

from typing import Any

from ragbot.orchestration.query_graph_helpers import _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_REWRITE_ENABLED_BY_INTENT
from ragbot.shared.errors import InvariantViolation


async def rewrite(
    state: GraphState,
    *,
    model_resolver: Any,
    llm: Any,
    _lang: Any,
    _invoke_llm_node: Any,
) -> dict:
    # Wave M3.7-G1 — name step ctx so the rewrite LLM cost is
    # attributed to its row in request_steps (was NULL pre-fix).
    async with state["step_tracker"].step("rewrite") as rw_ctx:
        # Per-intent skip gate: lightweight intents (greeting/chitchat/
        # factoid/feedback/vu_vo/out_of_scope) do not benefit from query
        # reformulation — skip the LLM call and carry the original query
        # forward unchanged. Saves ~1.2s per turn on those intent classes
        # with zero T1 quality regression (HALLU=0 sacred unaffected;
        # grounding_check still validates the final answer).
        _rewrite_intent = str(state.get("intent") or "")
        _rewrite_enabled_map = _pcfg(
            state, "rewrite_enabled_by_intent", None,
        )
        if isinstance(_rewrite_enabled_map, dict) and _rewrite_intent in _rewrite_enabled_map:
            try:
                _intent_rewrite_enabled = bool(_rewrite_enabled_map[_rewrite_intent])
            except (TypeError, ValueError):
                _intent_rewrite_enabled = True
        else:
            # Unknown intent or no override → default constant lookup.
            _intent_rewrite_enabled = DEFAULT_REWRITE_ENABLED_BY_INTENT.get(
                _rewrite_intent, True,
            )
        if not _intent_rewrite_enabled:
            rw_ctx.set_metadata(
                skipped=True,
                reason="per_intent_disabled",
                intent=_rewrite_intent,
            )
            return {"rewritten_query": state["query"]}

        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=rewriting")
        query = state["query"]
        # Thread last 2 history pairs into rewrite so multi-turn pronouns
        # ("có ưu đãi không" after "triệt lông") resolve correctly.
        # Empty history → user_content stays the raw query, byte-identical
        # to pre-2026-05-27 behaviour for first-turn requests.
        _rw_history = state.get("conversation_history", []) or []
        if _rw_history:
            # Last 2 pairs = 4 messages (user/assistant x 2)
            _recent = _rw_history[-4:]
            _hist_lines: list[str] = []
            for m in _recent:
                role_label = (
                    "User" if m.get("role") == "user" else "Assistant"
                )
                content = (m.get("content") or "")[:200]
                _hist_lines.append(f"{role_label}: {content}")
            _user_content = (
                "Conversation context (last turns):\n"
                + "\n".join(_hist_lines)
                + f"\n\nCurrent query to rewrite: {query}\n\n"
                + "Rewrite the current query as a standalone search query, "
                + "expanding any pronouns or implicit references from the "
                + "conversation context (e.g. 'có ưu đãi không' after "
                + "discussing service X should become 'có ưu đãi cho dịch "
                + "vụ X không')."
            )
        else:
            _user_content = query
        messages = [
            {"role": "system", "content": _lang(state).prompt_rewriter},
            {"role": "user", "content": _user_content},
        ]
        payload, _ctx = await _invoke_llm_node(
            state,
            purpose="rewriting",
            messages=messages,
            user_prompt=state["query"],
        )
        rw_ctx.record_llm(
            model_used=str(payload.get("model_name") or "") or None,
            prompt_tokens=int(payload.get("prompt_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or 0),
            cost_usd=float(payload.get("cost_usd") or 0.0),
        )
        rewritten = payload["text"] or state["query"]
        return {"rewritten_query": rewritten}
