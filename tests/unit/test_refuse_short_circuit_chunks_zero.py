"""F1a regression: `generate` short-circuits when graded_chunks is empty.

Per CLAUDE.md app-mindset rule + F9 cost audit (90% NEW-half refuse turns
paid $0.00067/turn for a 56-token canned answer that adds zero quality):

    When ``state["graded_chunks"]`` is empty, the generate node MUST:
        1. Skip the LLM call entirely (no `llm.complete` await).
        2. Return the bot owner's ``oos_answer_template`` directly (read
           via ``_pcfg`` from ``pipeline_config.oos_answer_template``).
        3. Fall back to ``DEFAULT_OOS_ANSWER_TEMPLATE`` when the bot has
           not configured one.
        4. Emit an audit event ``refuse_short_circuit_fired``.
        5. Set ``answer_type="no_context"`` and
           ``answer_reason="no_chunks_short_circuit"``.
        6. Proceed normally (LLM called) when graded_chunks is non-empty.

App-mindset preservation:
    - The returned text comes from the bot owner DB column (or a
      domain-neutral fallback constant) — application does NOT inject
      its own answer phrasing.
    - Bot owner override path: ``pipeline_config.refuse_short_circuit_enabled``
      to disable, ``bots.oos_answer_template`` to customize the text.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.shared.constants import DEFAULT_OOS_ANSWER_TEMPLATE


# ---------------------------------------------------------------------------
# Minimal port doubles (mirror test_generate_no_app_injection.py shapes)
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


def _make_fakes(answer_text: str = "stub answer") -> tuple[MagicMock, MagicMock]:
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
        "text": answer_text,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    })
    llm.complete_runtime_stream = None
    return resolver, llm


def _build_generate(
    *, bot_system_prompt: str = "Bot owner system prompt.",
) -> tuple[callable, MagicMock, list]:
    """Return (generate_afunc, llm_mock, audit_events_list).

    audit_events_list captures every (event, payload) tuple emitted by
    the node so tests can assert ``refuse_short_circuit_fired`` fired.
    """
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_fakes()
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


def _make_state(
    *,
    graded_chunks: list[dict] | None = None,
    pipeline_overrides: dict | None = None,
) -> dict:
    pc = {
        "structured_output_enabled": False,
        "generate_use_structured_output": False,
        "prompt_compression_enabled": False,
        "lost_in_middle_reorder_enabled": False,
        "condense_history_limit": 6,
        # F1a default: short-circuit ENABLED. Tests can override.
        "refuse_short_circuit_enabled": True,
        "oos_answer_template": "",
    }
    if pipeline_overrides:
        pc.update(pipeline_overrides)
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
        "query": "Câu hỏi gì đó",
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_zero_chunks_returns_template_no_llm_call() -> None:
    """F1a: graded_chunks=[] → return template, llm.complete NOT called."""
    afunc, llm, _events = _build_generate()
    state = _make_state(graded_chunks=[])
    out = asyncio.run(afunc(state))

    # Cost win: zero LLM calls on the refuse short-circuit path.
    assert llm.complete.await_count == 0, (
        "F1a violation: llm.complete was called even though graded_chunks=[]"
    )
    # Falls back to constant when bot template empty.
    assert out["answer"] == DEFAULT_OOS_ANSWER_TEMPLATE
    assert out["answer_type"] == "no_context"
    assert out["answer_reason"] == "no_chunks_short_circuit"
    assert out["chunks_used"] == 0


def test_zero_chunks_uses_bot_oos_template_when_set() -> None:
    """When bot owner sets ``oos_answer_template``, that text is returned
    verbatim (single source of truth = DB column, not the constant).
    """
    bot_template = "Em chưa có thông tin, anh/chị liên hệ hotline 0000 nhé."
    afunc, llm, _events = _build_generate()
    state = _make_state(
        graded_chunks=[],
        pipeline_overrides={"oos_answer_template": bot_template},
    )
    out = asyncio.run(afunc(state))

    assert llm.complete.await_count == 0
    assert out["answer"] == bot_template
    assert out["answer"] != DEFAULT_OOS_ANSWER_TEMPLATE
    assert out["answer_type"] == "no_context"


def test_nonzero_chunks_proceeds_to_llm() -> None:
    """When graded_chunks is non-empty the LLM call MUST still fire —
    short-circuit only applies on the empty-chunks branch.
    """
    afunc, llm, _events = _build_generate()
    chunk = {
        "chunk_id": "00000000-0000-0000-0000-000000000001",
        "text": "Doc content here.",
        "document_name": "doc.pdf",
        "chunk_index": 0,
    }
    state = _make_state(graded_chunks=[chunk])
    out = asyncio.run(afunc(state))

    assert llm.complete.await_count >= 1, (
        "non-empty graded_chunks must NOT short-circuit — LLM must be called"
    )
    # answer is the LLM stub text, NOT the OOS template.
    assert out.get("answer") != DEFAULT_OOS_ANSWER_TEMPLATE
    assert out.get("answer_reason") != "no_chunks_short_circuit"


def test_short_circuit_audit_event_emitted() -> None:
    """The short-circuit path emits a structured audit event so ops can
    quantify the cost-win and verify the gate fires as expected.
    """
    afunc, llm, events = _build_generate()
    state = _make_state(graded_chunks=[])
    asyncio.run(afunc(state))

    assert llm.complete.await_count == 0
    fired = [e for e in events if e[0] == "refuse_short_circuit_fired"]
    assert len(fired) == 1, (
        f"expected exactly 1 refuse_short_circuit_fired audit event, "
        f"got {len(fired)}; all events: {[e[0] for e in events]!r}"
    )
    payload = fired[0][1]
    assert payload.get("template_source") in ("bot_oos_template", "default_constant")
    assert isinstance(payload.get("template_chars"), int)
    assert payload["template_chars"] > 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
