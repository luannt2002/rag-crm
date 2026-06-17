"""T5 — CRAG grade step observability: batch-vs-per-chunk path metadata.

deepdive lever 3 — currently the ``grade`` step row in
``request_steps`` does not distinguish:

* **batch path** — single LLM call grades all reranked chunks
  (``GradeBatchOutput`` schema, ``grade_use_batch=True`` default).
* **per-chunk fallback** — ``N`` parallel LLM calls when the batch path
  fails parsing or returns an empty list (``GradeOutput`` schema).

When grade ``p95`` spikes (e.g. R8 OLD ~5500ms suspected), ops cannot
tell which path drove the spike without re-running with debug logs. The
fix extends the existing ``step_tracker.step("grade")`` metadata with:

* ``grade_path`` — ``"batch"`` | ``"per_chunk_fallback"``
* ``n_chunks_input`` — chunk count entering the node
* ``n_relevant`` / ``n_irrelevant`` / ``n_ambiguous`` — CRAG 3-state counts
* ``structured_output_used`` — bool, True iff parsed grades produced

No new step name. No prompt change. No LLM-answer override. Pure
observability — backward-compat metadata extension.

Drives the **live** grade node via the ``test_t2_perf_fixes`` harness:
patch the module-level ``_call_with_schema`` import to control which
path the grade body takes (batch returns parsed → batch path; batch
returns ``None`` → per-chunk fallback).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Recording fakes (mirrors test_t2_perf_fixes.py / phase_a/c harness)         #
# --------------------------------------------------------------------------- #


class _RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict = {}

    def set_metadata(self, **kwargs) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs) -> None:
        return None

    def record(self, **_kwargs) -> None:
        return None

    def record_llm(self, **_kwargs) -> None:
        return None


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.steps: list[_RecordingStepCtx] = []

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx(name)
        self.steps.append(ctx)
        yield ctx

    def by_name(self, name: str) -> list[_RecordingStepCtx]:
        return [s for s in self.steps if s.name == name]


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


def _resolver_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "ok", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })
    return resolver, llm


def _grade_state(*, n_chunks: int, grade_use_batch: bool, tracker=None):
    """Minimal state holding only fields the grade node body reads."""
    return {
        "rewritten_query": "q",
        "query": "q",
        "intent": "factoid",
        "message_id": 1,
        "request_id": uuid4(),
        "record_tenant_id": uuid4(),
        "record_bot_id": uuid4(),
        "reranked_chunks": [
            {"chunk_id": f"c-{i}", "content": f"text-{i}", "score": 0.5}
            for i in range(n_chunks)
        ],
        "_total_graph_iterations": 0,
        "pipeline_config": {
            "structured_output_enabled": True,
            "grade_use_structured_output": True,
            "grade_use_batch": grade_use_batch,
            "crag_grade_concurrency": 5,
            "max_total_graph_iterations": 10,
            "crag_min_relevant_count": 1,
            "crag_min_relevant_fraction": 0.0,
        },
        "step_tracker": tracker if tracker is not None else _RecordingStepTracker(),
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


def _run_grade_with_call_with_schema_stub(*, n_chunks: int, grade_use_batch: bool, fake_call):
    """Drive the live grade node with ``_call_with_schema`` patched.

    Patches ``ragbot.orchestration.query_graph._call_with_schema`` to the
    supplied coroutine, builds the graph, fetches the compiled grade
    node and invokes it with a synthesised state.

    Returns ``(tracker, result_dict)``.
    """
    import ragbot.orchestration.query_graph as qg

    saved = qg._call_with_schema
    resolver, llm = _resolver_llm()
    llm._litellm_module = MagicMock()
    tracker = _RecordingStepTracker()

    try:
        qg._call_with_schema = fake_call  # type: ignore[assignment]

        class _NoopVS:
            async def hybrid_search(self, **_kw):
                return []

            async def search(self, **_kw):
                return []

        class _NoopEmb:
            async def embed(self, texts, **_kw):
                return [[0.1] * 8 for _ in texts] if isinstance(texts, list) else [[0.1] * 8]

            async def embed_batch(self, texts, **_kw):
                return [[0.1] * 8 for _ in texts]

        class _NoopGuardrail:
            async def check_input(self, *_a, **_kw):
                return []

            async def check_output(self, *_a, **_kw):
                return []

        graph = qg.build_graph(
            invocation_logger=_FakeInvocationLogger(),
            guardrail=_NoopGuardrail(),
            model_resolver=resolver,
            llm=llm,
            vector_store=_NoopVS(),
            embedder=_NoopEmb(),
        )
        grade_runnable = graph.get_graph().nodes["grade"]
        state = _grade_state(n_chunks=n_chunks, grade_use_batch=grade_use_batch, tracker=tracker)
        result = asyncio.run(grade_runnable.data.ainvoke(state))
    finally:
        qg._call_with_schema = saved  # type: ignore[assignment]

    return tracker, result


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_grade_batch_path_recorded_in_metadata():
    """Batch path: ``_call_with_schema`` returns a parsed
    ``GradeBatchOutput`` for ``GradeBatchOutput`` schema → grade body
    takes batch branch. Metadata MUST report ``grade_path='batch'`` and
    ``structured_output_used=True``.
    """
    from ragbot.application.dto.llm_schemas import (
        ChunkGradeItem,
        GradeBatchOutput,
    )

    n_chunks = 3

    async def _fake_call(*, schema, **_kw):
        # Batch schema → return parsed GradeBatchOutput with all "yes".
        if schema is GradeBatchOutput:
            return GradeBatchOutput(
                grades=[
                    ChunkGradeItem(chunk_id=f"c-{i}", grade="yes")
                    for i in range(n_chunks)
                ],
            )
        # Per-chunk schema fallback (should NOT be hit on this test).
        return None

    tracker, result = _run_grade_with_call_with_schema_stub(
        n_chunks=n_chunks, grade_use_batch=True, fake_call=_fake_call,
    )

    grade_steps = tracker.by_name("grade")
    assert len(grade_steps) == 1, f"grade step must fire once, got {len(grade_steps)}"
    md = grade_steps[0].metadata

    assert md.get("grade_path") == "batch", md
    assert md.get("structured_output_used") is True, md
    assert md.get("n_chunks_input") == n_chunks, md
    # All graded "yes" → all relevant.
    assert md.get("n_relevant") == n_chunks, md
    assert md.get("n_irrelevant") == 0, md
    assert md.get("n_ambiguous") == 0, md


def test_grade_per_chunk_fallback_path_recorded():
    """Per-chunk fallback path: batch ``_call_with_schema`` returns
    ``None`` → grade body falls back to the parallel per-chunk loop.
    Metadata MUST report ``grade_path='per_chunk_fallback'`` and
    ``structured_output_used=True`` (per-chunk grades parse OK).
    """
    from ragbot.application.dto.llm_schemas import (
        GradeBatchOutput,
        GradeOutput,
    )

    n_chunks = 3

    async def _fake_call(*, schema, **_kw):
        # Batch returns None → forces fallback to per-chunk.
        if schema is GradeBatchOutput:
            return None
        if schema is GradeOutput:
            return GradeOutput(grade="no")
        return None

    tracker, result = _run_grade_with_call_with_schema_stub(
        n_chunks=n_chunks, grade_use_batch=True, fake_call=_fake_call,
    )

    grade_steps = tracker.by_name("grade")
    assert len(grade_steps) == 1, f"grade step must fire once, got {len(grade_steps)}"
    md = grade_steps[0].metadata

    assert md.get("grade_path") == "per_chunk_fallback", md
    assert md.get("structured_output_used") is True, md
    assert md.get("n_chunks_input") == n_chunks, md
    # Per-chunk graded "no" → all irrelevant (3-state CRAG).
    assert md.get("n_irrelevant") == n_chunks, md
    assert md.get("n_relevant") == 0, md
    assert md.get("n_ambiguous") == 0, md


def test_grade_metadata_includes_chunk_counts():
    """Metadata MUST always include the four chunk-count keys
    (``n_chunks_input``, ``n_relevant``, ``n_irrelevant``,
    ``n_ambiguous``) — observability invariants for analyzers regardless
    of which path executed.
    """
    from ragbot.application.dto.llm_schemas import (
        ChunkGradeItem,
        GradeBatchOutput,
    )

    n_chunks = 4

    async def _fake_call(*, schema, **_kw):
        if schema is GradeBatchOutput:
            # Mix: 2 yes, 1 no, 1 partial (ambiguous).
            return GradeBatchOutput(
                grades=[
                    ChunkGradeItem(chunk_id="c-0", grade="yes"),
                    ChunkGradeItem(chunk_id="c-1", grade="yes"),
                    ChunkGradeItem(chunk_id="c-2", grade="no"),
                    ChunkGradeItem(chunk_id="c-3", grade="partial"),
                ],
            )
        return None

    tracker, _ = _run_grade_with_call_with_schema_stub(
        n_chunks=n_chunks, grade_use_batch=True, fake_call=_fake_call,
    )

    md = tracker.by_name("grade")[0].metadata
    for key in ("n_chunks_input", "n_relevant", "n_irrelevant", "n_ambiguous"):
        assert key in md, f"grade metadata missing key {key}: {md}"
        assert isinstance(md[key], int), (key, md)
    assert md["n_chunks_input"] == n_chunks, md
    assert md["n_relevant"] == 2, md
    assert md["n_irrelevant"] == 1, md
    assert md["n_ambiguous"] == 1, md
    # Counts add up to input.
    assert (
        md["n_relevant"] + md["n_irrelevant"] + md["n_ambiguous"]
        == md["n_chunks_input"]
    ), md


def test_structured_output_flag_persists():
    """``structured_output_used`` MUST be False when no path produces
    parsed grades — i.e. both batch returns None AND per-chunk all
    return None. The grade body then falls through to the
    "treat-all-ambiguous" branch but the step row still records the
    flag for ops to spot the failure.
    """
    from ragbot.application.dto.llm_schemas import (
        GradeBatchOutput,
        GradeOutput,
    )

    n_chunks = 2

    async def _fake_call(*, schema, **_kw):
        # Both schemas return None → no parsed grades anywhere.
        return None

    tracker, _ = _run_grade_with_call_with_schema_stub(
        n_chunks=n_chunks, grade_use_batch=True, fake_call=_fake_call,
    )

    grade_steps = tracker.by_name("grade")
    assert len(grade_steps) == 1, f"grade step must fire once, got {len(grade_steps)}"
    md = grade_steps[0].metadata

    assert md.get("structured_output_used") is False, md
    # n_chunks_input still recorded so analyzers know fan-in size.
    assert md.get("n_chunks_input") == n_chunks, md
    # grade_path must still be reported (batch was attempted, then per-chunk).
    assert md.get("grade_path") in ("batch", "per_chunk_fallback"), md


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
