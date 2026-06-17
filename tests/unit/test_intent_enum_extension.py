"""`UnderstandOutput.intent` Literal extension regression test.

Per MEGA_VB_VERDICT.md §6 the prior gate
``_intent in ("greeting","chitchat","feedback")`` was DEAD CODE because
the Literal only carried 7 values (factoid, comparison, multi_hop,
aggregation, out_of_scope, greeting, feedback). The classifier could
never emit "chitchat" so short social messages ("em ơi", "khoẻ không",
"thế à") were misrouted to factoid → no_chunks_short_circuit.

Root-cause fix: extend the Literal with two new values:

    chitchat   — short social / meta messages beyond bare greetings
    vu_vo      — vague / non-substantive acknowledgements needing clarity

The chitchat short-circuit gate in ``query_graph.generate`` then matches
on the structured intent directly. The query-pattern heuristic stays as
defense-in-depth for short typo / mis-labeled inputs.

This test file pins:
  1. New intents are present in the Literal + ``_VALID_INTENTS``.
  2. Pydantic schema validates the new values without error.
  3. Pydantic schema rejects unknown values (typo / drift guard).
  4. The chitchat gate routes new intents into the chitchat branch
     (i.e. they SKIP the refuse short-circuit).
  5. The HALLU=0 contract is preserved: a hallu-trap query that
     happens to be short still hits the OOS template.

App-mindset preservation: this test does NOT inject answer text —
it only asserts the gate routes correctly. The actual reply text comes
from the bot owner's system_prompt + LLM (single source of truth).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import get_args
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.dto.llm_schemas import UnderstandOutput
from ragbot.shared.constants import DEFAULT_OOS_ANSWER_TEMPLATE


# ---------------------------------------------------------------------------
# §1 — Literal enum + _VALID_INTENTS exposure
# ---------------------------------------------------------------------------
def test_literal_contains_new_chitchat_value() -> None:
    values = set(get_args(UnderstandOutput.model_fields["intent"].annotation))
    assert "chitchat" in values, (
        f"V2 fix regression: 'chitchat' missing from UnderstandOutput.intent "
        f"Literal. Current values: {sorted(values)}"
    )


def test_literal_contains_new_vu_vo_value() -> None:
    values = set(get_args(UnderstandOutput.model_fields["intent"].annotation))
    assert "vu_vo" in values, (
        f"V2 fix regression: 'vu_vo' missing from UnderstandOutput.intent "
        f"Literal. Current values: {sorted(values)}"
    )


def test_literal_preserves_existing_seven_values() -> None:
    """Adding new values must not drop any of the original 7."""
    values = set(get_args(UnderstandOutput.model_fields["intent"].annotation))
    required = {
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
    }
    missing = required - values
    assert not missing, f"V2 fix regression: original intents missing {missing}"


def test_valid_intents_runtime_list_includes_new_values() -> None:
    """Module-level `_VALID_INTENTS` is computed via get_args(...) on the
    Literal, so it should reflect the schema extension automatically.
    """
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    assert "chitchat" in _VALID_INTENTS, (
        f"_VALID_INTENTS out of sync with schema; got {_VALID_INTENTS}"
    )
    assert "vu_vo" in _VALID_INTENTS, (
        f"_VALID_INTENTS out of sync with schema; got {_VALID_INTENTS}"
    )


def test_valid_intents_factoid_first_invariant_preserved() -> None:
    """Existing invariant from test_understand_intent_determinism — the
    fallback text-scan picks the FIRST matching candidate, so 'factoid'
    must remain at index 0 to prefer retrieval over OOS.
    """
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    assert _VALID_INTENTS[0] == "factoid", (
        f"V2 extension broke ordering; first intent must be 'factoid', "
        f"got '{_VALID_INTENTS[0]}'"
    )


# ---------------------------------------------------------------------------
# §2 — Schema validation accepts new + rejects unknown
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "intent",
    [
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
        "chitchat",
        "vu_vo",
    ],
)
def test_schema_accepts_all_nine_intents(intent: str) -> None:
    parsed = UnderstandOutput(condensed_query="hello", intent=intent)
    assert parsed.intent == intent


def test_schema_rejects_unknown_intent() -> None:
    """Drift guard — typo / hallucinated intent values must fail validation."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UnderstandOutput(condensed_query="hello", intent="smalltalk")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# §3 — End-to-end gate routing inside the generate node
# ---------------------------------------------------------------------------
class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _make_resolver_and_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(name="mock-provider")
    cfg.provider.name = "mock"
    cfg.params = MagicMock()
    cfg.params.max_tokens = None
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "stub answer",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    })
    llm.complete_runtime_stream = None
    return resolver, llm


def _build_generate():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_resolver_and_llm()
    audit_events: list[tuple[str, dict]] = []

    class _CapturingAudit:
        async def log(self, _record_bot_id, _category, event, payload):
            audit_events.append((event, dict(payload)))

    compiled = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        audit_logger=_CapturingAudit(),
    )
    node = compiled.nodes["generate"].bound
    return node.afunc, llm, audit_events


def _make_state(*, intent: str, query: str, graded_chunks=None):
    pc = {
        "structured_output_enabled": False,
        "generate_use_structured_output": False,
        "prompt_compression_enabled": False,
        "lost_in_middle_reorder_enabled": False,
        "condense_history_limit": 6,
        "refuse_short_circuit_enabled": True,
        "oos_answer_template": "",
    }
    return {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "language": "vi",
        "query": query,
        "intent": intent,
        "rewritten_query": None,
        "graded_chunks": graded_chunks or [],
        "conversation_history": [],
        "answer": "",
        "model_used": "mock/model",
        "pipeline_config": pc,
        "step_tracker": _FakeStepTracker(),
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


@pytest.mark.parametrize("intent", ["chitchat", "vu_vo"])
def test_new_intents_skip_refuse_short_circuit(intent: str) -> None:
    """When classifier emits chitchat / vu_vo, the gate must NOT short-circuit
    — control flows through to the LLM so the bot owner's sysprompt can
    handle the friendly micro-reply.
    """
    afunc, llm, events = _build_generate()
    # Use a query long enough that the token-count heuristic does NOT also
    # fire — this isolates the intent gate as the sole reason for skipping.
    state = _make_state(
        intent=intent,
        query=(
            "Đây là một câu rất dài hoàn toàn vượt qua ngưỡng token "
            "chitchat heuristic mặc định để cô lập logic intent gate"
        ),
        graded_chunks=[],
    )
    out = asyncio.run(afunc(state))

    # Must NOT have hit the refuse_short_circuit_fired event.
    fired = [e for e in events if e[0] == "refuse_short_circuit_fired"]
    assert len(fired) == 0, (
        f"intent={intent!r} should bypass refuse short-circuit; got events: "
        f"{[e[0] for e in events]!r}"
    )
    # answer_reason must NOT be no_chunks_short_circuit.
    assert out.get("answer_reason") != "no_chunks_short_circuit", (
        f"intent={intent!r} unexpectedly short-circuited; out={out!r}"
    )
    # llm.complete called at least once (LLM took over).
    assert llm.complete.await_count >= 1, (
        f"intent={intent!r} must reach LLM, but llm.complete was not called"
    )


def test_factoid_with_zero_chunks_still_short_circuits() -> None:
    """HALLU=0 contract: factoid + chunks=0 must still hit OOS template
    when the query is long enough that token-count heuristic does NOT fire.
    """
    afunc, llm, events = _build_generate()
    state = _make_state(
        intent="factoid",
        query=(
            "Cho hỏi giá dịch vụ chăm sóc da kèm phần massage cổ vai gáy "
            "loại standard premium plus là bao nhiêu một buổi"
        ),
        graded_chunks=[],
    )
    out = asyncio.run(afunc(state))

    fired = [e for e in events if e[0] == "refuse_short_circuit_fired"]
    assert len(fired) == 1, (
        f"factoid + chunks=0 + long query MUST short-circuit; got events: "
        f"{[e[0] for e in events]!r}"
    )
    assert llm.complete.await_count == 0
    assert out["answer"] == DEFAULT_OOS_ANSWER_TEMPLATE
    assert out["answer_reason"] == "no_chunks_short_circuit"


def test_chitchat_intent_with_hallu_trap_query_still_skips_short_circuit() -> None:
    """Edge case: when the classifier explicitly labels intent=chitchat we
    trust it — the intent gate fires regardless of token-count or
    trap-keyword overlap. (Trap-keyword check is a defence-in-depth for
    the *pattern* path, not for the explicit-intent path.)
    """
    afunc, llm, events = _build_generate()
    # Query contains a hallu-trap noun ("bia") but classifier says chitchat.
    state = _make_state(
        intent="chitchat",
        query="bia hôm nay",
        graded_chunks=[],
    )
    out = asyncio.run(afunc(state))

    fired = [e for e in events if e[0] == "refuse_short_circuit_fired"]
    assert len(fired) == 0, (
        "explicit intent=chitchat must skip short-circuit even with trap "
        f"keyword; got events: {[e[0] for e in events]!r}"
    )
    assert out.get("answer_reason") != "no_chunks_short_circuit"
    assert llm.complete.await_count >= 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
