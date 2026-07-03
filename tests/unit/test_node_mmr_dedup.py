"""Unit tests for `mmr_dedup` orchestration node.

Covers the ~22 LoC node at `query_graph.py:2452`. Each test drives the
node via its closure (`compiled.nodes['mmr_dedup'].bound.afunc`) so we
can pass in a controlled `reranked_chunks` payload and assert on:

- output shape (`{"reranked_chunks": list[...]}`)
- audit event payload (`mmr_dedup` with before/after/lambda/threshold)
- step tracker wrapping (single `mmr_dedup` step entry)
- threshold + lambda config respected
"""

from __future__ import annotations

import asyncio

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_state,
    node_callable,
)


def _chunks(texts_and_scores: list[tuple[str, float]]) -> list[dict]:
    return [
        {
            "chunk_id": f"c{i}",
            "id": f"c{i}",
            "text": text,
            "content": text,
            "score": score,
            "document_name": "doc",
            "chunk_index": i,
        }
        for i, (text, score) in enumerate(texts_and_scores)
    ]


def _run(compiled, state):
    afunc = node_callable(compiled, "mmr_dedup")
    return asyncio.run(afunc(state))


def test_mmr_dedup_returns_empty_reranked_when_input_empty():
    compiled, tracker, audit, *_ = build_test_graph()
    state = make_state(reranked_chunks=[])
    out = _run(compiled, state)
    assert out == {"reranked_chunks": []}
    # Step always wraps; audit always fires with before=0, after=0
    assert tracker.by_name("mmr_dedup"), tracker.names()
    payloads = audit.by_event("mmr_dedup")
    assert payloads and payloads[-1]["before"] == 0
    assert payloads[-1]["after"] == 0


def test_mmr_dedup_preserves_diverse_chunks():
    compiled, _tracker, audit, *_ = build_test_graph()
    chunks = _chunks(
        [
            ("Tokenizer benchmark report on Wikipedia corpus", 0.9),
            ("Pricing list with monthly subscription tiers", 0.8),
            ("Customer support hours and parking guide", 0.7),
        ]
    )
    out = _run(compiled, make_state(reranked_chunks=chunks))
    # All three are textually unrelated -> MMR should keep all of them at
    # threshold 0.88 (default).
    assert len(out["reranked_chunks"]) == 3
    payload = audit.by_event("mmr_dedup")[-1]
    assert payload["before"] == 3
    assert payload["after"] == 3


def test_mmr_dedup_drops_near_duplicate_at_default_threshold():
    """002-D contract update: the survivor FLOOR (DEFAULT_MMR_MIN_KEEP=3)
    outranks dedup — measured on zembed-1, no threshold separates distinct
    sections from near-dups, so small contexts must never be collapsed
    (the 6→1 warranty collapse starved generate into fabricating scope).
    Dedup therefore manifests only ABOVE the floor: with 5 candidates and
    identical texts, the dup is dropped but never below 3 survivors."""
    compiled, _tracker, audit, *_ = build_test_graph()
    near_dup = "ABCDE FGHIJ KLMNO PQRST UVWXY"
    chunks = _chunks(
        [
            (near_dup, 0.95),
            (near_dup, 0.94),  # identical text → similarity = 1.0
            (near_dup, 0.93),
            (near_dup, 0.92),
            ("entirely different topic about parking", 0.5),
        ]
    )
    out = _run(compiled, make_state(reranked_chunks=chunks))
    payload = audit.by_event("mmr_dedup")[-1]
    assert payload["before"] == 5
    # dedup fires above the floor…
    assert payload["after"] < 5
    # …but the floor is never pierced (002-D).
    assert payload["after"] >= 3


def test_mmr_dedup_respects_lambda_param_in_audit():
    compiled, _tracker, audit, *_ = build_test_graph()
    chunks = _chunks([("alpha", 0.9), ("beta", 0.8)])
    state = make_state(
        reranked_chunks=chunks,
        pipeline_config={"mmr_lambda": 0.3, "mmr_similarity_threshold": 0.5},
    )
    _ = _run(compiled, state)
    payload = audit.by_event("mmr_dedup")[-1]
    # Configured values must be reflected in the audit event verbatim.
    assert payload["lambda"] == pytest.approx(0.3)
    assert payload["similarity_threshold"] == pytest.approx(0.5)


def test_mmr_dedup_step_wraps_exactly_once_per_call():
    compiled, tracker, *_ = build_test_graph()
    state = make_state(reranked_chunks=_chunks([("one", 0.9)]))
    _ = _run(compiled, state)
    _ = _run(compiled, state)  # call again to confirm no leak between runs
    # tracker accumulates across calls — 2 invocations → 2 wraps
    assert len(tracker.by_name("mmr_dedup")) == 2


def test_mmr_dedup_emits_audit_with_full_payload_keys():
    """Regression guard for the audit schema: 4 keys MUST always appear."""
    compiled, _tracker, audit, *_ = build_test_graph()
    state = make_state(reranked_chunks=_chunks([("only", 0.6)]))
    _ = _run(compiled, state)
    payload = audit.by_event("mmr_dedup")[-1]
    expected_keys = {"before", "after", "lambda", "similarity_threshold"}
    assert expected_keys.issubset(payload.keys()), payload


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
