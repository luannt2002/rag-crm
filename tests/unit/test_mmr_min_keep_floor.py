"""Step-4 (002 cluster D): mmr_dedup collapse floor.

MEASURED (specs/002 evidence, zembed-1 on a sectioned policy doc): distant
DISTINCT-section pairs cosine p50=0.975 / max=0.990 vs adjacent near-dup pairs
p50=0.982 — the two distributions overlap almost completely, so NO similarity
threshold can separate them (at the old 0.88 default, 100% of distinct-section
pairs were wrongly deduped → the 6→1 collapse that starved warranty answers
into fabricated scope). Threshold recalibration alone CANNOT fix this class —
the floor guarantee is the primary fix: never collapse below ``min_keep``
survivors; when the ceiling would drop everything, force-keep by relevance.
"""
from __future__ import annotations

from ragbot.shared.mmr import mmr_filter


def _chunk(cid: str, score: float) -> dict:
    # identical embeddings → pairwise cosine == 1.0 > any threshold
    return {"chunk_id": cid, "score": score, "content": f"section {cid} text",
            "embedding": [1.0, 0.0, 0.0]}


_SIX = [_chunk(f"c{i}", 0.9 - i * 0.1) for i in range(6)]


def test_floor_keeps_min_keep_by_relevance() -> None:
    out = mmr_filter([dict(c) for c in _SIX], lambda_param=0.7,
                     similarity_threshold=0.88, min_keep=3)
    assert len(out) == 3, "floor must survive the all-similar collapse"
    assert [c["chunk_id"] for c in out] == ["c0", "c1", "c2"], "forced picks = best relevance"


def test_floor_default_one_preserves_legacy() -> None:
    out = mmr_filter([dict(c) for c in _SIX], lambda_param=0.7,
                     similarity_threshold=0.88)
    assert len(out) == 1  # legacy behavior when floor not requested


def test_floor_caps_at_input_size() -> None:
    two = [dict(c) for c in _SIX[:2]]
    out = mmr_filter(two, lambda_param=0.7, similarity_threshold=0.88, min_keep=5)
    assert len(out) == 2


def test_node_reads_min_keep_knob_and_threshold_recalibrated() -> None:
    import inspect
    from ragbot.orchestration.nodes import mmr_dedup as node
    from ragbot.shared.constants import (
        DEFAULT_MMR_MIN_KEEP,
        DEFAULT_MMR_SIMILARITY_THRESHOLD,
    )

    src = inspect.getsource(node)
    assert "mmr_min_keep" in src and "DEFAULT_MMR_MIN_KEEP" in src
    assert DEFAULT_MMR_MIN_KEEP >= 3
    # measured recalibration: 0.88 wrongly deduped 100% distinct sections
    assert DEFAULT_MMR_SIMILARITY_THRESHOLD >= 0.98
