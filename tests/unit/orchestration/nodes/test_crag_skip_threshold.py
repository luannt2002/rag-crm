"""[T1-Smartness] S1 Pipeline-Opt — CRAG grade-node early-exit when top score is high.

The grade node now short-circuits BEFORE the LLM call when the pass-1 top
retrieval score clears ``crag_skip_retry_above_score``. This saves both
the grade-LLM call and the downstream ``rewrite_retry`` loop (~10s).

Trace fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4 motivates the fix: top_score
=0.91 wasted 10683ms on a CRAG retry that produced the same answer.

These tests drive the **live** grade node body (not a copy / mock) by
fetching the compiled node from ``build_graph`` and ``ainvoke``-ing it
with a synthesised state. ``_call_with_schema`` is patched to a
deliberate sentinel that raises when invoked — proving the LLM call is
skipped on the high-score path.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Recording fakes                                                             #
# --------------------------------------------------------------------------- #


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
    llm._litellm_module = MagicMock()
    return resolver, llm


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


# --------------------------------------------------------------------------- #
# State builder                                                               #
# --------------------------------------------------------------------------- #


def _grade_state(
    *,
    chunks: list[dict],
    skip_threshold: float | None,
    tracker: _RecordingStepTracker | None = None,
    intent: str = "factoid",
) -> dict:
    """Build a minimal state hitting the grade-node early-exit path."""
    pcfg: dict = {
        "structured_output_enabled": True,
        "grade_use_structured_output": True,
        "grade_use_batch": True,
        "crag_grade_concurrency": 5,
        "max_total_graph_iterations": 10,
        "crag_min_relevant_count": 1,
        "crag_min_relevant_fraction": 0.0,
        "max_grade_retries": 1,
    }
    if skip_threshold is not None:
        pcfg["crag_skip_retry_above_score"] = skip_threshold
    return {
        "rewritten_query": "q",
        "query": "q",
        "intent": intent,
        "message_id": 1,
        "request_id": uuid4(),
        "record_tenant_id": uuid4(),
        "record_bot_id": uuid4(),
        "reranked_chunks": list(chunks),
        "_total_graph_iterations": 0,
        "pipeline_config": pcfg,
        "step_tracker": tracker if tracker is not None else _RecordingStepTracker(),
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


def _run_grade_node(state: dict, *, llm_call_should_raise: bool = True) -> tuple[_RecordingStepTracker, dict]:
    """Drive the live grade node body.

    When ``llm_call_should_raise=True`` we monkeypatch ``_call_with_schema``
    to a coroutine that raises — so any test claiming "no LLM call on
    skip path" will fail loudly if the early-exit was missed.
    """
    import ragbot.orchestration.query_graph as qg

    saved = qg._call_with_schema

    async def _exploding_call(**_kw):
        raise AssertionError(
            "grade-node LLM call should NOT fire when crag_skip_retry "
            "early-exit triggered"
        )

    async def _passive_call(**_kw):
        # Used by no-skip tests where the grade LLM is expected to run.
        # Returns None so the grade body falls through to the
        # "all-ambiguous" path (avoids touching schema-specific fakes).
        # ``_call_with_schema`` returns the parsed schema or None.
        return None

    resolver, llm = _resolver_llm()
    tracker = state["step_tracker"]

    try:
        qg._call_with_schema = (  # type: ignore[assignment]
            _exploding_call if llm_call_should_raise else _passive_call
        )

        graph = qg.build_graph(
            invocation_logger=_FakeInvocationLogger(),
            guardrail=_NoopGuardrail(),
            model_resolver=resolver,
            llm=llm,
            vector_store=_NoopVS(),
            embedder=_NoopEmb(),
        )
        grade_runnable = graph.get_graph().nodes["grade"]
        result = asyncio.run(grade_runnable.data.ainvoke(state))
    finally:
        qg._call_with_schema = saved  # type: ignore[assignment]

    return tracker, result


# --------------------------------------------------------------------------- #
# Tests — 6 spec'd by S1 handoff                                              #
# --------------------------------------------------------------------------- #


def test_skip_when_score_high():
    """chunks[0].score=0.91, threshold=0.7 → skip grade-LLM + retry."""
    state = _grade_state(
        chunks=[
            {"chunk_id": "c1", "score": 0.91, "content": "high"},
            {"chunk_id": "c2", "score": 0.42, "content": "low"},
        ],
        skip_threshold=0.7,
    )
    tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    assert result.get("crag_skip_retry") is True, (
        "expected crag_skip_retry flag on high-score path"
    )
    assert result.get("retrieval_adequate") is True
    assert len(result.get("graded_chunks") or []) == 2
    # All chunks pass through as "relevant" so generate has full context.
    for chunk in result["graded_chunks"]:
        assert chunk["relevance"] == "relevant"
    # Step metadata records the skip path for observability.
    grade_steps = tracker.by_name("grade")
    assert grade_steps, "grade step missing from tracker"
    md = grade_steps[-1].metadata
    assert md.get("grade_path") == "skip_high_score"
    assert md.get("skip_top_score") == 0.91
    assert md.get("skip_threshold") == 0.7


def test_no_skip_when_score_low():
    """max=0.5 < threshold=0.7 → fall through to grade-LLM call."""
    state = _grade_state(
        chunks=[
            {"chunk_id": "c1", "score": 0.5, "content": "low"},
            {"chunk_id": "c2", "score": 0.42, "content": "lower"},
        ],
        skip_threshold=0.7,
    )
    # LLM call MUST fire on this path — passive stub returns (None, None).
    tracker, result = _run_grade_node(state, llm_call_should_raise=False)

    assert not result.get("crag_skip_retry"), (
        "early-exit must NOT fire when max_score < threshold"
    )
    # Grade body proceeded to the structured-output path with parsed=None,
    # so the result reflects normal grading (no skip flag set).
    grade_steps = tracker.by_name("grade")
    assert grade_steps
    md = grade_steps[-1].metadata
    assert md.get("grade_path") != "skip_high_score"


def test_no_skip_when_no_chunks():
    """Empty reranked_chunks → return without ever consulting threshold."""
    state = _grade_state(chunks=[], skip_threshold=0.7)
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    # The empty-chunks guard runs BEFORE the skip block, so the skip flag
    # must not appear on the empty path. ``retrieval_adequate`` should be
    # False (no chunks to grade), preserving legacy "refuse" semantics.
    assert not result.get("crag_skip_retry")
    assert result.get("retrieval_adequate") is False
    assert result.get("graded_chunks") == []


def test_per_bot_override():
    """Per-bot threshold (0.85 via pipeline_config) overrides system default 0.7."""
    # Bot owner tightened threshold to 0.85. Pass-1 top score 0.80 sits
    # ABOVE the system default (0.7) but BELOW the bot's tighter floor
    # (0.85) → must NOT skip.
    state = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.80, "content": "mid-high"}],
        skip_threshold=0.85,
    )
    tracker, result = _run_grade_node(state, llm_call_should_raise=False)
    assert not result.get("crag_skip_retry"), (
        "per-bot 0.85 override must keep top_score=0.80 in the retry path"
    )

    # Bump score above the tighter floor → skip fires.
    state2 = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.92, "content": "very high"}],
        skip_threshold=0.85,
    )
    _t2, result2 = _run_grade_node(state2, llm_call_should_raise=True)
    assert result2.get("crag_skip_retry") is True


def test_disable_via_threshold_1_1():
    """threshold > 1.0 → never skip (sentinel disable)."""
    # Score 0.99 — would normally skip at default 0.7 — but the bot owner
    # set threshold to 1.1 (sentinel), so the gate is disabled.
    state = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.99, "content": "near-perfect"}],
        skip_threshold=1.1,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=False)

    assert not result.get("crag_skip_retry"), (
        "threshold=1.1 must disable the gate even on near-perfect scores"
    )


def test_threshold_at_exact_boundary():
    """max=0.7 == threshold=0.7 → skip (>= boundary)."""
    state = _grade_state(
        chunks=[
            {"chunk_id": "c1", "score": 0.7, "content": "boundary"},
            {"chunk_id": "c2", "score": 0.6, "content": "below"},
        ],
        skip_threshold=0.7,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    assert result.get("crag_skip_retry") is True, (
        "boundary equality must trigger skip — gate uses >= semantics"
    )
    assert result.get("retrieval_adequate") is True


# --------------------------------------------------------------------------- #
# Supplementary regression tests                                              #
# --------------------------------------------------------------------------- #


def test_skip_reason_string_format():
    """``crag_skip_reason`` is human-readable for trace investigation."""
    state = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.91, "content": "x"}],
        skip_threshold=0.7,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    reason = result.get("crag_skip_reason", "")
    assert "top_score=" in reason
    assert "0.910" in reason  # 3-decimal format
    assert ">=" in reason
    assert "0.7" in reason


def test_zero_threshold_preserves_disabled_behaviour():
    """When pipeline_config explicitly sets threshold=0.0 → skip never fires."""
    state = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.99, "content": "x"}],
        skip_threshold=0.0,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=False)
    assert not result.get("crag_skip_retry")


def test_intent_self_correction_in_skip_path():
    """Skip path also applies OOS intent self-correction (parity w/ normal path)."""
    state = _grade_state(
        chunks=[{"chunk_id": "c1", "score": 0.91, "content": "x"}],
        skip_threshold=0.7,
        intent="out_of_scope",
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    assert result.get("crag_skip_retry") is True
    # OOS reclassified to fallback intent so generate doesn't refuse.
    assert result.get("intent_corrected") is True
    assert result.get("intent") != "out_of_scope"


def test_default_constant_is_seven_tenths():
    """Single source of truth — DEFAULT constant matches S1 spec."""
    from ragbot.shared.constants import DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE

    assert DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE == 0.7


def test_plan_limit_schema_default_matches_constant():
    """``PLAN_LIMIT_SCHEMA`` default mirrors the constant — no drift."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA
    from ragbot.shared.constants import DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE

    schema = PLAN_LIMIT_SCHEMA["crag_skip_retry_above_score"]
    assert schema["default"] == DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE
    assert schema["max"] > 1.0, "max must permit disable-by-overshoot"


def test_no_score_field_safe():
    """Chunks with missing/None score are skipped, not crashed on."""
    state = _grade_state(
        chunks=[
            {"chunk_id": "c1", "content": "no score"},
            {"chunk_id": "c2", "score": None, "content": "explicit None"},
            {"chunk_id": "c3", "score": 0.91, "content": "valid high"},
        ],
        skip_threshold=0.7,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    # c3 score 0.91 still triggers skip; bad-data rows are ignored.
    assert result.get("crag_skip_retry") is True


def test_non_numeric_score_safe():
    """Garbage score values are skipped (TypeError / ValueError handled)."""
    state = _grade_state(
        chunks=[
            {"chunk_id": "c1", "score": "not-a-number", "content": "junk"},
            {"chunk_id": "c2", "score": 0.91, "content": "valid"},
        ],
        skip_threshold=0.7,
    )
    _tracker, result = _run_grade_node(state, llm_call_should_raise=True)

    assert result.get("crag_skip_retry") is True
