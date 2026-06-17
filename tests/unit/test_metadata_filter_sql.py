"""Unit tests — pgvector_store ``_doc_filter_sql`` metadata filter wiring.

Pure-SQL test: we don't need a live Postgres connection because
``_doc_filter_sql`` returns ``(clause, params)`` deterministically.
The integration test in ``tests/integration`` covers end-to-end
behaviour against a real DB.
"""

from __future__ import annotations

import json
import uuid

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def _make_store() -> PgVectorStore:
    """Construct a store with a no-op session factory (pure-SQL test)."""
    return PgVectorStore(session_factory=lambda: None, dimension=8)  # type: ignore[arg-type]


def test_no_metadata_filter_omits_jsonb_clause() -> None:
    """Default (None) → behaviour unchanged from ."""
    store = _make_store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(bot_id)

    assert "metadata_json" not in clause
    assert "@>" not in clause
    assert params == {"record_bot_id": bot_id}


def test_empty_metadata_filter_omits_jsonb_clause() -> None:
    """Empty dict is treated as "no filter" so the SQL stays identical."""
    store = _make_store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(bot_id, metadata_filter={})

    assert "metadata_json" not in clause
    assert "metadata_filter" not in params


def test_metadata_filter_adds_containment_clause() -> None:
    """Non-empty dict → adds ``metadata_json @> :metadata_filter::jsonb``
    INSIDE the documents subquery (write-side stores extracted metadata
    on ``documents.metadata_json``, not on ``document_chunks``)."""
    store = _make_store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(
        bot_id, metadata_filter={"document_type": "price_list"}
    )

    assert "metadata_json @>" in clause
    assert ":metadata_filter" in clause
    assert "CAST(:metadata_filter AS jsonb)" in clause
    # The containment clause MUST sit inside the documents subquery —
    # otherwise it would filter document_chunks.metadata_json (always
    # empty for legacy chunks) and silently return 0 rows.
    inner_subquery_start = clause.index("FROM documents")
    inner_subquery_end = clause.rindex(")")
    inner = clause[inner_subquery_start:inner_subquery_end]
    assert "metadata_json @>" in inner, (
        "filter must live inside the documents subquery, not outside"
    )
    # Value must be JSON-serialised so psycopg/asyncpg accept it as a
    # text bind parameter — Postgres casts it to jsonb in-SQL.
    assert "metadata_filter" in params
    payload = json.loads(params["metadata_filter"])
    assert payload == {"document_type": "price_list"}


def test_record_bot_id_clause_still_present_with_filter() -> None:
    """Bot scope MUST stay in place even when metadata filter is set."""
    store = _make_store()
    bot_id = uuid.uuid4()
    clause, params = store._doc_filter_sql(
        bot_id, metadata_filter={"document_type": "policy"}
    )

    assert "record_bot_id = :record_bot_id" in clause
    assert "deleted_at IS NULL" in clause
    assert params["record_bot_id"] == bot_id


def test_metadata_filter_supports_multiple_keys() -> None:
    """Multi-key dict survives JSON serialisation in deterministic order
    (order doesn't matter for ``@>`` containment)."""
    store = _make_store()
    bot_id = uuid.uuid4()
    _, params = store._doc_filter_sql(
        bot_id,
        metadata_filter={"document_type": "faq", "entity": "billing"},
    )
    payload = json.loads(params["metadata_filter"])
    assert payload == {"document_type": "faq", "entity": "billing"}
