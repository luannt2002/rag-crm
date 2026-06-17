"""Phase E1 — ingest-side step trace: raw → (.md) → strategy → chunks → quality.

For each document of a bot, show the full ingest-side decision trail using
REAL data:
  raw_content (head)
    → analyze_document() profile
    → select_strategy() (strategy + confidence)   [re-run, deterministic]
    → ACTUAL stored chunks from document_chunks (count, type, chars, context)
    → quality flags (fragment / oversized / ok)

No LLM, no server — pure DB read + deterministic chunker analysis. Answers
"tài liệu này dùng phương pháp nào, cắt bao nhiêu chunk, bao nhiêu chunk chuẩn".

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_ingest_trace.py <bot_id_substr> [bot_id_substr ...]
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from ragbot.shared.chunking import analyze_document, select_strategy
from ragbot.shared.constants import (
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
)

_FRAG_MIN = DEFAULT_CHUNK_ORPHAN_THRESHOLD          # < this = fragment suspect
_OVERSIZE = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS * 2   # > this = oversized suspect


def _profile_view(p: dict) -> str:
    keys = (
        "total_headings", "table_count", "is_csv_format",
        "vn_hierarchical_markers", "mixed_content_score", "avg_text_length",
        "total_words",
    )
    return ", ".join(f"{k}={p[k]}" for k in keys if k in p)


async def _trace_bot(conn: asyncpg.Connection, bot_substr: str) -> None:
    docs = await conn.fetch(
        """
        SELECT d.id, d.document_name, d.mime_type, d.content_chars, d.raw_content,
               b.bot_id
        FROM documents d JOIN bots b ON b.id = d.record_bot_id
        WHERE b.bot_id LIKE $1
        ORDER BY b.bot_id, d.document_name
        """,
        f"%{bot_substr}%",
    )
    if not docs:
        print(f"!! no documents for bot LIKE %{bot_substr}%")
        return

    for d in docs:
        raw = d["raw_content"] or ""
        print("=" * 90)
        print(
            f"BOT {d['bot_id']} · doc {d['document_name']} · mime={d['mime_type']} "
            f"· raw={len(raw)}c"
        )
        # Step 1 — profile + strategy (deterministic re-run on raw).
        prof = analyze_document(raw)
        strat, conf = select_strategy(prof, text=raw)
        print(f"  [profile ] {_profile_view(prof)}")
        print(f"  [strategy] {strat}  (confidence={conf:.2f})")
        print(f"  [raw head] {raw[:120].replace(chr(10), '|')}")

        # Step 2 — ACTUAL stored chunks.
        chunks = await conn.fetch(
            """
            SELECT chunk_index, chunk_type, chunk_chars, chunk_context, content
            FROM document_chunks
            WHERE record_document_id = $1
            ORDER BY chunk_index
            """,
            d["id"],
        )

        # Step 1b — embedding/vector status for this doc's chunks.
        emb = await conn.fetchrow(
            """
            SELECT count(*) AS tot,
                   count(embedding) AS embedded,
                   min(vector_dims(embedding)) AS dim
            FROM document_chunks WHERE record_document_id = $1
            """,
            d["id"],
        )
        head = await conn.fetchval(
            "SELECT embedding::text FROM document_chunks "
            "WHERE record_document_id = $1 AND embedding IS NOT NULL LIMIT 1",
            d["id"],
        )
        head_s = (head[:70] + "…") if head else "NULL — NOT EMBEDDED!"
        print(
            f"  [embed   ] {emb['embedded']}/{emb['tot']} embedded · dim={emb['dim']} "
            f"· vec={head_s}"
        )
        n = len(chunks)
        sizes = [c["chunk_chars"] or len(c["content"] or "") for c in chunks]
        frag = sum(1 for s in sizes if 0 < s < _FRAG_MIN)
        over = sum(1 for s in sizes if s > _OVERSIZE)
        ok = n - frag - over
        types: dict[str, int] = {}
        for c in chunks:
            types[c["chunk_type"] or "?"] = types.get(c["chunk_type"] or "?", 0) + 1
        avg = round(sum(sizes) / n, 1) if n else 0
        print(
            f"  [chunks  ] n={n} avg={avg}c  ok={ok} fragment(<{_FRAG_MIN})={frag} "
            f"oversized(>{_OVERSIZE})={over}  types={types}"
        )
        for c in chunks[:4]:
            ctx = (c["chunk_context"] or "")[:46]
            body = (c["content"] or "").replace("\n", "|")[:80]
            print(
                f"    #{c['chunk_index']:<3} {c['chunk_type'] or '?':<10} "
                f"{(c['chunk_chars'] or 0):>4}c ctx='{ctx}' :: {body}"
            )
        if n > 4:
            print(f"    … (+{n - 4} more)")


async def main(substrs: list[str]) -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        for s in substrs:
            await _trace_bot(conn, s)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or ["spa", "thong-tu", "chinh-sach-xe"]
    sys.exit(asyncio.run(main(args)))
