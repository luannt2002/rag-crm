"""Unit tests for RAGAS-style evaluator — mocked LLM/embedding stubs."""

from __future__ import annotations

import asyncio
import json

import pytest

from ragbot.evaluation.ragas_metrics import (
    LLMRagasEvaluator,
    MetricEvaluator,
    TurnInput,
)


def _completion_resp(content: str | dict) -> dict:
    """Build a litellm-shaped completion response dict with given content."""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}]}


def _embedding_resp(vec: list[float]) -> dict:
    return {"data": [{"embedding": vec}]}


def _make_completion_fn(script: list):
    """Return an awaitable that pops a queued response per call."""
    queue = list(script)

    async def _fn(**_kwargs):
        if not queue:
            raise AssertionError("completion_fn called more times than scripted")
        return queue.pop(0)

    return _fn, queue


def _make_embedding_fn(script: dict[str, list[float]]):
    """Return an awaitable mapping input text → vector."""

    async def _fn(*, model: str, input: list[str]):  # noqa: ARG001, A002
        text = input[0]
        vec = script.get(text)
        if vec is None:
            # default: deterministic embedding from hash so unknown text maps
            # to a stable orthogonal-ish vector
            h = abs(hash(text))
            vec = [((h >> i) & 1) * 1.0 for i in range(8)]
        return _embedding_resp(vec)

    return _fn


def test_evaluator_protocol_satisfied() -> None:
    """LLMRagasEvaluator must implement MetricEvaluator (Strategy + DI)."""
    ev = LLMRagasEvaluator(completion_fn=lambda **_: None, embedding_fn=lambda **_: None)
    assert isinstance(ev, MetricEvaluator)


def test_faithfulness_empty_answer_returns_zero() -> None:
    """Empty answer → score = 0.0 (unscoreable, no claims)."""
    completion_fn, _ = _make_completion_fn([])
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(question="q", answer="", retrieved_chunks=("chunk",))
    score, n_claims = asyncio.run(ev.score_faithfulness(turn))
    assert score == 0.0
    assert n_claims == 0


def test_faithfulness_no_claims_returns_one() -> None:
    """If LLM extracts zero claims (greeting only) → faith = 1.0 (vacuous)."""
    completion_fn, _ = _make_completion_fn([_completion_resp({"claims": []})])
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(question="q", answer="Xin chào!", retrieved_chunks=("ctx",))
    score, n_claims = asyncio.run(ev.score_faithfulness(turn))
    assert score == 1.0
    assert n_claims == 0


def test_faithfulness_perfect_grounding_returns_one() -> None:
    """All extracted claims judged grounded → faith = 1.0."""
    script = [
        _completion_resp({"claims": ["claim A", "claim B"]}),
        _completion_resp({"grounded": True}),
        _completion_resp({"grounded": True}),
    ]
    completion_fn, _ = _make_completion_fn(script)
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(
        question="q",
        answer="A and B are both supported by docs.",
        retrieved_chunks=("doc says A", "doc says B"),
    )
    score, n_claims = asyncio.run(ev.score_faithfulness(turn))
    assert score == pytest.approx(1.0)
    assert n_claims == 2


def test_faithfulness_pure_hallucination_returns_low() -> None:
    """All claims judged ungrounded → faith = 0.0."""
    script = [
        _completion_resp({"claims": ["price is 99k", "free shipping"]}),
        _completion_resp({"grounded": False}),
        _completion_resp({"grounded": False}),
    ]
    completion_fn, _ = _make_completion_fn(script)
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(
        question="how much",
        answer="price is 99k and free shipping",
        retrieved_chunks=("policy chunk unrelated",),
    )
    score, _n = asyncio.run(ev.score_faithfulness(turn))
    assert score == pytest.approx(0.0)


def test_answer_relevance_high_when_reverse_q_matches() -> None:
    """Reverse Q embedding ≈ original Q embedding → relevance ≈ 1.0."""
    completion_script = [_completion_resp({"questions": ["giá triệt nách bao nhiêu"]})]
    completion_fn, _ = _make_completion_fn(completion_script)
    # identical vectors → cosine = 1.0
    embedding_fn = _make_embedding_fn(
        {
            "giá triệt lông nách": [1.0, 0.0, 0.0],
            "giá triệt nách bao nhiêu": [1.0, 0.0, 0.0],
        }
    )
    ev = LLMRagasEvaluator(
        completion_fn=completion_fn, embedding_fn=embedding_fn, reverse_questions_n=1
    )
    turn = TurnInput(
        question="giá triệt lông nách",
        answer="199k buổi lẻ",
        retrieved_chunks=("price chunk",),
    )
    score, n_rev = asyncio.run(ev.score_answer_relevance(turn))
    assert score == pytest.approx(1.0, abs=1e-6)
    assert n_rev == 1


def test_answer_relevance_low_when_off_topic() -> None:
    """Reverse Q orthogonal to original Q → relevance ≈ 0.0."""
    completion_script = [_completion_resp({"questions": ["bạn là ai"]})]
    completion_fn, _ = _make_completion_fn(completion_script)
    embedding_fn = _make_embedding_fn(
        {
            "giá triệt lông nách": [1.0, 0.0, 0.0],
            "bạn là ai": [0.0, 1.0, 0.0],
        }
    )
    ev = LLMRagasEvaluator(
        completion_fn=completion_fn, embedding_fn=embedding_fn, reverse_questions_n=1
    )
    turn = TurnInput(
        question="giá triệt lông nách",
        answer="Em là Claude AI",
        retrieved_chunks=("price chunk",),
    )
    score, _n = asyncio.run(ev.score_answer_relevance(turn))
    assert score == pytest.approx(0.0, abs=1e-6)


def test_context_precision_all_relevant_returns_one() -> None:
    """Every chunk judged relevant → precision = 1.0."""
    script = [
        _completion_resp({"grounded": True}),
        _completion_resp({"grounded": True}),
        _completion_resp({"grounded": True}),
    ]
    completion_fn, _ = _make_completion_fn(script)
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(
        question="giá triệt nách",
        answer="...",
        retrieved_chunks=("triệt nách 199k", "triệt nách combo", "triệt nách buổi lẻ"),
    )
    score, n_chunks = asyncio.run(ev.score_context_precision(turn))
    assert score == pytest.approx(1.0)
    assert n_chunks == 3


def test_context_precision_mixed_ap_at_k_rank_aware() -> None:
    """AP@K rank-aware: relevant at rank 1,3 of 4 chunks.

    P@1 = 1/1 = 1.0 (rel at rank 1)
    P@3 = 2/3 = 0.6667 (rel at rank 3, n_relevant_so_far=2)
    AP@K = (1.0 + 0.6667) / 2 = 0.8333
    """
    script = [
        _completion_resp({"grounded": True}),
        _completion_resp({"grounded": False}),
        _completion_resp({"grounded": True}),
        _completion_resp({"grounded": False}),
    ]
    completion_fn, _ = _make_completion_fn(script)
    embedding_fn = _make_embedding_fn({})
    ev = LLMRagasEvaluator(completion_fn=completion_fn, embedding_fn=embedding_fn)
    turn = TurnInput(
        question="q",
        answer="a",
        retrieved_chunks=("c1", "c2", "c3", "c4"),
    )
    score, _n = asyncio.run(ev.score_context_precision(turn))
    assert score == pytest.approx((1.0 + 2/3) / 2)


def test_context_precision_top1_relevant_returns_1_0() -> None:
    """1 relevant chunk at rank 1 of 16 → AP@K = 1.0 (perfect top-1).

    Pin test for Phan Xi Pang case: 1 chunk at top-1 contains answer,
    15 irrelevant chunks after. Old formula = 1/16 = 0.0625 (wrong).
    AP@K = (1/1) / 1 = 1.0 (correct).
    """
    script = [_completion_resp({"grounded": True})] + [
        _completion_resp({"grounded": False}) for _ in range(15)
    ]
    completion_fn, _ = _make_completion_fn(script)
    ev = LLMRagasEvaluator(
        completion_fn=completion_fn,
        embedding_fn=_make_embedding_fn({}),
    )
    turn = TurnInput(
        question="đỉnh núi cao nhất việt nam",
        answer="phan xi pang",
        retrieved_chunks=tuple(f"chunk_{i}" for i in range(16)),
    )
    score, n = asyncio.run(ev.score_context_precision(turn))
    assert score == pytest.approx(1.0)
    assert n == 16


def test_context_precision_zero_relevant_returns_0_0() -> None:
    """No chunks relevant → AP@K = 0.0."""
    script = [_completion_resp({"grounded": False}) for _ in range(5)]
    completion_fn, _ = _make_completion_fn(script)
    ev = LLMRagasEvaluator(
        completion_fn=completion_fn,
        embedding_fn=_make_embedding_fn({}),
    )
    turn = TurnInput(
        question="q",
        answer="a",
        retrieved_chunks=tuple(f"c{i}" for i in range(5)),
    )
    score, _n = asyncio.run(ev.score_context_precision(turn))
    assert score == pytest.approx(0.0)


def test_context_precision_top_rank_beats_bottom_rank() -> None:
    """Same n_relevant but at different ranks → top-rank scores higher.

    Case A: relevant at rank 1,2 of 10 → AP@K = (1/1 + 2/2) / 2 = 1.0
    Case B: relevant at rank 9,10 of 10 → AP@K = (1/9 + 2/10) / 2 ≈ 0.156
    Old formula gave both = 2/10 = 0.2 (rank-blind).
    """
    # Case A: top
    script_a = (
        [_completion_resp({"grounded": True}), _completion_resp({"grounded": True})]
        + [_completion_resp({"grounded": False}) for _ in range(8)]
    )
    completion_a, _ = _make_completion_fn(script_a)
    ev_a = LLMRagasEvaluator(
        completion_fn=completion_a,
        embedding_fn=_make_embedding_fn({}),
    )
    turn = TurnInput(
        question="q",
        answer="a",
        retrieved_chunks=tuple(f"c{i}" for i in range(10)),
    )
    score_a, _ = asyncio.run(ev_a.score_context_precision(turn))

    # Case B: bottom
    script_b = (
        [_completion_resp({"grounded": False}) for _ in range(8)]
        + [_completion_resp({"grounded": True}), _completion_resp({"grounded": True})]
    )
    completion_b, _ = _make_completion_fn(script_b)
    ev_b = LLMRagasEvaluator(
        completion_fn=completion_b,
        embedding_fn=_make_embedding_fn({}),
    )
    score_b, _ = asyncio.run(ev_b.score_context_precision(turn))

    assert score_a == pytest.approx(1.0)
    assert score_b == pytest.approx((1/9 + 2/10) / 2)
    assert score_a > score_b  # rank matters


def test_score_turn_combines_three_metrics() -> None:
    """End-to-end score_turn returns 3 metrics + counters."""
    # claims=1 grounded, reverse_qs=1 matching, chunks=1 relevant → all 1.0
    completion_script = [
        _completion_resp({"claims": ["only claim"]}),
        _completion_resp({"questions": ["q matches"]}),
        _completion_resp({"grounded": True}),  # context_precision chunk
        _completion_resp({"grounded": True}),  # faithfulness verify
    ]
    completion_fn, leftover = _make_completion_fn(completion_script)
    embedding_fn = _make_embedding_fn(
        {"q matches": [1.0, 0.0], "original q": [1.0, 0.0]}
    )
    ev = LLMRagasEvaluator(
        completion_fn=completion_fn, embedding_fn=embedding_fn, reverse_questions_n=1
    )
    turn = TurnInput(
        question="original q",
        answer="some answer with one claim",
        retrieved_chunks=("supporting chunk",),
    )
    score = asyncio.run(ev.score_turn(turn))
    assert score.faithfulness == pytest.approx(1.0)
    assert score.answer_relevance == pytest.approx(1.0, abs=1e-6)
    assert score.context_precision == pytest.approx(1.0)
    assert score.n_claims == 1
    assert score.n_reverse_qs == 1
    assert score.n_chunks == 1
    assert score.judge_calls >= 3  # claims + verify + context_precision
    assert score.embed_calls >= 2  # 1 reverse-Q × 2 embeds (q + reverse_q)
    assert leftover == []  # all scripted responses consumed
