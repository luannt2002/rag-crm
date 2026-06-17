"""Unit tests for entity-quota-aware RRF (rrf_round_robin).

Proves the two load-bearing properties:
1. Minority entity survives the top_k cut when one entity dominates the pool.
2. Output degrades to plain RRF order when entities are balanced / quota off.
"""

from __future__ import annotations

from ragbot.orchestration.nodes.rrf_round_robin import rrf_round_robin

# Cormack canonical penalty constant. Local test literal (range/index style is
# whitelisted in tests; a fixed k makes the RRF scores hand-checkable).
K = 60


def _chunk(cid: str, entity: str) -> dict:
    return {"chunk_id": cid, "entity": entity, "text": f"chunk-{cid}"}


def _entity_of(chunk: dict):
    return chunk.get("entity")


def _plain_rrf_ids(ranked_lists, *, k: int) -> list[str]:
    """Reference plain-RRF ordering (no fairness layer) for comparison."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    seq = 0
    for results in ranked_lists:
        for rank, ch in enumerate(results):
            cid = str(ch["chunk_id"])
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in first_seen:
                first_seen[cid] = seq
                seq += 1
    return sorted(scores, key=lambda c: (-scores[c], first_seen[c]))


# --------------------------------------------------------------------------- #
# Property 1 — minority entity survives when one entity dominates             #
# --------------------------------------------------------------------------- #
def test_minority_entity_survives_when_majority_dominates():
    # Entity A floods the top of both lists; entity B has one weak chunk that
    # plain RRF would push out of a tight top_k.
    list_a = [
        _chunk("a1", "A"),
        _chunk("a2", "A"),
        _chunk("a3", "A"),
        _chunk("b1", "B"),  # the lone minority chunk, ranked last
    ]
    list_b = [
        _chunk("a1", "A"),
        _chunk("a2", "A"),
        _chunk("a3", "A"),
    ]
    top_k = 3

    # Plain RRF would NOT include b1 in the top-3 (a1/a2/a3 all outscore it).
    plain = _plain_rrf_ids([list_a, list_b], k=K)
    assert "b1" not in plain[:top_k]

    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=1,
        entity_of=_entity_of,
        top_k=top_k,
    )
    out_ids = [c["chunk_id"] for c in out]

    # The minority entity B now keeps a guaranteed slot inside the top_k.
    assert "b1" in out_ids
    assert len(out_ids) == top_k
    entities_present = {c["entity"] for c in out}
    assert entities_present == {"A", "B"}  # both compared entities represented


def test_quota_grants_strongest_chunk_per_entity_first():
    # Entity B has two chunks of differing strength; quota=1 must grant the
    # stronger (earlier-ranked) one, not the weaker.
    list_a = [
        _chunk("a1", "A"),
        _chunk("b_strong", "B"),  # rank 1 -> higher RRF
        _chunk("a2", "A"),
        _chunk("b_weak", "B"),  # rank 3 -> lower RRF
    ]
    list_b = [_chunk("a1", "A"), _chunk("a2", "A")]

    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=1,
        entity_of=_entity_of,
        top_k=2,
    )
    out_ids = [c["chunk_id"] for c in out]
    assert "b_strong" in out_ids
    assert "b_weak" not in out_ids


# --------------------------------------------------------------------------- #
# Property 2 — degrades to plain RRF when balanced / disabled                 #
# --------------------------------------------------------------------------- #
def test_balanced_entities_match_plain_rrf_order():
    # A and B alternate evenly; the fairness layer must not reorder anything.
    list_a = [
        _chunk("a1", "A"),
        _chunk("b1", "B"),
        _chunk("a2", "A"),
        _chunk("b2", "B"),
    ]
    list_b = [
        _chunk("b1", "B"),
        _chunk("a1", "A"),
        _chunk("b2", "B"),
        _chunk("a2", "A"),
    ]

    plain = _plain_rrf_ids([list_a, list_b], k=K)
    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=2,
        entity_of=_entity_of,
    )
    assert [c["chunk_id"] for c in out] == plain


def test_quota_zero_is_plain_rrf():
    list_a = [_chunk("a1", "A"), _chunk("a2", "A"), _chunk("b1", "B")]
    list_b = [_chunk("a1", "A"), _chunk("a2", "A")]

    plain = _plain_rrf_ids([list_a, list_b], k=K)
    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=0,  # fairness disabled
        entity_of=_entity_of,
    )
    assert [c["chunk_id"] for c in out] == plain


def test_single_entity_is_plain_rrf():
    # Only one entity -> nothing to protect -> plain RRF order even with quota.
    list_a = [_chunk("a1", "A"), _chunk("a2", "A"), _chunk("a3", "A")]
    list_b = [_chunk("a2", "A"), _chunk("a1", "A")]

    plain = _plain_rrf_ids([list_a, list_b], k=K)
    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=2,
        entity_of=_entity_of,
    )
    assert [c["chunk_id"] for c in out] == plain


# --------------------------------------------------------------------------- #
# Edge cases                                                                   #
# --------------------------------------------------------------------------- #
def test_empty_input_returns_empty():
    assert rrf_round_robin([], k=K, per_entity_quota=1, entity_of=_entity_of) == []
    assert (
        rrf_round_robin(
            [[], []], k=K, per_entity_quota=1, entity_of=_entity_of
        )
        == []
    )


def test_single_list_returned_unchanged():
    only = [_chunk("a1", "A"), _chunk("b1", "B")]
    out = rrf_round_robin(
        [only], k=K, per_entity_quota=1, entity_of=_entity_of
    )
    assert [c["chunk_id"] for c in out] == ["a1", "b1"]


def test_none_entity_excluded_from_quota_but_kept_in_fill():
    # A chunk with no entity (entity_of -> None) gets no quota grant but is
    # still eligible for the global fill phase.
    def entity_of(chunk):
        return chunk.get("entity")  # "X"/"Y"/None

    list_a = [
        {"chunk_id": "x1", "entity": "X"},
        {"chunk_id": "n1", "entity": None},
        {"chunk_id": "y1", "entity": "Y"},
    ]
    list_b = [
        {"chunk_id": "x1", "entity": "X"},
        {"chunk_id": "y1", "entity": "Y"},
        {"chunk_id": "n1", "entity": None},
    ]
    out = rrf_round_robin(
        [list_a, list_b], k=K, per_entity_quota=1, entity_of=entity_of
    )
    out_ids = [c["chunk_id"] for c in out]
    # Both real entities survive; the entity-less chunk is still present.
    assert {"x1", "y1"} <= set(out_ids)
    assert "n1" in out_ids


def test_score_overwritten_with_rrf_value():
    list_a = [_chunk("a1", "A"), _chunk("b1", "B")]
    list_b = [_chunk("a1", "A"), _chunk("b1", "B")]
    out = rrf_round_robin(
        [list_a, list_b], k=K, per_entity_quota=1, entity_of=_entity_of
    )
    by_id = {c["chunk_id"]: c for c in out}
    # a1 at rank 0 in both lists -> 2/(K+1); b1 at rank 1 in both -> 2/(K+2).
    assert by_id["a1"]["score"] == 2.0 / (K + 1)
    assert by_id["b1"]["score"] == 2.0 / (K + 2)


def test_top_k_caps_output_length():
    list_a = [_chunk(f"a{i}", "A") for i in range(5)] + [_chunk("b1", "B")]
    list_b = [_chunk(f"a{i}", "A") for i in range(5)]
    out = rrf_round_robin(
        [list_a, list_b],
        k=K,
        per_entity_quota=1,
        entity_of=_entity_of,
        top_k=3,
    )
    assert len(out) == 3
    # Minority still survives even under a tight cap.
    assert "b1" in {c["chunk_id"] for c in out}
