"""Pin STEP-5 attribution decouple — _build_stats_attributed_refs (Phase B-1).

The stats route answers from a synthetic chunk (sentinel id → FK-skipped by
``_build_chunk_refs``), so its retrieval is invisible to CHUNK_RECALL. The helper
attributes it to the matched entities' REAL source chunks (``record_chunk_id``)
WITHOUT adding raw chunks to the LLM context — measurement decoupled from the
answer, so HALLU=0 / COVERAGE are untouched (verified live: CHUNK_RECALL
0.31→0.85, COVERAGE 0.95 held, HALLU=0).
"""
from __future__ import annotations

from ragbot.interfaces.http.routes.test_chat.chat_routes import (
    _build_stats_attributed_refs,
)


def test_graded_chunks_become_refs() -> None:
    graded = [{"chunk_id": "c1", "score": 0.9}, {"id": "c2", "score": 0.5}]
    refs = _build_stats_attributed_refs(graded, {})
    assert [r["chunk_id"] for r in refs] == ["c1", "c2"]
    assert refs[0]["rank"] == 0 and refs[1]["rank"] == 1
    assert refs[0]["score"] == 0.9


def test_stats_entities_attributed_without_touching_graded() -> None:
    # synthetic chunk (sentinel) + entities pointing at REAL source chunks.
    graded = [{"chunk_id": "SENTINEL", "score": 1.0}]
    final_state = {
        "stats_entities": [
            {"record_chunk_id": "u1"},
            {"record_chunk_id": "u2"},
            {"record_chunk_id": None},   # NULL FK → skipped
            {"entity_name": "no-fk"},     # missing key → skipped
        ]
    }
    refs = _build_stats_attributed_refs(graded, final_state)
    ids = [r["chunk_id"] for r in refs]
    assert "SENTINEL" in ids            # graded chunk (LLM context) preserved
    assert "u1" in ids and "u2" in ids  # entity source chunks attributed
    assert None not in ids              # null record_chunk_id dropped
    assert len(refs) == 3               # SENTINEL + u1 + u2


def test_dedup_entity_already_in_graded() -> None:
    graded = [{"chunk_id": "u1", "score": 0.8}]
    final_state = {"stats_entities": [{"record_chunk_id": "u1"}]}  # same id
    refs = _build_stats_attributed_refs(graded, final_state)
    assert [r["chunk_id"] for r in refs].count("u1") == 1  # not double-written


def test_no_stats_entities_is_passthrough() -> None:
    graded = [{"chunk_id": "c1", "score": 0.9}]
    assert len(_build_stats_attributed_refs(graded, {})) == 1
    assert len(_build_stats_attributed_refs(graded, None)) == 1
    assert _build_stats_attributed_refs([], {}) == []
