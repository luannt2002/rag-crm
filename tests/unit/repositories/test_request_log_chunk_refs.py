"""G15 regression — request_chunk_refs split from request_logs.retrieved_chunks.

Pre-G15 ``RequestLogRepository.finalize_request_log`` wrote retrieved-chunk
metadata into an inline JSONB column on ``request_logs``. Alembic 0109
drops the column and splits the data into the relational
``request_chunk_refs`` child table.

These tests pin the pure-Python transform helper ``_build_chunk_refs``
which the repository now calls inside ``finalize_request_log`` to map
the caller's ``[{chunk_id, rank, score}, ...]`` list to ``RequestChunkRefModel``
rows. The helper guards three behaviours that matter for the FK contract:

1. Rows missing a parseable UUID ``chunk_id`` MUST be dropped (the new
   table FK-constrains ``record_chunk_id`` to ``document_chunks.id`` so
   a NULL / non-UUID would fail the INSERT and abort the whole turn).
2. ``rank`` MUST default to the array index when the caller omits it
   (legacy callers passed ``score`` + ``preview`` only).
3. ``score`` MUST round-trip through ``Decimal`` so the ``NUMERIC(8,6)``
   column stores the exact value (float bin → str → Decimal).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ragbot.infrastructure.db.models_monitoring import RequestChunkRefModel
from ragbot.infrastructure.repositories.request_log_repository import (
    RequestLogRepository,
)


_REQUEST_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def test_build_chunk_refs_returns_empty_for_no_input() -> None:
    """``None`` / empty list → no refs (caller passes both shapes)."""
    assert RequestLogRepository._build_chunk_refs(_REQUEST_ID, None) == []
    assert RequestLogRepository._build_chunk_refs(_REQUEST_ID, []) == []


def test_build_chunk_refs_maps_valid_chunk_id_payloads() -> None:
    """Happy path — chunk_id present, rank + score forwarded verbatim."""
    cid1 = uuid4()
    cid2 = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [
            {"chunk_id": str(cid1), "rank": 0, "score": 0.95},
            {"chunk_id": str(cid2), "rank": 1, "score": 0.42},
        ],
    )
    assert len(refs) == 2
    assert all(isinstance(r, RequestChunkRefModel) for r in refs)

    assert refs[0].record_request_id == _REQUEST_ID
    assert refs[0].record_chunk_id == cid1
    assert refs[0].rank == 0
    assert refs[0].score == Decimal("0.95")

    assert refs[1].record_chunk_id == cid2
    assert refs[1].rank == 1
    assert refs[1].score == Decimal("0.42")


def test_build_chunk_refs_accepts_id_synonym_for_chunk_id() -> None:
    """Legacy callers in the codebase pass ``id`` instead of ``chunk_id``."""
    cid = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [{"id": str(cid), "score": 0.7}],
    )
    assert len(refs) == 1
    assert refs[0].record_chunk_id == cid


def test_build_chunk_refs_drops_rows_with_no_chunk_id() -> None:
    """Pre-G15 callers passed only ``chunk_index`` + ``preview`` -- skip them.

    The new FK constraint would reject a NULL ``record_chunk_id`` and abort
    the request_log commit, so the helper MUST quietly drop these rows.
    """
    cid = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [
            {"chunk_index": 0, "preview": "...", "score": 0.9},   # no id
            {"chunk_id": "not-a-uuid", "score": 0.5},             # bad id
            {"chunk_id": None, "score": 0.5},                      # null id
            {"chunk_id": str(cid), "score": 0.8},                  # valid
        ],
    )
    assert len(refs) == 1
    assert refs[0].record_chunk_id == cid


def test_build_chunk_refs_defaults_rank_to_index_when_missing() -> None:
    """Some callers populate only chunk_id + score; rank should fall back."""
    cid_a = uuid4()
    cid_b = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [
            {"chunk_id": str(cid_a), "score": 0.5},
            {"chunk_id": str(cid_b), "score": 0.4},
        ],
    )
    # No ``rank`` in either dict → expect (0, 1) from enumerate.
    assert [r.rank for r in refs] == [0, 1]


def test_build_chunk_refs_handles_missing_score_as_null() -> None:
    """score is NUMERIC(8,6) NULL-able -- preserve that distinction."""
    cid = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [{"chunk_id": str(cid)}],   # no score key at all
    )
    assert len(refs) == 1
    assert refs[0].score is None


def test_build_chunk_refs_skips_non_dict_elements_defensively() -> None:
    """Edge: caller passed a stray string / int in the list -- don't crash."""
    cid = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        ["garbage", 42, None, {"chunk_id": str(cid), "score": 0.6}],
    )
    assert len(refs) == 1
    assert refs[0].record_chunk_id == cid


def test_build_chunk_refs_coerces_invalid_rank_to_index() -> None:
    """``rank`` of wrong type → fall back to enumerate index (no crash)."""
    cid = uuid4()
    refs = RequestLogRepository._build_chunk_refs(
        _REQUEST_ID,
        [{"chunk_id": str(cid), "rank": "not-an-int", "score": 0.5}],
    )
    assert refs[0].rank == 0   # idx fallback


def test_request_chunk_ref_model_has_cascade_fks_in_metadata() -> None:
    """Sanity: the ORM definition declares CASCADE on both FK columns.

    The migration physically sets ``ON DELETE CASCADE`` -- if the ORM
    declares anything else, future schema rebuilds (``Base.metadata.create_all``
    in tests) would drift from production behaviour.

    We inspect the FK SPEC string instead of resolving the column (which
    would require the parent tables to be importable in the test session).
    """
    table = RequestChunkRefModel.__table__
    fk_specs = [
        (fk.parent.name, fk._colspec, fk.ondelete)
        for fk in table.foreign_keys
    ]
    assert ("record_request_id", "request_logs.request_id", "CASCADE") in fk_specs
    assert ("record_chunk_id", "document_chunks.id", "CASCADE") in fk_specs


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
