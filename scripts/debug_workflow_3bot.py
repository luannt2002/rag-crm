"""Phase E3 — orchestrate ingest + vector trace across bots, dump 1 file/bot.

Reads scenario files (tests/scenarios/<bot>_scenario.json), then per bot:
  * INGEST audit: per-doc strategy / chunk count / quality / embedding +
    flags (0-chunk docs, fragments, oversized).
  * VECTOR trace per question: embed query (one shared embedder) → pgvector
    cosine top-K → record cosine scores + whether the ``expect`` substring is
    inside top-5 / top-K / absent (recall-miss).

Writes reports/debug_traces/DEBUG_<bot>.json (machine) + .md (human). No
scoring LLM — pure data dump for Claude/agent to debug.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_workflow_3bot.py [topk]
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import uuid as _uuid

import asyncpg

from ragbot.shared.chunking import analyze_document, select_strategy
from ragbot.shared.constants import (
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
)

_FRAG = DEFAULT_CHUNK_ORPHAN_THRESHOLD
_OVER = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS * 2
_OUT = "reports/debug_traces"


async def _make_embedder():
    from ragbot.application.dto.ai_specs import EmbeddingSpec
    from ragbot.bootstrap import Container

    container = Container()
    embedder = container.embedder()
    s = container.settings()
    spec = EmbeddingSpec(
        binding_id=_uuid.uuid4(),
        model_name=s.embedding.model_name,
        provider=getattr(s.embedding, "provider", "zeroentropy"),
        dimension=s.embedding.dimension,
        model_version=getattr(s.embedding, "model_version", "zembed-1"),
        task="query",
    )
    return embedder, spec


async def _ingest_audit(conn, bot_id) -> list[dict]:
    docs = await conn.fetch(
        """
        SELECT d.id, d.document_name, d.mime_type, d.content_chars, d.raw_content,
               d.chunks_processed
        FROM documents d WHERE d.record_bot_id = $1 ORDER BY d.created_at
        """,
        bot_id,
    )
    out = []
    for d in docs:
        raw = d["raw_content"] or ""
        prof = analyze_document(raw)
        strat, conf = select_strategy(prof, text=raw)
        chunks = await conn.fetch(
            "SELECT chunk_chars, chunk_type FROM document_chunks "
            "WHERE record_document_id = $1",
            d["id"],
        )
        sizes = [c["chunk_chars"] or 0 for c in chunks]
        n = len(chunks)
        flags = []
        if n == 0 and (d["chunks_processed"] or 0) > 0:
            flags.append(f"STALE_COUNTER chunks_processed={d['chunks_processed']} but 0 stored")
        if n == 0 and len(raw) > 200:
            flags.append("ZERO_CHUNKS despite content")
        frag = sum(1 for s in sizes if 0 < s < _FRAG)
        over = sum(1 for s in sizes if s > _OVER)
        if frag:
            flags.append(f"fragments={frag}")
        if over:
            flags.append(f"oversized={over}")
        if strat == "table_csv" and n > 15:
            flags.append(f"ROW_AS_CHUNK n={n} (aggregation recall risk)")
        out.append({
            "doc": d["document_name"], "mime": d["mime_type"],
            "raw_chars": len(raw), "strategy": strat, "confidence": round(conf, 2),
            "n_chunks": n, "avg_chars": round(sum(sizes) / n, 1) if n else 0,
            "chunks_processed_counter": d["chunks_processed"],
            "head": raw[:80].replace("\n", "|"), "flags": flags,
        })
    return out


async def _vector_trace(conn, embedder, spec, bot_id, tenant_id, q, expect, topk) -> dict:
    vec = await embedder.embed_one(q["q"], spec=spec, record_tenant_id=tenant_id)
    lit = "[" + ",".join(str(x) for x in vec) + "]"
    rows = await conn.fetch(
        """
        SELECT chunk_index, chunk_type,
               1 - (embedding <=> $1::vector) AS cosine, content
        FROM document_chunks
        WHERE record_bot_id = $2 AND embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector LIMIT $3
        """,
        lit, bot_id, topk,
    )
    top = [
        {"rank": i + 1, "cosine": round(r["cosine"], 4), "idx": r["chunk_index"],
         "type": r["chunk_type"], "head": (r["content"] or "").replace("\n", "|")[:90]}
        for i, r in enumerate(rows)
    ]
    rank = None
    if expect:
        for r in top:
            if expect.lower() in (r["head"] or "").lower():
                rank = r["rank"]
                break
        # deeper check on full content for the expect
        if rank is None:
            hit = await conn.fetchval(
                """
                SELECT min(rank) FROM (
                  SELECT row_number() OVER (ORDER BY embedding <=> $1::vector) AS rank,
                         content
                  FROM document_chunks
                  WHERE record_bot_id = $2 AND embedding IS NOT NULL
                ) s WHERE s.content ILIKE '%' || $3 || '%'
                """,
                lit, bot_id, expect,
            )
            rank = int(hit) if hit is not None else None
    verdict = "open"
    if expect:
        if rank is None:
            verdict = "RECALL_MISS_ABSENT"
        elif rank <= 5:
            verdict = "in_top5"
        elif rank <= topk:
            verdict = f"OUTSIDE_top5_rank{rank}"
        else:
            verdict = f"OUTSIDE_topK_rank{rank}"
    return {
        "id": q["id"], "flow": q["flow"], "q": q["q"], "expect": expect,
        "expect_rank": rank, "verdict": verdict,
        "top_cosine": top[0]["cosine"] if top else None, "top": top,
    }


async def main(topk: int) -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    embedder, spec = await _make_embedder()
    os.makedirs(_OUT, exist_ok=True)
    master_issues: list[str] = []
    try:
        for path in sorted(glob.glob("tests/scenarios/*_scenario.json")):
            sc = json.load(open(path))
            bot = sc["bot_id"]
            botrow = await conn.fetchrow(
                "SELECT id, record_tenant_id FROM bots WHERE bot_id = $1 "
                "ORDER BY created_at LIMIT 1",
                bot,
            )
            if botrow is None:
                master_issues.append(f"{bot}: BOT NOT FOUND")
                continue
            print(f"== {bot} ==")
            ingest = await _ingest_audit(conn, botrow["id"])
            for d in ingest:
                for f in d["flags"]:
                    master_issues.append(f"{bot} · doc {d['doc']}: {f}")
            qres = []
            for q in sc["questions"]:
                r = await _vector_trace(
                    conn, embedder, spec, botrow["id"],
                    botrow["record_tenant_id"], q, q.get("expect"), topk,
                )
                qres.append(r)
                if "RECALL_MISS" in r["verdict"] or "OUTSIDE" in r["verdict"]:
                    master_issues.append(
                        f"{bot} · {q['id']} ({q['flow']}): {r['verdict']} — {q['q'][:50]}"
                    )
                print(f"  {q['id']} {r['verdict']:<22} topcos={r['top_cosine']}")
            out = {"bot": bot, "topk": topk, "ingest": ingest, "questions": qres}
            with open(f"{_OUT}/DEBUG_{bot}.json", "w") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)
        with open(f"{_OUT}/MASTER_ISSUES.md", "w") as fh:
            fh.write("# Debug workflow — master issue list\n\n")
            for i in master_issues:
                fh.write(f"- {i}\n")
        print(f"\n=== {len(master_issues)} issues → {_OUT}/MASTER_ISSUES.md ===")
    finally:
        close = getattr(embedder, "close", None)
        if close is not None:
            await close()
        await conn.close()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)))
