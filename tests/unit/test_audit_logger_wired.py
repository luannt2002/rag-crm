"""build_graph wires audit_logger into nodes that emit pipeline-trace events.

Verifies the kwarg is accepted, that the emitter is invoked at least once
during a full run, and that one of the documented event names fires.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_kw: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _resolver_and_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(api_key="x", base_url=None, code="mock", timeout_ms=5000)
    cfg.params = MagicMock(temperature=0.0, max_tokens=128)
    cfg.pricing = None
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "answer", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })
    return resolver, llm


def test_build_graph_accepts_audit_logger_kwarg():
    """build_graph signature exposes audit_logger keyword."""
    from ragbot.orchestration.query_graph import build_graph

    sig = inspect.signature(build_graph)
    assert "audit_logger" in sig.parameters
    # Must be optional (default None) so existing callers don't break.
    assert sig.parameters["audit_logger"].default is None


def test_audit_logger_log_called_during_pipeline_run():
    """A graph run with audit_logger fires at least one ``log(...)`` call."""
    from ragbot.orchestration.query_graph import build_graph

    audit = MagicMock()
    audit.log = AsyncMock()

    resolver, llm = _resolver_and_llm()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        audit_logger=audit,
    )

    initial = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 7,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "Test question",
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}

    asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 25}))
    assert audit.log.await_count > 0, (
        "audit_logger.log was never awaited during the pipeline run"
    )

    # Pull out the event names emitted — at least one is a known pipeline event.
    emitted_events = {call.args[2] for call in audit.log.await_args_list}
    known_events = {
        "query_received",
        "intent_extracted",
        "cache_check",
        "query_completed",
        "grade_executed",
    }
    assert emitted_events & known_events, (
        f"no known pipeline event emitted; saw={emitted_events}"
    )


def test_audit_logger_failure_does_not_break_pipeline():
    """If audit emit raises, the pipeline still completes (observability never breaks)."""
    from ragbot.orchestration.query_graph import build_graph

    audit = MagicMock()
    audit.log = AsyncMock(side_effect=RuntimeError("disk full"))

    resolver, llm = _resolver_and_llm()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        audit_logger=audit,
    )

    initial = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 8,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "Q",
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}

    final = asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 25}))
    assert "answer" in final and isinstance(final["answer"], str), (
        "pipeline must reach a terminal answer field even when audit emit raises"
    )


def test_chat_worker_passes_audit_logger():
    """chat_worker forwards audit_logger via the canonical DI builder.

    Post ADR-W1-DI every callsite goes through build_graph_di_kwargs, whose
    alias map binds audit_logger ← container.pipeline_audit_logger; the AST
    parity test guards the callsite, this pins the alias mapping itself."""
    import pathlib

    # chat_worker was split into a package — concatenate every sub-module.
    _cw_dir = pathlib.Path("src/ragbot/interfaces/workers/chat_worker")
    src = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(_cw_dir.glob("*.py"))
    )
    assert "get_graph(**build_graph_di_kwargs(container))" in src, (
        "chat_worker must use the canonical DI builder (ADR-W1-DI)"
    )
    from ragbot.orchestration.graph_assembly import _PROVIDER_ALIASES

    assert _PROVIDER_ALIASES.get("audit_logger") == "pipeline_audit_logger", (
        "builder alias map must bind audit_logger ← pipeline_audit_logger"
    )


def test_chat_stream_passes_audit_logger():
    """chat_stream forwards audit_logger via the canonical DI builder."""
    import pathlib

    src = pathlib.Path("src/ragbot/interfaces/http/routes/chat_stream.py").read_text(
        encoding="utf-8",
    )
    assert "get_graph(**build_graph_di_kwargs(container))" in src, (
        "chat_stream must use the canonical DI builder (ADR-W1-DI)"
    )


def test_test_chat_passes_audit_logger():
    """Both test_chat call sites forward audit_logger via the canonical builder."""
    import pathlib

    src = pathlib.Path(
        "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py"
    ).read_text(
        encoding="utf-8",
    )
    assert src.count("get_graph(**build_graph_di_kwargs(container))") >= 2, (
        "test_chat must use the canonical DI builder at both call sites (ADR-W1-DI)"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
