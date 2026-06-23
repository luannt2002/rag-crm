"""E-1: entity-fairness round-robin wired into the multi-query RRF merge.

Plain RRF starves a minority entity (one document with few matching chunks)
out of a tight top_k. The retrieve node's merge helper now switches to the
``rrf_round_robin`` fairness layer when (a) the bot opts in via
``entity_fairness_enabled`` AND (b) the intent compares/joins entities. Default
OFF is byte-identical to ``rrf_merge_chunks``.
"""

from __future__ import annotations

from ragbot.application.services.multi_query_expansion import rrf_merge_chunks
from ragbot.orchestration.nodes.retrieve import _merge_multi_query_chunks
from ragbot.shared.constants import DEFAULT_RRF_K


def _two_entity_pool():
    """Majority entity docA (3 chunks) dominates; minority docB has 1 chunk."""
    branch_1 = [
        {"chunk_id": "a1", "document_id": "docA", "text": "A one"},
        {"chunk_id": "a2", "document_id": "docA", "text": "A two"},
        {"chunk_id": "a3", "document_id": "docA", "text": "A three"},
        {"chunk_id": "b1", "document_id": "docB", "text": "B one"},
    ]
    branch_2 = [
        {"chunk_id": "a1", "document_id": "docA", "text": "A one"},
        {"chunk_id": "a2", "document_id": "docA", "text": "A two"},
    ]
    return [branch_1, branch_2]


def test_default_off_is_byte_identical_to_plain_rrf():
    pool = _two_entity_pool()
    expected = rrf_merge_chunks(pool, rrf_k=DEFAULT_RRF_K)
    got = _merge_multi_query_chunks(
        pool, rrf_k=DEFAULT_RRF_K, intent="comparison", pipeline_config=None
    )
    assert got == expected, "default (flag off) must equal plain RRF"


def test_flag_off_even_for_comparison_intent():
    pool = _two_entity_pool()
    expected = rrf_merge_chunks(pool, rrf_k=DEFAULT_RRF_K)
    got = _merge_multi_query_chunks(
        pool,
        rrf_k=DEFAULT_RRF_K,
        intent="comparison",
        pipeline_config={"entity_fairness_enabled": False},
    )
    assert got == expected


def test_flag_on_wrong_intent_is_plain_rrf():
    pool = _two_entity_pool()
    expected = rrf_merge_chunks(pool, rrf_k=DEFAULT_RRF_K)
    got = _merge_multi_query_chunks(
        pool,
        rrf_k=DEFAULT_RRF_K,
        intent="factoid",
        pipeline_config={"entity_fairness_enabled": True},
    )
    assert got == expected, "fairness only applies to comparison/multi_hop intents"


def test_minority_entity_survives_tight_top_k_when_enabled():
    pool = _two_entity_pool()
    merged = _merge_multi_query_chunks(
        pool,
        rrf_k=DEFAULT_RRF_K,
        intent="comparison",
        pipeline_config={
            "entity_fairness_enabled": True,
            "entity_fairness_per_entity_quota": 1,
        },
    )
    # Tight top_k=2: plain RRF would keep a1, a2 (both docA) and drop b1.
    top2 = {c["chunk_id"] for c in merged[:2]}
    assert "b1" in top2, (
        f"minority entity docB starved from tight top_k even with fairness on: {top2}"
    )

    # Contrast: plain RRF drops the minority entity from top_k=2.
    plain = rrf_merge_chunks(pool, rrf_k=DEFAULT_RRF_K)
    plain_top2 = {c["chunk_id"] for c in plain[:2]}
    assert "b1" not in plain_top2, (
        "test premise broken — plain RRF should starve docB in top-2"
    )
