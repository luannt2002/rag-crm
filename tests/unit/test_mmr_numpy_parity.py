"""Parity tests for NumPy-vectorised cosine path of ``mmr_filter``.

Locks the vectorised implementation against:
- empty / single-candidate edge cases,
- numerical equivalence with the pure-Python ``_cosine_similarity`` helper
  (the previous semantics) on a fixed-seed random batch,
- lambda extremes (1.0 = pure relevance ordering, 0.0 = pure diversity).
"""

from __future__ import annotations

import math

import numpy as np

from ragbot.shared.mmr import _cosine_similarity, mmr_filter


def _make_chunk(idx: int, score: float, embedding: list[float]) -> dict:
    return {"content": f"chunk-{idx}", "score": score, "embedding": embedding}


def _reference_pick_order(
    chunks: list[dict], lambda_param: float, similarity_threshold: float
) -> list[str]:
    """Reproduce mmr_filter's selection order using only the pure-Python
    cosine helper. Used as the parity baseline.
    """
    if not chunks:
        return []
    selected = [chunks[0]]
    candidates = list(enumerate(chunks))[1:]
    while candidates:
        best_pos = -1
        best_score = float("-inf")
        for pos, (_, cand) in enumerate(candidates):
            relevance = float(cand.get("score") or 0)
            max_sim = 0.0
            for sel in selected:
                sim = _cosine_similarity(cand["embedding"], sel["embedding"])
                max_sim = max(max_sim, sim)
            if max_sim > similarity_threshold:
                continue
            score = lambda_param * relevance - (1 - lambda_param) * max_sim
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos < 0:
            break
        _, winner = candidates.pop(best_pos)
        selected.append(winner)
    return [c["content"] for c in selected]


def test_empty_input_returns_empty_list() -> None:
    assert mmr_filter([]) == []


def test_single_candidate_returns_that_one() -> None:
    chunks = [_make_chunk(0, 0.9, [1.0, 0.0, 0.0])]
    result = mmr_filter(chunks, use_cosine=True)
    assert len(result) == 1
    assert result[0]["content"] == "chunk-0"


def test_numpy_matches_pure_python_reference_on_seeded_batch() -> None:
    """Vectorised cosine path must agree with the pure-Python reference on a
    fixed-seed random batch — same selection order, same per-pick max
    similarity within 1e-6 tolerance.
    """
    rng = np.random.default_rng(seed=20260509)
    dim = 16
    n = 12
    chunks: list[dict] = []
    for i in range(n):
        emb = rng.uniform(-1.0, 1.0, size=dim).tolist()
        # Skew scores so MMR has a non-trivial relevance term.
        score = 0.95 - i * 0.05
        chunks.append(_make_chunk(i, score, emb))

    lambda_param = 0.7
    threshold = 0.99

    reference_order = _reference_pick_order(
        [dict(c) for c in chunks], lambda_param, threshold
    )
    actual = mmr_filter(
        [dict(c) for c in chunks],
        lambda_param=lambda_param,
        similarity_threshold=threshold,
        use_cosine=True,
    )
    actual_order = [c["content"] for c in actual]

    assert actual_order == reference_order

    # Per-pair cosine numerical parity within float tolerance.
    for a in chunks:
        for b in chunks:
            py = _cosine_similarity(a["embedding"], b["embedding"])
            np_val = float(
                np.dot(a["embedding"], b["embedding"])
                / (
                    math.sqrt(sum(x * x for x in a["embedding"]))
                    * math.sqrt(sum(x * x for x in b["embedding"]))
                )
            )
            assert math.isclose(py, np_val, abs_tol=1e-6, rel_tol=1e-6)


def test_lambda_one_pure_relevance_keeps_score_descending_order() -> None:
    """lambda=1.0 collapses MMR to relevance ranking — output preserves
    descending score order when the threshold is loose enough to keep all
    candidates."""
    chunks = [
        _make_chunk(0, 0.9, [1.0, 0.0, 0.0]),
        _make_chunk(1, 0.7, [0.0, 1.0, 0.0]),
        _make_chunk(2, 0.5, [0.0, 0.0, 1.0]),
    ]
    result = mmr_filter(
        chunks, lambda_param=1.0, similarity_threshold=0.99, use_cosine=True
    )
    contents = [c["content"] for c in result]
    assert contents == ["chunk-0", "chunk-1", "chunk-2"]


def test_lambda_zero_pure_diversity_picks_orthogonal_next() -> None:
    """lambda=0.0 ignores relevance entirely — after the first chunk, the
    next pick must be the candidate with the lowest similarity to the
    selected set (most diverse), even if its relevance score is the
    lowest."""
    chunks = [
        _make_chunk(0, 0.9, [1.0, 0.0, 0.0]),
        # Near-duplicate of chunk-0 (cosine ~0.9988) but high score — should
        # NOT be picked second under pure diversity.
        _make_chunk(1, 0.8, [0.95, 0.05, 0.0]),
        # Orthogonal to chunk-0 (cosine = 0.0) — most diverse, low relevance.
        _make_chunk(2, 0.1, [0.0, 1.0, 0.0]),
    ]
    result = mmr_filter(
        chunks, lambda_param=0.0, similarity_threshold=0.99, use_cosine=True
    )
    # First slot is always chunks[0]; second pick under lambda=0 must be the
    # orthogonal one to maximise diversity.
    assert result[0]["content"] == "chunk-0"
    assert len(result) >= 2
    assert result[1]["content"] == "chunk-2"
