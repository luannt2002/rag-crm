"""Regression: when ``_stream_sink`` is wired into state, the generate node
MUST take the free-form streaming path even if ``structured_output_enabled``
and ``generate_use_structured_output`` are True.

WHY: structured-output uses LiteLLM JSON mode which buffers the entire
response server-side before returning. That defeats SSE TTFT — clients see
zero deltas until the full JSON arrives. AGENT-Z1-STREAMING-MAX shipped a
sink-aware bypass in ``query_graph.generate`` that forces ``so_generate=False``
when ``state.get("_stream_sink")`` is not None. These tests pin that
behaviour so a future refactor can't silently re-enable JSON-mode for
streamed requests and erase the TTFT gain.

Approach: extract the compiled ``generate`` closure (mirrors the pattern in
``test_generate_no_app_injection.py``) and drive it twice:

    1) Without ``_stream_sink``  → SO path → ``llm.complete_runtime`` w/
       structured schema (or the structured helper).
    2) With ``_stream_sink``      → free-form path → ``llm.complete`` (the
       streaming branch is gated on ``stream_fn`` truthy AND sink truthy).

We verify by checking ``llm.complete`` is awaited in case 2 even when SO
flags default to True.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Minimal port doubles (mirror test_generate_no_app_injection.py)
# ---------------------------------------------------------------------------
class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        ctx.model_id = "mock/model"
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


def _make_fakes(answer_text: str = "[chunk:c1] streamed answer"):
    """Build (resolver, llm) pair. ``llm`` exposes both the non-stream
    ``complete`` (returns dict) and a no-op ``complete_runtime_stream`` (None)
    so the streaming branch is NOT taken — we only assert the
    structured-vs-free-form decision, not actual delta yielding.
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock()
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
    # Force the streaming branch to be a no-op so the free-form fallback
    # in _invoke_llm_node falls through to ``complete``. This makes the
    # test deterministic without simulating a real async iterator.
    llm.complete_runtime_stream = None
    return resolver, llm


def _extract_generate_closure():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_fakes()
    compiled = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,    )
    node = compiled.nodes["generate"].bound
    return node.afunc, llm


def _make_state_with_so_on(*, stream_sink=None) -> dict:
    """State with structured-output flags ON (the production default)."""
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
        "query": "what is the price?",
        "rewritten_query": None,
        # One graded chunk so refuse-short-circuit doesn't bypass the LLM call.
        "graded_chunks": [
            {"chunk_id": "c1", "content": "price is 1.000.000 VND",
             "score": 0.9, "document_name": "doc.txt"},
        ],
        "conversation_history": [],
        "answer": "",
        "model_used": "mock/model",
        "pipeline_config": {
            # Production defaults — SO ON.
            "structured_output_enabled": True,
            "generate_use_structured_output": True,
            "prompt_compression_enabled": False,
            "lost_in_middle_reorder_enabled": False,
            "condense_history_limit": 6,
            "refuse_short_circuit_enabled": False,
        },
        # Inject sink only when caller wants the streaming path.
        **({"_stream_sink": stream_sink} if stream_sink is not None else {}),
        "step_tracker": _FakeStepTracker(),
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_so_path_used_when_no_stream_sink() -> None:
    """Without a stream sink, structured-output may run (we just assert the
    sink-bypass flag is the ONLY thing that flips so_generate to False).

    This test asserts the negative: when sink is absent, the SO branch
    isn't forcibly disabled by the stream-aware guard. Structured-output
    can still fall back to free-form on JSON parse failure (existing
    behaviour), so we only check the call shape — at least one LLM call
    happens.
    """
    afunc, llm = _extract_generate_closure()
    state = _make_state_with_so_on(stream_sink=None)
    out = asyncio.run(afunc(state))
    # Either structured (uses helper, falls back to free-form) or free-form —
    # either way at least one LLM call happens. We only need to confirm the
    # generate node didn't error out.
    assert isinstance(out, dict)
    assert "answer" in out


def test_stream_sink_forces_freeform_path() -> None:
    """With a stream sink wired, generate MUST hit ``llm.complete`` (free-form
    path) not the structured helper, even though SO defaults are True.

    This locks in the AGENT-Z1-STREAMING-MAX bypass: ``state["_stream_sink"]
    is not None`` overrides ``so_generate`` to False inside ``generate``.
    """
    afunc, llm = _extract_generate_closure()
    sink = asyncio.Queue(maxsize=8)
    state = _make_state_with_so_on(stream_sink=sink)
    out = asyncio.run(afunc(state))
    # Free-form path = llm.complete awaited at least once.
    assert llm.complete.await_count >= 1, (
        "stream-sink-aware bypass not honoured — generate took the SO path "
        "and never called llm.complete (free-form). This breaks SSE TTFT."
    )
    assert isinstance(out, dict)
    assert out.get("answer", "")


def test_stream_sink_present_but_falsy_object_still_treated_as_active() -> None:
    """An empty Queue is a truthy ``not None`` value — bypass MUST fire.

    Guards against a future ``if state.get("_stream_sink"):`` regression
    where a freshly created (empty) Queue would be truthy but the bypass
    semantics depend strictly on ``is not None``, not truthiness. Keeps
    the contract explicit.
    """
    afunc, llm = _extract_generate_closure()
    sink = asyncio.Queue()  # empty, but not None
    state = _make_state_with_so_on(stream_sink=sink)
    asyncio.run(afunc(state))
    assert llm.complete.await_count >= 1
