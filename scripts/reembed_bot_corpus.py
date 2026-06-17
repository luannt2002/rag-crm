#!/usr/bin/env python3
"""Re-embed every chunk for one bot using its current per-bot resolver spec.

Use case: bot binding switched embedding provider; existing chunks remain
in the previous vector space, so the query path raises ``different vector
dimensions`` at runtime. This script realigns the corpus to the active
binding.

Steps per chunk:
1. Resolve EmbeddingSpec via ModelResolverService (per-bot binding).
2. Re-embed with passage task.
3. ``UPDATE document_chunks SET embedding = CAST(:emb AS vector)``.

DRY-RUN by default. Pass ``--apply`` to write.

Examples:
    .venv/bin/python scripts/reembed_bot_corpus.py \\
        --bot-uuid 4d741129-e1ed-4224-be35-675ee7d16e1e --apply
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sql_text

from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.bootstrap import Container
from ragbot.shared.constants import DEFAULT_EMBEDDING_TASK_PASSAGE

logger = structlog.get_logger(__name__)


async def fetch_bot_chunks(
    session_factory: Any,
    *,
    bot_uuid: str,
) -> list[dict[str, Any]]:
    """Return all live chunks for one bot."""
    sql = """
        SELECT dc.id,
               dc.content,
               d.record_bot_id,
               d.record_tenant_id
        FROM document_chunks dc
        JOIN documents d ON dc.record_document_id = d.id
        WHERE d.record_bot_id = :bot_uuid
          AND d.deleted_at IS NULL
          AND dc.content IS NOT NULL
          AND length(dc.content) > 0
        ORDER BY dc.created_at
    """
    async with session_factory() as session:
        rows = (await session.execute(sql_text(sql), {"bot_uuid": bot_uuid})).all()
        return [
            {
                "id": r.id,
                "content": r.content,
                "record_bot_id": r.record_bot_id,
                "record_tenant_id": r.record_tenant_id,
            }
            for r in rows
        ]


async def update_embedding(
    session_factory: Any,
    *,
    chunk_id: UUID,
    embedding: list[float],
) -> None:
    """Write one vector via ``CAST(:emb AS vector)`` in its own transaction."""
    async with session_factory() as session:
        await session.execute(
            sql_text(
                "UPDATE document_chunks "
                "SET embedding = CAST(:emb AS vector) "
                "WHERE id = :id"
            ),
            {"id": chunk_id, "emb": str(embedding)},
        )
        await session.commit()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bot-uuid", required=True, help="UUID of bot whose corpus to re-embed")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--apply", action="store_true", help="Apply writes; default DRY-RUN")
    args = ap.parse_args()

    container = Container()
    session_factory = container.session_factory()
    embedder = container.embedder()
    resolver: ModelResolverService = container.model_resolver()

    chunks = await fetch_bot_chunks(session_factory, bot_uuid=args.bot_uuid)
    print(f"Found {len(chunks)} live chunks for bot {args.bot_uuid}")
    if not chunks:
        return 0

    record_tenant_id = chunks[0]["record_tenant_id"]
    record_bot_id = chunks[0]["record_bot_id"]

    spec = await resolver.resolve_embedding(
        record_bot_id, record_tenant_id=record_tenant_id,
    )
    # Force passage head — asymmetric embedding models require it at ingest.
    if spec.task != DEFAULT_EMBEDDING_TASK_PASSAGE:
        spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_PASSAGE})

    print(f"Resolved spec: model={spec.model_name} dim={spec.dimension} task={spec.task}")
    if not args.apply:
        print("DRY-RUN — pass --apply to write")
        return 0

    success = 0
    failures = 0
    for i in range(0, len(chunks), args.batch_size):
        batch = chunks[i : i + args.batch_size]
        texts = [c["content"] for c in batch]
        try:
            vectors = await embedder.embed_batch(
                texts,
                spec=spec,
                record_tenant_id=record_tenant_id,
            )
        except (OSError, ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
            logger.error(
                "batch_embed_failed",
                batch_size=len(batch),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            failures += len(batch)
            continue
        if len(vectors) != len(batch):
            logger.error("batch_length_mismatch", expected=len(batch), got=len(vectors))
            failures += len(batch)
            continue
        for chunk, vec in zip(batch, vectors, strict=True):
            try:
                await update_embedding(
                    session_factory,
                    chunk_id=chunk["id"],
                    embedding=vec,
                )
                success += 1
            except (OSError, ValueError, RuntimeError) as exc:
                logger.error(
                    "update_failed",
                    chunk_id=str(chunk["id"]),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                failures += 1
        print(f"  batch {i // args.batch_size + 1}: success={success} failures={failures}")

    print(f"DONE — success={success} failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
