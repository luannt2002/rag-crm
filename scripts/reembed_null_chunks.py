#!/usr/bin/env python3
"""Re-embed legacy chunks where ``document_chunks.embedding`` is NULL.

DRY-RUN by default; pass ``--apply`` to write. Per-bot scope via
``--bot-uuid``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sql_text

from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.bootstrap import Container

logger = structlog.get_logger(__name__)


async def fetch_null_chunks(
    session_factory: Any,
    *,
    bot_uuid: str | None,
) -> list[dict[str, Any]]:
    """Return ``{id, content, record_bot_id, record_tenant_id}`` per NULL-embedding chunk."""
    sql = """
        SELECT dc.id,
               dc.content,
               d.record_bot_id,
               d.record_tenant_id
        FROM document_chunks dc
        JOIN documents d ON dc.record_document_id = d.id
        WHERE dc.embedding IS NULL
          AND d.deleted_at IS NULL
          AND dc.content IS NOT NULL
          AND length(dc.content) > 0
    """
    params: dict[str, Any] = {}
    if bot_uuid:
        sql += " AND d.record_bot_id = :bot_uuid"
        params["bot_uuid"] = bot_uuid
    sql += " ORDER BY d.record_bot_id, dc.created_at"
    async with session_factory() as session:
        rows = (await session.execute(sql_text(sql), params)).all()
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


def _group_by_bot(chunks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group chunks by ``record_bot_id`` for per-bot spec resolution."""
    out: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        out.setdefault(str(c["record_bot_id"]), []).append(c)
    return out


async def reembed_for_bot(
    *,
    session_factory: Any,
    embedder: Any,
    resolver: ModelResolverService,
    bot_chunks: list[dict[str, Any]],
    batch_size: int,
    apply: bool,
) -> tuple[int, int]:
    """Re-embed all chunks of one bot. Returns ``(success, failure)`` counts."""
    if not bot_chunks:
        return 0, 0
    record_bot_id = bot_chunks[0]["record_bot_id"]
    record_tenant_id = bot_chunks[0]["record_tenant_id"]

    spec = await resolver.resolve_embedding(
        record_bot_id=record_bot_id,
        record_tenant_id=record_tenant_id,
    )
    print(
        f"  bot={record_bot_id} model={spec.model_name} dim={spec.dimension} "
        f"chunks={len(bot_chunks)}"
    )

    if not apply:
        return 0, 0

    success = 0
    failures = 0
    for i in range(0, len(bot_chunks), batch_size):
        batch = bot_chunks[i : i + batch_size]
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
            logger.error(
                "batch_length_mismatch",
                expected=len(batch),
                got=len(vectors),
            )
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
        print(
            f"    batch {i // batch_size + 1}: cumulative success={success} "
            f"failures={failures}"
        )
    return success, failures


async def main() -> int:
    """CLI entry — re-embed all NULL chunks (optionally scoped to one bot)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--bot-uuid",
        default=None,
        help="Limit to single bot UUID; default = all bots with NULL chunks",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Chunks per embedder batch (default 32)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply writes; default DRY-RUN (count + per-bot spec print only)",
    )
    args = ap.parse_args()

    container = Container()
    session_factory = container.session_factory()
    embedder = container.embedder()
    resolver: ModelResolverService = container.model_resolver()

    chunks = await fetch_null_chunks(session_factory, bot_uuid=args.bot_uuid)
    print(f"Found {len(chunks)} NULL-embedding chunks")
    if not chunks:
        return 0

    grouped = _group_by_bot(chunks)
    print(f"Spans {len(grouped)} bot(s):")

    if not args.apply:
        for bot_id, bot_chunks in grouped.items():
            tenant_id = bot_chunks[0]["record_tenant_id"]
            try:
                spec = await resolver.resolve_embedding(
                    record_bot_id=bot_chunks[0]["record_bot_id"],
                    record_tenant_id=tenant_id,
                )
                print(
                    f"  bot={bot_id} chunks={len(bot_chunks)} "
                    f"model={spec.model_name} dim={spec.dimension}"
                )
            except (ValueError, RuntimeError, KeyError) as exc:
                print(
                    f"  bot={bot_id} chunks={len(bot_chunks)} "
                    f"SPEC_RESOLVE_FAILED: {type(exc).__name__}: {exc}"
                )
        print(
            f"DRY-RUN — pass --apply to write. Would re-embed {len(chunks)} chunks."
        )
        return 0

    t0 = time.time()
    total_success = 0
    total_failures = 0
    for bot_id, bot_chunks in grouped.items():
        print(f"Re-embedding bot {bot_id}:")
        try:
            s, f = await reembed_for_bot(
                session_factory=session_factory,
                embedder=embedder,
                resolver=resolver,
                bot_chunks=bot_chunks,
                batch_size=args.batch_size,
                apply=True,
            )
        except (ValueError, RuntimeError, KeyError) as exc:
            logger.error(
                "bot_reembed_aborted",
                record_bot_id=bot_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            print(
                f"  bot={bot_id} ABORTED: {type(exc).__name__}: {exc}"
            )
            total_failures += len(bot_chunks)
            continue
        total_success += s
        total_failures += f

    elapsed = time.time() - t0
    print(
        f"DONE in {elapsed:.1f}s: {total_success} chunks re-embedded, "
        f"{total_failures} failures"
    )
    return 0 if total_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
