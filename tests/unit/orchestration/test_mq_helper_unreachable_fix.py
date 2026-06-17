"""Mega-sprint G23 — fix unreachable code in ``_run_multi_query_expansion``.

Background. The closure ``_run_multi_query_expansion`` carried two
mutually-dominating early-exit guards:

    if len(sub_queries_state) <= 1:
        state["fanout_bypassed"] = True
        return []
    if len(sub_queries_state) >= 2:
        return []

Every possible value of ``len`` is captured by either ``<= 1`` or
``>= 2`` — the helper therefore ALWAYS returns ``[]``, and every
statement after line 1810 (~170 lines of MQ paraphrase logic) is dead.

The two return blocks have opposite intents:

* Z2 commit (de6573f, 2026-05-01) added ``>= 2: return []`` — bypass
  when decompose already produced sub-queries.
* S2 commit (3dc4159, 2026-05-12) added ``<= 1: return []`` with the
  comment "skip paraphrase fanout when decompose did not produce ≥2
  sub-queries". This was the SAME inverted-gate bug that bit the
  inline retrieve fanout block, fixed in 8ec1eb9 (2026-05-15) for the
  inline path but never carried over to this helper.

Inline post-fix gate semantics (8ec1eb9): bypass when
``decompose_active OR _has_preset_mq`` — i.e. when sub-queries already
exist. Otherwise the LLM-paraphrase fanout MUST run; that branch is
the retrieval lever for compound queries like the regression case
"Điều 38 và 3" (Case B 2026-05-14).

Fix. Apply the same gate inversion to the helper. Drop the buggy
``<= 1: return []`` block; keep the ``>= 2`` block but mark it as the
bypass (writes ``state["fanout_bypassed"] = True``). The helper then
runs MQ paraphrase exactly when called by ``rewrite_and_mq_parallel``
from the rewrite branch (where ``sub_queries`` is empty by graph
topology — decompose lives on a sibling branch).

Test strategy.

1. **Static guard** — source must NOT contain the buggy
   ``len(sub_queries_state) <= 1: return []`` sequence; must contain the
   bypass at ``len(sub_queries_state) >= 2`` followed by the flag write.
2. **Reachability** — the LLM-call section after the bypass guards must
   be reached when ``sub_queries`` is empty (typical caller condition).
   Direct closure-call is not possible (it lives inside ``build_graph``);
   we drive it through ``rewrite_and_mq_parallel`` and observe whether
   the LLM mock's ``complete`` is invoked with ``purpose="multi_query"``.
"""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.orchestration import query_graph as qg


# --------------------------------------------------------------------------- #
# 1. Source-level guards — pin the post-fix shape of the helper.              #
# --------------------------------------------------------------------------- #


def _extract_helper_source(name: str) -> str:
    """Pull a nested ``async def <name>`` block out of build_graph.

    Mirrors the helper used by the existing fanout-bypass test file —
    trims by sibling-indent so we get just the helper body.
    """
    build_src = inspect.getsource(qg.build_graph)
    lines = build_src.splitlines(keepends=True)
    start = None
    indent = None
    for i, line in enumerate(lines):
        if f"async def {name}(" in line:
            start = i
            indent = len(line) - len(line.lstrip(" "))
            break
    assert start is not None, f"helper {name!r} not found in build_graph"
    assert indent is not None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent <= indent and (
            line.lstrip().startswith("async def ")
            or line.lstrip().startswith("def ")
        ):
            end = j
            break
    return "".join(lines[start:end])


def test_helper_does_not_short_circuit_on_le_one_sub_queries() -> None:
    """The buggy ``<= 1: return []`` block must be gone.

    Pre-fix: that block intercepted EVERY call from
    ``rewrite_and_mq_parallel`` (which hands the helper a state with
    no sub_queries — decompose is on a sibling branch). The LLM
    paraphrase logic was unreachable.
    """
    src = _extract_helper_source("_run_multi_query_expansion")
    assert "if len(sub_queries_state) <= 1:" not in src, (
        "Buggy bypass ``if len(sub_queries_state) <= 1: return []`` "
        "re-introduced. Inline post-fix gate semantics (8ec1eb9): "
        "bypass when sub-queries ALREADY exist (>= 2); fanout MUST "
        "run when called with no sub-queries (the typical "
        "rewrite_and_mq_parallel call-site condition)."
    )


def test_helper_bypasses_only_when_decompose_already_produced_sub_queries() -> None:
    """The decompose-precedence bypass (``>= 2``) must remain — when
    the caller already supplied ≥2 sub-queries, paraphrase fanout is
    redundant. The bypass also writes ``fanout_bypassed = True`` so
    downstream readers can detect the skip.
    """
    src = _extract_helper_source("_run_multi_query_expansion")
    assert "if len(sub_queries_state) >= 2:" in src, (
        "Decompose-precedence bypass ``len(sub_queries_state) >= 2`` "
        "removed — would let MQ paraphrase fire on a real multi-hop "
        "decomposition (decompose's job is sub-question split; "
        "paraphrase is for the single-query expansion path)."
    )
    assert 'state["fanout_bypassed"] = True' in src, (
        "Bypass flag write missing from the helper — downstream "
        "(tests, metrics, traces) reads ``fanout_bypassed`` to detect "
        "skipped paraphrase."
    )


# --------------------------------------------------------------------------- #
# 2. Behavioral — drive ``rewrite_and_mq_parallel`` and observe the helper.   #
# --------------------------------------------------------------------------- #
#
# The helper is a closure inside ``build_graph``; we drive it through the
# public ``rewrite_and_mq_parallel`` node by invoking the compiled graph
# from the ``rewrite_and_mq_parallel`` entry. To keep the surface tiny
# we hand-craft a minimal state and call the wrapper directly via the
# node-table that LangGraph exposes on the compiled graph.
#
# The expected behaviour after the fix: when ``sub_queries`` is empty
# (the typical caller condition from the rewrite branch), the helper
# REACHES the LLM-call section and invokes ``llm.complete`` with
# ``purpose="multi_query"``. Pre-fix it short-circuited and never called
# the LLM — that is the bug.
# --------------------------------------------------------------------------- #


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _FakeEmbedder:
    async def embed(self, texts, **_kw):
        if isinstance(texts, list):
            return [[0.1] * 8 for _ in texts]
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


class _RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict = {}

    def set_metadata(self, **kwargs) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs) -> None:
        return None

    def record_llm(self, **_kw) -> None:
        """Wave M3.2 — no-op mirror of StepContext.record_llm."""
        pass

    def record(self, **_kwargs) -> None:
        return None


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.steps: list[_RecordingStepCtx] = []

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx(name)
        self.steps.append(ctx)
        yield ctx

    def names(self) -> list[str]:
        return [s.name for s in self.steps]


def _resolver_and_llm_calls() -> tuple[MagicMock, MagicMock, list[dict]]:
    """LLM mock that records every ``complete`` call so we can observe
    whether the multi_query branch fired.
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/router-model"
    cfg.model_name = "mock/router-model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock()
    cfg.provider.name = "mock-provider"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    calls: list[dict] = []

    async def _complete(_cfg, messages, **kw):
        calls.append({
            "purpose": kw.get("purpose"),
            "n_messages": len(messages),
        })
        purpose = kw.get("purpose", "")
        if purpose == "multi_query":
            return {
                "text": '["alt phrasing one", "alt phrasing two"]',
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        return {
            "text": "rewritten query text",
            "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm, calls


def _base_parallel_state(tracker: _RecordingStepTracker) -> dict:
    return {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "câu hỏi mẫu nhiều token cho gating min tokens",
        "rewritten_query": None,
        # Empty sub_queries — caller condition from rewrite branch
        # (decompose is on a sibling branch and has not run).
        "sub_queries": [],
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        # Use aggregation intent — multi_query is enabled for complex intents.
        "intent": "aggregation",
        "pipeline_config": {
            # MQ enabled with > 1 variants so the gate at line 1797
            # does not short-circuit; the buggy <= 1 / >= 2 pair is the
            # bug under test.
            "multi_query_enabled": True,
            "multi_query_n_variants": 3,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "multi_query_min_tokens": 1,
            "multi_query_skip_chitchat_intent": False,
            "multi_query_entity_gate_enabled": False,
            "pipeline_parallel_rewrite_mq_enabled": True,
            "embedding_model": "mock/model",
            "embedding_dimension": 8,
        },
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


def _build_compiled():
    resolver, llm, calls = _resolver_and_llm_calls()
    compiled = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        llm=llm,
        model_resolver=resolver,
        embedder=_FakeEmbedder(),
    )
    return compiled, calls


def _invoke_node(compiled, node_name: str, state: dict) -> None:
    """Pull the registered node out of the compiled graph and call it.

    LangGraph exposes ``compiled.nodes`` as a dict of
    ``PregelNode`` objects; the underlying user callable hangs off
    ``.bound`` (Runnable). Driving the node directly avoids running the
    entire pipeline — we only need to observe whether the helper inside
    ``rewrite_and_mq_parallel`` reaches the LLM-call site.
    """
    pregel_node = compiled.nodes[node_name]
    runnable = pregel_node.bound
    asyncio.run(runnable.ainvoke(state))


def test_rewrite_and_mq_parallel_invokes_multi_query_llm_when_no_sub_queries() -> None:
    """When ``rewrite_and_mq_parallel`` runs with no sub_queries (the
    typical caller condition from the rewrite branch), the helper MUST
    reach the LLM-call section and invoke ``llm.complete`` with
    ``purpose="multi_query"``.

    Pre-fix: ``<= 1: return []`` short-circuited every call ⇒ zero
    multi_query LLM invocations recorded.

    Post-fix: gate inverted ⇒ multi_query LLM invocation observed.
    """
    compiled, calls = _build_compiled()
    tracker = _RecordingStepTracker()
    state = _base_parallel_state(tracker)

    _invoke_node(compiled, "rewrite_and_mq_parallel", state)

    mq_calls = [c for c in calls if c.get("purpose") == "multi_query"]
    assert len(mq_calls) >= 1, (
        "rewrite_and_mq_parallel did not invoke the multi_query LLM "
        "branch — the helper short-circuited before the LLM call. "
        f"All recorded LLM calls: {calls!r}. Step names recorded: "
        f"{tracker.names()!r}. Expected ≥ 1 call with "
        "purpose='multi_query'."
    )


def test_rewrite_and_mq_parallel_skips_multi_query_when_sub_queries_already_present() -> None:
    """Decompose-precedence — when the caller already supplied ≥2
    sub-queries, the helper MUST bypass (no LLM call). This pins the
    other half of the gate post-fix.
    """
    compiled, calls = _build_compiled()
    tracker = _RecordingStepTracker()
    state = _base_parallel_state(tracker)
    state["sub_queries"] = ["sub one", "sub two"]

    _invoke_node(compiled, "rewrite_and_mq_parallel", state)

    mq_calls = [c for c in calls if c.get("purpose") == "multi_query"]
    assert len(mq_calls) == 0, (
        "Decompose-precedence broken — helper invoked multi_query LLM "
        "even though caller already supplied ≥2 sub-queries. "
        f"Recorded LLM calls: {calls!r}."
    )
