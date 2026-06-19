#!/usr/bin/env python3
"""R1 fix — backfill embeddings for parent chunks that have content but no vector.

Small-to-big (parent-child) chunking embeds the small children and leaves the
larger PARENT blocks unembedded (retrieved only via child->parent expansion).
For structural corpora (e.g. legal articles) a parent can carry the article
BODY while its child carries only the heading — so the body is invisible to
dense search and the bot wrongly refuses (the Dieu 56 coverage miss).

This backfills passage embeddings for those parent chunks using the SAME params
the app's JinaEmbedder uses (model jina-embeddings-v3, task retrieval.passage,
1024-dim) so the new vectors live in the SAME space as the children + queries.
The parents already carry a BM25 search_vector; this adds the dense half.

Usage: python scripts/db/backfill_parent_embeddings.py [bot_id]
  bot_id optional — default backfills ALL bots' null-embedding parents.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
import httpx

API_URL = "https://api.jina.ai/v1/embeddings"
MODEL = "jina-embeddings-v3"
TASK = "retrieval.passage"
DIM = 1024
BATCH = 32
KEY = os.environ.get("EMBEDDING_JINA_API_KEY") or os.environ.get("JINA_API_KEY") or ""


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_URL_SYNC") or ""
    # asyncpg wants a plain postgres:// DSN (strip the +driver suffix).
    return raw.replace("+asyncpg", "").replace("+psycopg2", "")


async def _embed(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    r = await client.post(
        API_URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "input": texts, "task": TASK, "dimensions": DIM, "truncate": True},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return [row["embedding"] for row in data]


async def main() -> int:
    if not KEY:
        print("ERROR: JINA_API_KEY / EMBEDDING_JINA_API_KEY not set", file=sys.stderr)
        return 1
    bot_filter = sys.argv[1] if len(sys.argv) > 1 else None
    conn = await asyncpg.connect(_dsn())
    try:
        where = "dc.parent_chunk_id IS NULL AND dc.embedding IS NULL AND dc.content IS NOT NULL"
        params: list = []
        if bot_filter:
            where += " AND b.bot_id = $1"
            params.append(bot_filter)
        rows = await conn.fetch(
            f"""
            SELECT dc.id, dc.content
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.record_document_id
            JOIN bots b ON b.id = d.record_bot_id
            WHERE {where}
            ORDER BY dc.id
            """,
            *params,
        )
        print(f"null-embedding parent chunks to backfill: {len(rows)}"
              f"{f' (bot={bot_filter})' if bot_filter else ' (all bots)'}")
        if not rows:
            return 0
        done = 0
        async with httpx.AsyncClient() as client:
            for i in range(0, len(rows), BATCH):
                batch = rows[i : i + BATCH]
                vecs = await _embed(client, [r["content"] for r in batch])
                async with conn.transaction():
                    for row, vec in zip(batch, vecs, strict=True):
                        vec_str = "[" + ",".join(repr(float(x)) for x in vec) + "]"
                        await conn.execute(
                            "UPDATE document_chunks SET embedding = $1::vector WHERE id = $2",
                            vec_str, row["id"],
                        )
                done += len(batch)
                print(f"  embedded {done}/{len(rows)}")
        print(f"DONE — backfilled {done} parent embeddings.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
