"""Shared helpers for orchestration node unit tests.

Each `tests/unit/test_node_*.py` file needs to:

1. Construct a minimal `GraphState` dict (helper: `make_state`).
2. Stub `model_resolver` + `llm` + the few other ports `build_graph`
   requires (helper: `build_test_graph`).
3. Reach into the compiled graph and invoke a single node via
   `compiled.nodes['<name>'].bound.afunc` (helper: `node_callable`).

Keeping these helpers in one place avoids ~150 LoC of boilerplate per
test file and ensures all node tests use the *same* mocking surface so
behaviour drift in one node test cannot mask a regression in another.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

# Tracker last built via ``build_test_graph`` is exposed here so that
# ``make_state`` (called separately by the test) defaults to the same
# instance the test fixture is keeping a handle on. Without this, the
# tracker passed to a node would be a fresh instance and the test's
# ``tracker.by_name(...)`` assertions would always see zero steps.
_LAST_TEST_TRACKER: list[Any] = []
# Same idea for the optional kg_service / session_factory / bot_system_prompt
# pair: a test that wires them through ``build_test_graph`` expects the next
# ``make_state`` call (without explicit kwargs) to surface them on state.
_LAST_TEST_KG_SERVICE: list[Any] = []
_LAST_TEST_SESSION_FACTORY: list[Any] = []
_LAST_TEST_BOT_SYSTEM_PROMPT: list[Any] = []

# --------------------------------------------------------------------------- #
# Recording fakes                                                             #
# --------------------------------------------------------------------------- #


class RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict[str, Any] = {}
        self.model_used: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def set_metadata(self, **kwargs: Any) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs: Any) -> None:
        return None

    def record(self, **_kwargs: Any) -> None:
        return None

    def record_llm(
        self,
        *,
        model_used: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Wave M3.2 — mirrors StepContext.record_llm contract."""
        if model_used is not None:
            self.model_used = model_used
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens
        self.cost_usd += cost_usd


class RecordingStepTracker:
    """Captures every step name so tests can assert wrapping."""

    def __init__(self) -> None:
        self.steps: list[RecordingStepCtx] = []

    @asynccontextmanager
    async def step(self, name: str, **_kw: Any):
        ctx = RecordingStepCtx(name)
        self.steps.append(ctx)
        yield ctx

    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def by_name(self, name: str) -> list[RecordingStepCtx]:
        return [s for s in self.steps if s.name == name]


class RecordingAuditLogger:
    """Captures every (kind, event, data) triple emitted by `_audit`."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, dict[str, Any]]] = []

    async def log(
        self,
        bot_id: str,
        kind: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        self.events.append((bot_id, kind, event, data))

    def by_event(self, event: str) -> list[dict[str, Any]]:
        return [data for _bot, _kind, ev, data in self.events if ev == event]


class FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw: Any):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class FakeGuardrail:
    async def check_input(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        return []

    async def check_output(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        return []


# --------------------------------------------------------------------------- #
# Resolver / LLM stubs                                                        #
# --------------------------------------------------------------------------- #


def make_resolver_and_llm(
    *,
    text_response: str = "ok",
    structured_response: Any = None,
    embedding_dim: int = 8,
):
    """Create a paired (resolver, llm) test double.

    `text_response` is returned by `llm.complete(...)`. `structured_response`,
    if not None, is returned by the structured-output LLM module path; the
    matching `_litellm_module` stub is attached to the router so the
    structured branch in `query_graph.py` can find it.
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = embedding_dim
    cfg.provider = MagicMock(code="mock", name="mock", timeout_ms=10_000)
    cfg.params = MagicMock(max_tokens=128)
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    llm = MagicMock()

    async def _complete(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        # The orchestrator calls this both positionally and via keyword
        # (`messages=`); accept anything so test doubles never break on
        # signature drift in `_invoke_llm_node`.
        return {
            "text": text_response,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }

    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm, cfg


# --------------------------------------------------------------------------- #
# State builder                                                               #
# --------------------------------------------------------------------------- #


def make_state(
    *,
    query: str = "câu hỏi mẫu",
    history: list[dict[str, str]] | None = None,
    pipeline_config: dict[str, Any] | None = None,
    step_tracker: Any | None = None,
    bot_system_prompt: str = "",
    kg_service: Any | None = None,
    session_factory: Any | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal GraphState dict for node tests."""
    base: dict[str, Any] = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": query,
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
        "intent": "factoid",
        "conversation_history": history or [],
        "pipeline_config": pipeline_config or {},
        "language": "vi",
        # Per-request fields lifted out of build_graph closure. When the
        # caller did not pass an explicit tracker, reuse the last one
        # built via ``build_test_graph`` so the test fixture's reference
        # observes node-level ``step(...)`` calls. Falls back to a fresh
        # instance only if no graph has been built (e.g. pure state
        # builder tests).
        "step_tracker": (
            step_tracker
            if step_tracker is not None
            else (_LAST_TEST_TRACKER[-1] if _LAST_TEST_TRACKER else RecordingStepTracker())
        ),
        "bot_system_prompt": (
            bot_system_prompt
            if bot_system_prompt
            else (_LAST_TEST_BOT_SYSTEM_PROMPT[-1] if _LAST_TEST_BOT_SYSTEM_PROMPT else "")
        ),
        "kg_service": (
            kg_service
            if kg_service is not None
            else (_LAST_TEST_KG_SERVICE[-1] if _LAST_TEST_KG_SERVICE else None)
        ),
        "session_factory": (
            session_factory
            if session_factory is not None
            else (_LAST_TEST_SESSION_FACTORY[-1] if _LAST_TEST_SESSION_FACTORY else None)
        ),
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Graph builder                                                               #
# --------------------------------------------------------------------------- #


def build_test_graph(
    *,
    step_tracker: RecordingStepTracker | None = None,
    audit_logger: RecordingAuditLogger | None = None,
    text_response: str = "ok",
    semantic_cache: Any | None = None,
    kg_service: Any | None = None,
    session_factory: Any | None = None,
    vector_store: Any | None = None,
    embedder: Any | None = None,
    bot_system_prompt: str = "",
    llm_override: Any | None = None,
    resolver_override: Any | None = None,
):
    """Compile a graph with the given test doubles.

    Returns `(compiled, tracker, audit, resolver, llm)` so individual
    tests can keep references to assert on.
    """
    from ragbot.orchestration.query_graph import build_graph

    tracker = step_tracker or RecordingStepTracker()
    _LAST_TEST_TRACKER.append(tracker)
    _LAST_TEST_KG_SERVICE.append(kg_service)
    _LAST_TEST_SESSION_FACTORY.append(session_factory)
    _LAST_TEST_BOT_SYSTEM_PROMPT.append(bot_system_prompt)
    audit = audit_logger or RecordingAuditLogger()
    if llm_override is not None and resolver_override is not None:
        resolver, llm = resolver_override, llm_override
    else:
        resolver, llm, _cfg = make_resolver_and_llm(text_response=text_response)

    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vector_store or MagicMock(),
        embedder=embedder or MagicMock(),
        semantic_cache=semantic_cache,
        audit_logger=audit,
    )
    # Per-request fields are now carried on state, not closed over at
    # build time. Tests that drive the graph via ``graph.ainvoke(state)``
    # must put these into the state dict (``make_state`` does this for
    # them); tests that only inspect the compiled graph topology can
    # ignore them.
    _ = (tracker, bot_system_prompt, kg_service, session_factory)
    return compiled, tracker, audit, resolver, llm


def node_callable(compiled: Any, name: str):
    """Reach into the compiled graph to extract a node's async closure.

    LangGraph compiles each `add_node(name, func)` into a PregelNode whose
    `.bound.afunc` is the original coroutine. Tests that need to drive a
    single node with controlled state — without invoking the whole graph —
    use this accessor.
    """
    node = compiled.nodes[name]
    afunc = getattr(node.bound, "afunc", None)
    if afunc is None:
        raise AttributeError(f"node '{name}' has no bound.afunc closure")
    return afunc


__all__ = [
    "FakeGuardrail",
    "FakeInvocationLogger",
    "RecordingAuditLogger",
    "RecordingStepCtx",
    "RecordingStepTracker",
    "build_test_graph",
    "make_resolver_and_llm",
    "make_state",
    "node_callable",
]
