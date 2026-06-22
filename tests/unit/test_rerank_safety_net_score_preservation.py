"""M18 — rerank retrieval safety-net must not collapse a real score to 0.0.

Bug (``src/ragbot/orchestration/nodes/rerank.py``): the retrieval safety-net
re-injects top-of-retrieval RRF candidates that the cross-encoder under-ranked
so they survive the downstream CRAG absolute-floor + context-cap ordering. It
stamps each injected chunk with ``_stamp = min(surviving_rerank_scores)`` so the
chunk is lifted up to the surviving rerank floor.

When the min-score / cliff stage has already emptied the surviving pool
(``out == []``), ``min(_kept_scores)`` has no value to borrow and the code fell
back to ``_stamp = 0.0`` — overwriting the injected chunk's *real* positive RRF
retrieval score with ``0.0``. A genuinely-retrieved chunk then reports a
``top_score`` of ``0.0``, identical to a genuinely-empty result, and the
downstream absolute-floor ordering drops it — defeating the very safety-net that
re-injected it.

Expected behaviour: with nothing to lift the chunk *to*, the safety-net must
preserve the injected chunk's own real retrieval score, so a non-empty rerank
result reports its true max score (not 0.0).

These tests drive the real ``rerank`` node closure through ``build_graph`` — the
same harness pattern as ``test_per_intent_rerank_skip.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.shared.constants import DEFAULT_RERANK_TOP_N


# ---------------------------------------------------------------------------
# Reranker stub — returns LOW (below active-floor) scores so the threshold
# gate empties the surviving pool, triggering the safety-net with no score to
# borrow.
# ---------------------------------------------------------------------------


class _LowScoreReranker:
    """Reranker port stub that scores every chunk below the active floor.

    The post-rerank threshold gate (active floor default 0.30) then drops the
    whole reranked pool, leaving ``out == []`` so the safety-net runs with an
    empty surviving set.
    """

    def __init__(self, *, score: float) -> None:
        self._score = score
        self.calls: list[dict] = []

    def get_provider_name(self) -> str:
        return "low-score-fake"

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
        for c in chunks[:top_n]:
            row = dict(c)
            row["score"] = self._score
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
        pass


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.steps: dict[str, _RecordingStepCtx] = {}

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx()
        self.steps[name] = ctx
        yield ctx


async def _run_rerank_threshold_empties_pool(
    *,
    rrf_score: float,
    rerank_score: float,
    active_floor: float,
    safety_n: int,
) -> tuple[dict, _RecordingStepCtx]:
    """Drive the real rerank closure under the ``threshold`` strategy.

    The reranker scores every chunk at ``rerank_score`` (below ``active_floor``)
    so the post-rerank threshold gate empties the surviving pool, forcing the
    safety-net to run with no surviving score to borrow. The pre-rerank chunks
    carry a real positive ``rrf_score``.
    """
    from ragbot.orchestration.query_graph import build_graph

    tracker = _RecordingStepTracker()
    reranker = _LowScoreReranker(score=rerank_score)

    invocation_logger = MagicMock()

    @asynccontextmanager
    async def _noop_invocation(**_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx

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

    # 4 chunks, each a real positive RRF retrieval score. multi_hop is NOT in
    # the skip set so a real rerank fires; threshold strategy + low rerank
    # score empties the pool after gating.
    chunks = [
        {"chunk_id": f"c{i}", "content": f"body {i}", "score": rrf_score}
        for i in range(4)
    ]

    state: dict = {
        "query": "any query",
        "rewritten_query": None,
        "retrieved_chunks": chunks,
        "intent": "multi_hop",
        "pipeline_config": {
            "rerank_top_n": DEFAULT_RERANK_TOP_N,
            "reranker_enabled": True,
            "rerank_filter_strategy": "threshold",
            "reranker_min_score_active": active_floor,
            "reranker_min_score_bypass": 0.0,
            "rerank_intent_whitelist": None,
            "rerank_skip_intents": (),
            "rerank_retrieval_safety_n": safety_n,
        },
        "record_bot_id": uuid4(),
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }

    out = await func(state)
    return out, tracker.steps["rerank"]


# ---------------------------------------------------------------------------
# RED test — the safety-injected chunk keeps its real RRF score, not 0.0.
# ---------------------------------------------------------------------------


def test_safety_net_preserves_real_score_when_pool_emptied() -> None:
    """Threshold gate empties the reranked pool; the safety-net re-injects the
    top-of-retrieval chunk. Its real RRF score (0.04) must survive — NOT be
    overwritten with 0.0."""
    rrf_score = 0.04
    out, _ctx = asyncio.run(_run_rerank_threshold_empties_pool(
        rrf_score=rrf_score,
        rerank_score=0.10,   # below active floor → threshold gate empties pool
        active_floor=0.30,
        safety_n=2,
    ))
    kept = out["reranked_chunks"]
    # The safety-net must have re-injected at least one top-of-retrieval chunk.
    assert kept, "safety-net must re-inject top-of-retrieval chunk when pool emptied"
    injected = [c for c in kept if c.get("_safety_injected")]
    assert injected, "expected safety-injected chunk(s) in the output"
    # The real RRF score must be preserved, not collapsed to 0.0.
    max_score = max(float(c.get("score", 0) or 0) for c in kept)
    assert max_score == rrf_score, (
        f"safety-net collapsed real score to {max_score} (expected {rrf_score})"
    )
    for c in injected:
        assert float(c.get("score", 0) or 0) == rrf_score, (
            "injected chunk's real RRF score was overwritten with stamp 0.0"
        )


def test_safety_net_lifts_to_surviving_floor_when_pool_nonempty() -> None:
    """Regression guard: when the reranked pool is NON-empty, the existing
    lift-to-floor behaviour stays — injected chunks are stamped with the lowest
    surviving rerank score so they clear the CRAG absolute floor."""
    out, _ctx = asyncio.run(_run_rerank_threshold_empties_pool(
        rrf_score=0.02,
        rerank_score=0.50,   # above active floor → pool survives the gate
        active_floor=0.30,
        safety_n=2,
    ))
    kept = out["reranked_chunks"]
    survivors = [c for c in kept if not c.get("_safety_injected")]
    assert survivors, "reranked survivors must remain when scores clear the floor"
    surviving_min = min(float(c.get("score", 0) or 0) for c in survivors)
    injected = [c for c in kept if c.get("_safety_injected")]
    for c in injected:
        # Lifted up to the surviving floor (0.50), NOT left at raw RRF 0.02.
        assert float(c.get("score", 0) or 0) == surviving_min, (
            "non-empty pool must still lift injected chunk to surviving floor"
        )
