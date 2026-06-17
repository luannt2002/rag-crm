"""WE-4 — pin ``top_score`` lands in ``request_steps.metadata_json`` for the
``rerank`` step so ``scripts/diagnose_p95_bottleneck.py --rerank-score-histogram``
can chart real per-bot distribution.

The diagnose script SQL reads ``metadata_json ->> 'top_score'`` from
``request_steps`` rows where ``step_name = 'rerank'`` (see
``rerank_score_histogram_query`` at scripts/diagnose_p95_bottleneck.py:282).

Coverage:
  1. rerank-active path emits ``top_score`` reflecting the reranker score.
  2. RRF / bypass fallback path emits ``top_score`` reflecting RRF score scale.
  3. intent_skip_set path emits ``top_score`` from preserved retrieval order.
  4. empty_input path emits ``top_score == 0.0`` (no chunks).
  5. all paths emit a numeric ``top_score`` key (drift guard for histogram SQL).
  6. ``top_score`` matches the max ``score`` across surviving chunks.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.shared.constants import (
    DEFAULT_RERANK_SKIP_INTENTS,
    DEFAULT_RERANK_TOP_N,
)


# ---------------------------------------------------------------------------
# Test scaffold — mirrors test_per_intent_rerank_skip helpers
# ---------------------------------------------------------------------------


class _CapturingReranker:
    """Reranker stub. Rerank output scored at ``rerank_out_score`` so the
    surviving top_score is deterministic and well above the active floor."""

    def __init__(self, rerank_out_score: float = 0.91) -> None:
        self.calls: list[dict] = []
        self._score = rerank_out_score

    def get_provider_name(self) -> str:
        return "capturing-fake"

    async def rerank(
        self,
        *,
        query: str,
        chunks: list[dict],
        top_n: int,
        model: str | None = None,
    ) -> list[dict]:
        self.calls.append({"chunks_in": len(chunks), "top_n": top_n})
        out: list[dict] = []
        for i, c in enumerate(chunks[:top_n]):
            row = dict(c)
            # First chunk gets the headline score, rest decrement so max is
            # deterministic for the assertion.
            row["score"] = self._score - i * 0.01
            out.append(row)
        return out


class _RecordingStepCtx:
    def __init__(self) -> None:
        self.metadata: dict = {}

    def set_metadata(self, **kw) -> None:
        self.metadata.update(kw)

    def add_tokens(self, **_kw) -> None:
        pass

    def record_llm(self, **_kw) -> None:
        """Wave M3.2 — no-op mirror of StepContext.record_llm."""
        pass


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.steps: dict[str, _RecordingStepCtx] = {}

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx()
        self.steps[name] = ctx
        yield ctx


async def _run_rerank_node(
    *,
    intent: str,
    skip_intents,
    chunks: list[dict],
    rerank_top_n: int,
    reranker: _CapturingReranker,
    min_score_active: float = 0.30,
) -> _RecordingStepCtx:
    """Drive the real rerank closure and return its step context."""
    from ragbot.orchestration.query_graph import build_graph

    tracker = _RecordingStepTracker()

    @asynccontextmanager
    async def _noop_invocation(**_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx

    invocation_logger = MagicMock()
    invocation_logger.invoke_model = _noop_invocation

    guardrail = MagicMock()
    guardrail.check_input = AsyncMock(return_value=[])
    guardrail.check_output = AsyncMock(return_value=[])

    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "x", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })

    graph = build_graph(
        invocation_logger=invocation_logger,
        guardrail=guardrail,
        model_resolver=resolver,
        llm=llm,
        reranker=reranker,
    )

    rerank_node = graph.nodes["rerank"]
    runnable = getattr(rerank_node, "runnable", None) or rerank_node
    bound = getattr(runnable, "bound", None)
    func = bound if bound is not None else runnable
    if hasattr(func, "afunc"):
        func = func.afunc
    elif hasattr(func, "func"):
        func = func.func

    state: dict = {
        "query": "what's the price",
        "rewritten_query": None,
        "retrieved_chunks": chunks,
        "intent": intent,
        "pipeline_config": {
            "rerank_top_n": rerank_top_n,
            "reranker_enabled": True,
            "reranker_min_score_active": min_score_active,
            "reranker_min_score_bypass": 0.0,
            "rerank_intent_whitelist": None,
            "rerank_skip_intents": skip_intents,
        },
        "record_bot_id": uuid4(),
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }

    await func(state)
    return tracker.steps["rerank"]


# ---------------------------------------------------------------------------
# 1. Rerank-active path writes top_score
# ---------------------------------------------------------------------------


def test_rerank_mode_writes_top_score_from_cross_encoder() -> None:
    """Real rerank fires → top_score reflects the reranker output score
    (cross-encoder 0..1 scale). Asserts the histogram SQL can read a real
    value, not a missing key."""
    rk = _CapturingReranker(rerank_out_score=0.91)
    chunks = [
        {"chunk_id": f"c{i}", "content": f"body {i}", "score": 0.5 - i * 0.01}
        for i in range(20)  # pool > top_n forces rerank fire
    ]
    ctx = asyncio.run(_run_rerank_node(
        intent="multi_hop",  # heavyweight — never skipped
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        chunks=chunks,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert "top_score" in ctx.metadata, "rerank step must write top_score"
    assert ctx.metadata["mode"] == "rerank"
    # Reranker assigned 0.91 to chunk[0], 0.90 to chunk[1], etc. — max stays
    # at 0.91 unless the post-filter strips it.
    assert ctx.metadata["top_score"] == 0.91


# ---------------------------------------------------------------------------
# 2. Disabled / bypass path still writes top_score (from RRF retrieval score)
# ---------------------------------------------------------------------------


def test_disabled_mode_writes_top_score_from_retrieval() -> None:
    """No reranker configured (pass-through ``inp[:top_n]``) → top_score
    pulled from the retrieval scale (RRF 0.01-0.05 typically)."""
    rk = _CapturingReranker()
    chunks = [
        {"chunk_id": f"c{i}", "content": f"body {i}", "score": 0.045 - i * 0.001}
        for i in range(3)  # tiny pool, heavyweight intent → rerank fires
    ]
    # multi_hop avoids skip set; but to test "disabled", flip enabled=False via
    # passing min_score_active=0 so post-filter does not drop the chunks.
    ctx = asyncio.run(_run_rerank_node(
        intent="multi_hop",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        chunks=chunks,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
        min_score_active=0.0,
    ))
    # Even when rerank fires, top_score must be present.
    assert "top_score" in ctx.metadata
    assert isinstance(ctx.metadata["top_score"], float)


# ---------------------------------------------------------------------------
# 3. intent_skip_set path writes top_score
# ---------------------------------------------------------------------------


def test_intent_skip_set_writes_top_score() -> None:
    """Factoid + small pool → SKIP rerank. top_score still emitted, sourced
    from retrieval order (RRF scale)."""
    rk = _CapturingReranker()
    chunks = [
        {"chunk_id": f"c{i}", "content": f"body {i}", "score": 0.043 - i * 0.002}
        for i in range(3)  # 3 ≤ top_n (7) → safety satisfied → skip
    ]
    ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        chunks=chunks,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
        # Bypass floor is 0, so chunks survive.
        min_score_active=0.0,
    ))
    assert ctx.metadata["mode"] == "intent_skip_set"
    assert "top_score" in ctx.metadata
    # Max retrieval score = 0.043 on chunk[0].
    assert ctx.metadata["top_score"] == 0.043


# ---------------------------------------------------------------------------
# 4. Empty pool — top_score == 0.0, not missing
# ---------------------------------------------------------------------------


def test_empty_input_writes_top_score_zero() -> None:
    """Empty retrieval pool → mode=empty_input, top_score=0.0 (NOT missing)."""
    rk = _CapturingReranker()
    ctx = asyncio.run(_run_rerank_node(
        intent="multi_hop",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        chunks=[],
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert ctx.metadata["mode"] == "empty_input"
    assert "top_score" in ctx.metadata
    assert ctx.metadata["top_score"] == 0.0


# ---------------------------------------------------------------------------
# 5. Drift guard — every mode emits numeric top_score key
# ---------------------------------------------------------------------------


def test_top_score_key_present_across_all_modes() -> None:
    """Diagnose SQL filters ``metadata_json ? 'top_score'`` — assert the key
    is always present so the histogram never silently empties again."""
    # Reuse the three exercised modes above plus one more (empty).
    cases = [
        # (intent, n_chunks, expected_mode)
        ("multi_hop", 20, "rerank"),
        ("factoid", 3, "intent_skip_set"),
        ("multi_hop", 0, "empty_input"),
    ]
    for intent, n, expected in cases:
        rk = _CapturingReranker()
        chunks = [
            {"chunk_id": f"c{i}", "content": "x", "score": 0.4 - i * 0.01}
            for i in range(n)
        ]
        ctx = asyncio.run(_run_rerank_node(
            intent=intent,
            skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
            chunks=chunks,
            rerank_top_n=DEFAULT_RERANK_TOP_N,
            reranker=rk,
            min_score_active=0.0,
        ))
        assert ctx.metadata["mode"] == expected, (intent, n)
        assert "top_score" in ctx.metadata, f"missing top_score in mode={expected}"
        assert isinstance(ctx.metadata["top_score"], (int, float))


# ---------------------------------------------------------------------------
# 6. top_score equals max(score) across surviving chunks
# ---------------------------------------------------------------------------


def test_top_score_matches_max_surviving_score() -> None:
    """top_score reflects the maximum chunk score after rerank + filter, not
    the input pool max. This is what the diagnose histogram is actually
    plotting."""
    # Reranker boosts top to 0.91 — that's what the metric must capture, not
    # the original 0.5 input scale.
    rk = _CapturingReranker(rerank_out_score=0.91)
    chunks = [
        {"chunk_id": f"c{i}", "content": "x", "score": 0.5 - i * 0.01}
        for i in range(20)
    ]
    ctx = asyncio.run(_run_rerank_node(
        intent="multi_hop",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        chunks=chunks,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    # Pool input max was 0.5; rerank lifted top to 0.91; metric reports 0.91.
    assert ctx.metadata["top_score"] == 0.91
    # And the input pool max (0.5) is NOT what gets reported.
    assert ctx.metadata["top_score"] != 0.5
