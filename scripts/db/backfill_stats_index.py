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
import json
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
               JOIN document_chunks dc ON dc.record_document_id = d.id"""
        ))).fetchall()

    total_docs = total_entities = 0
    for doc_id, bot_id, tenant_id, workspace_id in docs:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                # ALL chunk types (not just 'table'): production extracts stats
                # from ctx.rows = every chunk of the doc; a priced catalog row can
                # land in a 'text'-typed chunk. parse_table_chunks self-guards on
                # delimiters, so prose chunks are skipped harmlessly.
                """SELECT dc.id, dc.chunk_index, dc.content, dc.chunk_type,
                          dc.metadata_json
                   FROM document_chunks dc
                   WHERE dc.record_document_id = :d
                   ORDER BY dc.chunk_index"""
            ), {"d": doc_id})).fetchall()
        # Prefer the RAW pre-enrichment row text (metadata_json.raw_chunk) — the
        # persisted ``content`` carries an enrichment prose prefix ("Đoạn trong
        # phần Kho lốp …") that parse_table_chunks would otherwise mis-extract as
        # noise AND splits the real table rows wrong (mirrors the production
        # extraction path in ingest_stages_final._raw_row).
        def _raw_of(meta: object) -> str | None:
            if isinstance(meta, dict):
                return meta.get("raw_chunk")
            if isinstance(meta, str):
                try:
                    return json.loads(meta).get("raw_chunk")
                except (ValueError, AttributeError):
                    return None
            return None

        chunk_dicts = []
        for r in rows:
            _d = {"id": str(r[0]), "chunk_index": r[1], "content": r[2], "chunk_type": r[3]}
            _raw = _raw_of(r[4])
            if _raw:
                _d["raw_chunk"] = _raw
            chunk_dicts.append(_d)
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
