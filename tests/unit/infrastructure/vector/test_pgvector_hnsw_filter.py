"""Lock test — G14 / MEGA-1: HNSW pushdown filter on document_chunks.

Pre-fix the bot filter sat behind ``record_document_id IN (SELECT id
FROM documents WHERE record_bot_id = …)``, a subquery that prevented the
PostgreSQL planner from pushing the predicate INTO the HNSW operator
(``ix_chunks_embedding_hnsw idx_scan = 0`` over a 22 MB index in
production).

Post-fix (alembic 0108 denormalizes ``record_bot_id`` onto
``document_chunks``) the WHERE fragment LEADS with the local column
``record_bot_id = :record_bot_id`` so the planner uses ``ix_chunks_bot``
first → HNSW pushdown still activates.

P0 soft-delete gate (commit ``4e8a83d``, multi-agent-r4-260516):
``_doc_filter_sql`` ALWAYS AND-s an ``EXISTS (SELECT 1 FROM documents
... AND d.deleted_at IS NULL)`` after the local ``record_bot_id``
predicate. Before that fix, chunks from soft-deleted documents leaked
into search because the empty-doc_meta path skipped the documents
subquery entirely. The local-column predicate STILL LEADS, so the
planner picks ``ix_chunks_bot`` first then prunes via EXISTS — HNSW
push-down remains active (verified post-migration via
``pg_stat_user_indexes.idx_scan``).

These tests are static-string assertions on the SQL fragment built by
``_doc_filter_sql`` because the planner behaviour itself is verified
post-migration via ``pg_stat_user_indexes.idx_scan`` (smoke step
documented in the assignment).
"""
from __future__ import annotations

import inspect
import json
import uuid
from unittest.mock import MagicMock

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def _store() -> PgVectorStore:
    return PgVectorStore(MagicMock())


def test_no_filter_uses_local_record_bot_id_predicate() -> None:
    """Hot path — no metadata filter — must LEAD with the local
    ``record_bot_id`` column predicate so the planner picks
    ``ix_chunks_bot_active`` (partial index from Wave M3.5-C migration
    010p) first and HNSW pushdown activates. The soft-delete gate is
    now a direct ``doc_deleted_at IS NULL`` column comparison
    (denormalised from ``documents.deleted_at`` via trigger) rather
    than the pre-fix correlated EXISTS subquery that executed
    per-candidate row.
    """
    store = _store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(bot_id)
    assert clause.startswith("record_bot_id = :record_bot_id"), (
        f"hot path must lead with local-column predicate; got: {clause!r}"
    )
    # Soft-delete gate is now a direct column comparison (no subquery).
    assert "doc_deleted_at IS NULL" in clause
    # The pre-fix EXISTS subquery must NOT appear — that's the whole point
    # of Wave M3.5-C.
    assert "EXISTS" not in clause, (
        f"M3.5-C removed EXISTS subquery; got: {clause!r}"
    )
    assert params == {"record_bot_id": bot_id}


def test_no_filter_does_not_join_documents() -> None:
    """MEGA-1 / G14 invariant + Wave M3.5-C: the local ``record_bot_id``
    predicate LEADS the WHERE (so planner picks ``ix_chunks_bot_active``
    partial index first / HNSW pushdown active) AND the soft-delete
    gate is a direct ``doc_deleted_at`` column predicate (NOT a JOIN /
    EXISTS / IN-subquery against ``documents``). The pre-fix forms
    (both ``record_document_id IN (SELECT ...)`` and the correlated
    ``EXISTS (SELECT 1 FROM documents d ...)``) executed per-candidate
    and killed HNSW pushdown.
    """
    store = _store()
    clause, _ = store._doc_filter_sql(uuid.uuid4())
    lower = clause.lower()
    # Local predicate leads.
    assert lower.startswith("record_bot_id = :record_bot_id"), (
        f"local bot filter must lead the WHERE: {clause!r}"
    )
    # Soft-delete gate present as a direct column comparison.
    assert "doc_deleted_at is null" in lower
    # Neither pre-fix subquery form must appear.
    assert "record_document_id in (select id from documents" not in lower
    assert "exists" not in lower, (
        f"Wave M3.5-C removed EXISTS subquery; got: {clause!r}"
    )


def test_chunk_level_filter_keeps_local_predicate_no_documents_join() -> None:
    """Article-no / clause-no filters live on chunks themselves — must
    AND onto the local-column predicate. The local predicate LEADS
    (``ix_chunks_bot`` first) and the chunk JSONB clause closes the
    WHERE so HNSW pushdown stays active. The soft-delete EXISTS
    subquery is present (P0 fix) but does NOT add the pre-fix
    ``record_document_id IN (SELECT …)`` pushdown-killing pattern."""
    store = _store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(
        bot_id, metadata_filter={"article_no": "38"},
    )
    lower = clause.lower()
    assert clause.startswith("record_bot_id = :record_bot_id"), (
        f"local bot filter must lead the WHERE: {clause!r}"
    )
    assert "metadata_json @> CAST(:chunk_metadata_filter AS jsonb)" in clause
    # No doc-meta filter requested → must NOT use the doc-meta subquery form
    # (``record_document_id IN (SELECT id FROM documents ...)``).
    assert "record_document_id in (select id from documents" not in lower, (
        f"chunk-only filter must not pull in doc-meta subquery: {clause!r}"
    )
    assert json.loads(params["chunk_metadata_filter"]) == {"article_no": "38"}


def test_doc_level_filter_adds_documents_subquery_after_local_predicate() -> None:
    """Doc-level keys (``document_type``, ``entity``) live on
    ``documents.metadata_json`` so they DO require the subquery — but it
    must AND-onto the local bot predicate (planner uses ``ix_chunks_bot``
    first)."""
    store = _store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(
        bot_id, metadata_filter={"document_type": "law"},
    )
    assert clause.startswith("record_bot_id = :record_bot_id"), (
        f"local bot filter must lead the WHERE: {clause!r}"
    )
    assert "AND record_document_id IN (" in clause
    assert "SELECT id FROM documents" in clause
    assert "deleted_at IS NULL" in clause
    assert json.loads(params["metadata_filter"]) == {"document_type": "law"}


def test_mixed_filter_keeps_both_predicates_in_expected_order() -> None:
    """Mix of doc-level + chunk-level — both predicates AND-ed in order
    (local bot → doc subquery → chunk metadata)."""
    store = _store()
    clause, params = store._doc_filter_sql(
        uuid.uuid4(),
        metadata_filter={"document_type": "law", "article_no": "38"},
    )
    bot_idx = clause.find("record_bot_id = :record_bot_id")
    doc_idx = clause.find("record_document_id IN (")
    chunk_idx = clause.find("metadata_json @> CAST(:chunk_metadata_filter")
    assert -1 not in {bot_idx, doc_idx, chunk_idx}, (
        f"all three predicates must be present: {clause!r}"
    )
    assert bot_idx < doc_idx < chunk_idx, (
        f"predicate order broken — local → doc → chunk: {clause!r}"
    )


def test_search_signature_preserves_record_bot_id_required() -> None:
    """The MEGA-1 fix must not loosen the bot-isolation invariant —
    ``record_bot_id`` stays REQUIRED on search."""
    sig = inspect.signature(PgVectorStore.search)
    assert "record_bot_id" in sig.parameters
    # Keyword-only (declared after the * marker in the source).
    assert sig.parameters["record_bot_id"].kind == inspect.Parameter.KEYWORD_ONLY


def test_hot_path_predicate_is_index_friendly_string() -> None:
    """Defensive contract: the hot-path predicate must LEAD with the
    simple column-equality form that PG can push INTO HNSW, and must
    NOT regress to the pre-fix ``record_document_id IN (SELECT id FROM
    documents WHERE record_bot_id = …)`` subquery that killed HNSW
    activation in production (``ix_chunks_embedding_hnsw idx_scan = 0``).

    The soft-delete EXISTS subquery added by the P0 fix is allowed —
    the planner still picks ``ix_chunks_bot`` first via the leading
    local predicate, then prunes via EXISTS. Anti-pattern: re-promoting
    the documents subquery to LEAD the WHERE (or replacing the local
    predicate with a doc-side filter) would silently kill HNSW —
    these assertions catch that regression.
    """
    store = _store()
    clause, _ = store._doc_filter_sql(uuid.uuid4())
    lower = clause.lower()
    # Local predicate MUST lead — this is the planner-relevant invariant.
    assert lower.startswith("record_bot_id = :record_bot_id"), (
        f"HNSW-pushdown regression — local predicate no longer leads: {clause!r}"
    )
    # The pre-fix HNSW-killing subquery pattern must NEVER appear on
    # the hot path (no doc-meta filter requested).
    assert "record_document_id in (select id from documents" not in lower, (
        f"HNSW-pushdown regression — pre-fix subquery pattern back: {clause!r}"
    )
    # No JOIN keyword (the soft-delete EXISTS is a correlated subquery,
    # not a JOIN — JOIN in the hot path would suggest doc-meta got mixed in).
    assert " join " not in f" {lower} ", (
        f"HNSW-pushdown regression — unexpected JOIN in hot path: {clause!r}"
    )
