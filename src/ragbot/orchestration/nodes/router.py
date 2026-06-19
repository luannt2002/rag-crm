"""Intent-routing node — classifies the query intent via a light LLM call.

Extracted from ``query_graph.build_graph``. di_kwargs (``model_resolver``,
``llm``) and the builder helpers (``_lang``, ``_invoke_llm_node``) are threaded
in as kwargs, bound via ``functools.partial`` in the graph builder.
"""
from __future__ import annotations

from typing import Any, get_args

from ragbot.application.dto.llm_schemas import UnderstandOutput
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_INTENT_FALLBACK
from ragbot.shared.errors import InvariantViolation

# Valid intent labels, lifted from the UnderstandOutput schema (single source
# of truth). Recomputed here rather than imported from query_graph to avoid an
# import cycle (query_graph imports this module).
_VALID_INTENTS: list[str] = list(get_args(UnderstandOutput.model_fields["intent"].annotation))


async def router(
    state: GraphState,
    *,
    model_resolver: Any,
    llm: Any,
    _lang: Any,
    _invoke_llm_node: Any,
) -> dict:
    async with state["step_tracker"].step("router"):
        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=routing")
        messages = [
            {"role": "system", "content": _lang(state).prompt_understand},
            {"role": "user", "content": f"<question>{state['query']}</question>"},
        ]
        payload, _ctx = await _invoke_llm_node(
            state,
            purpose="routing",
            messages=messages,
            user_prompt=state["query"],
        )
        raw = (payload["text"] or "").strip().lower()
        intent = DEFAULT_INTENT_FALLBACK
        for cand in _VALID_INTENTS:
            if cand in raw:
                intent = cand
                break
        update: dict = {"intent": intent}
        return update
