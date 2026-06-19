"""Backfill ``document_service_index`` from existing table chunks.

The stats index is normally populated at ingest time, but if the table was
created (or a bot ingested) before the stats path was active, the index sits
empty and catalog/price structured routes (``query_by_name_keyword`` /
price-of-entity) fall back to vector. This reparses every document's table
chunks with the deterministic ``parse_table_chunks`` extractor and writes the
entities — idempotent (delete-by-document before insert).

    set -a && source .env && set +a && python scripts/db/backfill_stats_index.py
"""
from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository
from ragbot.shared.document_stats import parse_table_chunks


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = async_sessionmaker(eng, expire_on_commit=False)
    repo = StatsIndexRepository(session_factory=session_factory)

    async with eng.connect() as conn:
        docs = (await conn.execute(text(
            """SELECT DISTINCT d.id, d.record_bot_id, d.record_tenant_id, b.workspace_id
               FROM documents d JOIN bots b ON b.id = d.record_bot_id
               JOIN document_chunks dc ON dc.record_document_id = d.id
               WHERE dc.chunk_type = 'table'"""
        ))).fetchall()

    total_docs = total_entities = 0
    for doc_id, bot_id, tenant_id, workspace_id in docs:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                """SELECT dc.id, dc.chunk_index, dc.content, dc.chunk_type
                   FROM document_chunks dc
                   WHERE dc.record_document_id = :d AND dc.chunk_type = 'table'
                   ORDER BY dc.chunk_index"""
            ), {"d": doc_id})).fetchall()
        chunk_dicts = [
            {"id": str(r[0]), "chunk_index": r[1], "content": r[2], "chunk_type": r[3]}
            for r in rows
        ]
        entities = parse_table_chunks(chunk_dicts)
        if not entities:
            continue
        await repo.delete_by_document(uuid.UUID(str(doc_id)))
        await repo.bulk_insert(
            record_tenant_id=uuid.UUID(str(tenant_id)),
            workspace_id=workspace_id or str(tenant_id),
            record_bot_id=uuid.UUID(str(bot_id)),
            record_document_id=uuid.UUID(str(doc_id)),
            entities=entities,
        )
        total_docs += 1
        total_entities += len(entities)
        print(f"  doc {doc_id}: +{len(entities)} entities")

    await eng.dispose()
    print(f"DONE — {total_entities} entities across {total_docs} documents")


if __name__ == "__main__":
    asyncio.run(main())
