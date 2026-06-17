"""Integration test — metadata-aware retrieval (Gap B.8).

Inserts two documents under the same bot, each with a distinct
``documents.metadata_json``. Verifies:

1. ``hybrid_search`` with ``metadata_filter={"document_type":"price_list"}``
   returns ONLY the price_list document.
2. ``hybrid_search`` with ``metadata_filter=None`` returns BOTH documents
   (legacy behaviour preserved).
3. ``hybrid_search`` with a filter that matches NOTHING returns ``[]``,
   so the relax-fallback path in ``query_graph`` triggers in production.

Hits real Postgres via ``DATABASE_URL`` — same fixture pattern as the
existing 3-key isolation suite.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL=") and "DATABASE_URL_SYNC" not in line:
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not set and .env not found")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
    """Idempotent insert into ``tenants`` so the FK on ``bots`` is satisfied."""
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": record_tenant_id, "name": f"test-{str(record_tenant_id)[:8]}"},
        )
        await session.commit()


async def _insert_bot(
    sf: Any, *, record_bot_id: uuid.UUID, record_tenant_id: uuid.UUID,
) -> None:
    """Minimal bot row honouring the 4-key + NOT-NULL constraints."""
    await _ensure_tenant_row(sf, record_tenant_id)
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO bots (
                    id, record_tenant_id, workspace_id, bot_id, channel_type,
                    bot_name, system_prompt, is_deleted,
                    created_at, updated_at, setting_options,
                    custom_vocabulary, max_documents, plan_limits,
                    bypass_token_limit, bypass_rate_limit, language
                )
                VALUES (
                    :id, :rt, :ws, :bot_id, 'web',
                    :name, '', false,
                    now(), now(), CAST(:opts AS jsonb),
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """,
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{record_bot_id.hex[:8]}",
                "bot_id": f"meta-aware-{record_bot_id.hex[:8]}",
                "name": "meta-aware-test-bot",
                "opts": json.dumps({}),
            },
        )
        await session.commit()


async def _insert_doc_with_chunk(
    sf: Any,
    *,
    record_bot_id: uuid.UUID,
    metadata: dict[str, Any],
    content: str,
    embedding: list[float],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one document + one chunk with deterministic embedding."""
    record_doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (id, record_bot_id, source_url, document_name,
                    tool_name, mime_type, language, state, version, content_hash,
                    acl, metadata_json, raw_content, content_chars,
                    created_at, updated_at)
                VALUES (:id, :bot, '', :name, 'manual', 'text/plain', 'vi',
                    'ready', 1, :hash, ARRAY[]::text[], CAST(:meta AS jsonb),
                    :raw, :chars, now(), now())
                """,
            ),
            {
                "id": record_doc_id,
                "bot": record_bot_id,
                "name": metadata.get("title", "doc"),
                "hash": uuid.uuid4().hex,
                "meta": json.dumps(metadata),
                "raw": content,
                "chars": len(content),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (id, record_document_id, chunk_index,
                    content, content_hash, embedding, metadata_json, chunk_chars,
                    created_at)
                VALUES (:id, :doc, 0, :content, :hash, CAST(:emb AS vector),
                    CAST(:meta AS jsonb), :chars, now())
                """,
            ),
            {
                "id": chunk_id,
                "doc": record_doc_id,
                "content": content,
                "hash": uuid.uuid4().hex,
                "emb": str(embedding),
                "meta": json.dumps({}),
                "chars": len(content),
            },
        )
        await session.commit()
    return record_doc_id, chunk_id


async def _cleanup(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text(
                """
                DELETE FROM document_chunks WHERE record_document_id IN
                    (SELECT id FROM documents WHERE record_bot_id = :bot)
                """,
            ),
            {"bot": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE record_bot_id = :bot"),
            {"bot": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :bot"), {"bot": record_bot_id},
        )
        await session.commit()


def _embedding(seed: int, dim: int = 1536) -> list[float]:
    """Deterministic random embedding for the test (matches default 1536)."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


@pytest.mark.asyncio
async def test_metadata_filter_narrows_results(session_factory: Any) -> None:
    """Filter by ``document_type=price_list`` returns only the matching doc;
    no filter returns both; an unmatched filter returns empty so the relax
    fallback can engage in production."""
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000005101")

    # Pre-clean by bot uuid (cheap; uuid is unique per run).
    await _cleanup(session_factory, record_bot_id)

    await _insert_bot(
        session_factory,
        record_bot_id=record_bot_id,
        record_tenant_id=record_tenant_id,
    )

    # Two docs, distinct metadata. Identical embeddings so dense ranking
    # ties and BM25 picks the same content; this isolates metadata
    # containment as the only differentiating signal.
    emb_a = _embedding(seed=11)
    emb_b = _embedding(seed=22)
    doc_a, _chunk_a = await _insert_doc_with_chunk(
        session_factory,
        record_bot_id=record_bot_id,
        metadata={"document_type": "price_list", "title": "Pricing A"},
        content="Pricing for plan A is detailed here.",
        embedding=emb_a,
    )
    doc_b, _chunk_b = await _insert_doc_with_chunk(
        session_factory,
        record_bot_id=record_bot_id,
        metadata={"document_type": "policy", "title": "Refund policy"},
        content="Pricing comes up here only as background to refunds.",
        embedding=emb_b,
    )

    try:
        store = PgVectorStore(session_factory=session_factory, dimension=1536)

        # Query embedding closer to A so unfiltered ranking puts A first.
        query_emb = _embedding(seed=11)
        query_text = "pricing"

        # 1. With metadata_filter → only the matching doc comes back.
        filtered = await store.hybrid_search(
            query_text=query_text,
            query_embedding=query_emb,
            record_bot_id=record_bot_id,
            top_k=10,
            metadata_filter={"document_type": "price_list"},
        )
        filtered_doc_ids = {r["document_id"] for r in filtered}
        assert filtered_doc_ids == {str(doc_a)}, (
            f"metadata filter should isolate doc_a, got {filtered_doc_ids}"
        )

        # 2. Without metadata_filter → both docs.
        unfiltered = await store.hybrid_search(
            query_text=query_text,
            query_embedding=query_emb,
            record_bot_id=record_bot_id,
            top_k=10,
        )
        unfiltered_doc_ids = {r["document_id"] for r in unfiltered}
        assert {str(doc_a), str(doc_b)} <= unfiltered_doc_ids, (
            f"no-filter should return both docs, got {unfiltered_doc_ids}"
        )

        # 3. Filter that matches nothing → empty result so production relax
        #    fallback can engage.
        empty = await store.hybrid_search(
            query_text=query_text,
            query_embedding=query_emb,
            record_bot_id=record_bot_id,
            top_k=10,
            metadata_filter={"document_type": "guide"},
        )
        assert empty == [], "unmatched filter must return [] (triggers relax)"

    finally:
        await _cleanup(session_factory, record_bot_id)


@pytest.mark.asyncio
async def test_backcompat_doc_without_extracted_metadata_still_visible(
    session_factory: Any,
) -> None:
    """Backward compat: a doc with empty ``metadata_json`` is invisible to a
    non-empty filter (correct semantics — relax fallback handles it) and
    fully visible when no filter is set."""
    record_bot_id = uuid.uuid4()
    await _cleanup(session_factory, record_bot_id)
    await _insert_bot(
        session_factory,
        record_bot_id=record_bot_id,
        record_tenant_id=uuid.UUID("00000000-0000-0000-0000-000000005102"),
    )

    emb = _embedding(seed=33)
    doc_id, _ = await _insert_doc_with_chunk(
        session_factory,
        record_bot_id=record_bot_id,
        metadata={},  # legacy doc — no extracted_metadata yet
        content="Legacy content with no metadata extracted.",
        embedding=emb,
    )
    try:
        store = PgVectorStore(session_factory=session_factory, dimension=1536)

        # No filter → doc shows up.
        no_filter = await store.hybrid_search(
            query_text="legacy",
            query_embedding=emb,
            record_bot_id=record_bot_id,
            top_k=5,
        )
        assert any(r["document_id"] == str(doc_id) for r in no_filter), (
            "legacy doc must be retrievable without filter"
        )

        # With filter → doc is hidden (correct — production relax fallback
        # re-runs the query without the filter when this returns 0).
        filtered = await store.hybrid_search(
            query_text="legacy",
            query_embedding=emb,
            record_bot_id=record_bot_id,
            top_k=5,
            metadata_filter={"document_type": "price_list"},
        )
        assert filtered == [], (
            "legacy doc lacking extracted_metadata must NOT match a non-empty filter"
        )
    finally:
        await _cleanup(session_factory, record_bot_id)
