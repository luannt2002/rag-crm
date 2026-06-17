"""Phase E2 — query-side step trace: embed → vector → topK → (all steps timing).

Two independent dumps, both REAL data:

  (A) VECTOR TRACE (deterministic re-run, no server): embed the query with the
      live embedder (zembed-1 1280-dim), run pgvector cosine over the bot's
      chunks, print top-N by cosine — so you can see the query vector, the
      per-chunk cosine score, and whether the expected answer sits OUTSIDE the
      top-K (the recall-miss check).

  (B) STEP TIMING TRACE (from request_steps): dump every pipeline step of a
      real request (retrieve → rrf → rerank → filter → mmr → prompt_build →
      generate → grounding) with duration_ms + tokens + per-step metadata, so
      you see which step costs how long, start→end.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_query_trace.py \
        --bot test-spa-id --q "spa có những dịch vụ gì" [--expect "Laser Carbon"] [--topk 20]
    # timing of the latest real request for a bot:
    .venv/bin/python scripts/debug_query_trace.py --bot test-spa-id --steps-latest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg


# ---------------------------------------------------------------------------
# (A) Vector trace — embed query + pgvector cosine
# ---------------------------------------------------------------------------
async def _embed_query(text: str, record_tenant_id) -> list[float]:
    """Embed ``text`` with the production-wired embedder (real keys from env)."""
    import uuid as _uuid

    from ragbot.application.dto.ai_specs import EmbeddingSpec
    from ragbot.bootstrap import Container

    container = Container()
    embedder = container.embedder()
    settings = container.settings()
    spec = EmbeddingSpec(
        binding_id=_uuid.uuid4(),
        model_name=settings.embedding.model_name,
        provider=getattr(settings.embedding, "provider", "zeroentropy"),
        dimension=settings.embedding.dimension,
        model_version=getattr(settings.embedding, "model_version", "zembed-1"),
        task="query",
    )
    try:
        vec = await embedder.embed_one(
            text, spec=spec, record_tenant_id=record_tenant_id,
        )
    finally:
        close = getattr(embedder, "close", None)
        if close is not None:
            await close()
    return list(vec)


async def _vector_trace(
    conn: asyncpg.Connection, bot: str, query: str, expect: str | None, topk: int
) -> None:
    botrow = await conn.fetchrow(
        "SELECT id, record_tenant_id FROM bots WHERE bot_id = $1 "
        "ORDER BY created_at LIMIT 1",
        bot,
    )
    if botrow is None:
        print(f"  !! bot {bot!r} not found")
        return
    bot_id = botrow["id"]

    qvec = await _embed_query(query, botrow["record_tenant_id"])
    print("=" * 92)
    print(f"VECTOR TRACE · bot={bot} · q={query!r}")
    print(f"  [query embed] dim={len(qvec)} head={[round(x, 5) for x in qvec[:6]]}…")

    vec_literal = "[" + ",".join(str(x) for x in qvec) + "]"
    rows = await conn.fetch(
        """
        SELECT dc.chunk_index, dc.chunk_type, dc.chunk_chars,
               1 - (dc.embedding <=> $1::vector) AS cosine,
               dc.chunk_context, dc.content
        FROM document_chunks dc
        WHERE dc.record_bot_id = $2 AND dc.embedding IS NOT NULL
        ORDER BY dc.embedding <=> $1::vector
        LIMIT $3
        """,
        vec_literal, bot_id, topk,
    )
    print(f"  [pgvector cosine top-{topk}] (zembed-1, cosine = 1 - dist)")
    expect_rank = None
    for rank, r in enumerate(rows, 1):
        body = (r["content"] or "").replace("\n", "|")
        hit = ""
        if expect and expect.lower() in (r["content"] or "").lower():
            hit = "  <<< EXPECTED"
            if expect_rank is None:
                expect_rank = rank
        print(
            f"    #{rank:<2} cos={r['cosine']:.4f} idx={r['chunk_index']:<3} "
            f"{r['chunk_type'] or '?':<10} :: {body[:74]}{hit}"
        )
    if expect:
        if expect_rank is None:
            print(f"  >> EXPECTED {expect!r} NOT in top-{topk} (recall miss in vector stage)")
        else:
            in5 = "INSIDE top-5" if expect_rank <= 5 else "OUTSIDE top-5 (cap risk!)"
            print(f"  >> EXPECTED {expect!r} at rank {expect_rank} — {in5}")


# ---------------------------------------------------------------------------
# (B) Step timing trace — from request_steps
# ---------------------------------------------------------------------------
async def _steps_trace(conn: asyncpg.Connection, bot: str, rid: str | None) -> None:
    if rid is None:
        rid = await conn.fetchval(
            """
            SELECT rs.record_request_id
            FROM request_steps rs
            JOIN request_logs rl ON rl.request_id = rs.record_request_id
            JOIN bots b ON b.id = rl.record_bot_id
            WHERE b.bot_id = $1 AND rs.step_name = 'generate'
            ORDER BY rs.started_at DESC LIMIT 1
            """,
            bot,
        )
    if rid is None:
        print(f"  !! no request with steps found for bot {bot!r}")
        return
    print("=" * 92)
    print(f"STEP TIMING TRACE · bot={bot} · request={rid}")
    rows = await conn.fetch(
        """
        SELECT step_name, step_order, duration_ms, input_tokens, output_tokens,
               cost_usd, status, metadata_json
        FROM request_steps WHERE record_request_id = $1 ORDER BY step_order
        """,
        rid,
    )
    total = 0
    for r in rows:
        total += r["duration_ms"] or 0
        md = r["metadata_json"]
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (ValueError, TypeError):
                md = {}
        # show the decision-bearing metadata keys (drop step_kind noise)
        slim = {k: v for k, v in (md or {}).items() if k != "step_kind"} if isinstance(md, dict) else {}
        slim_s = json.dumps(slim, ensure_ascii=False)
        if len(slim_s) > 110:
            slim_s = slim_s[:110] + "…"
        tok = ""
        if r["input_tokens"] or r["output_tokens"]:
            tok = f" tok={r['input_tokens']}/{r['output_tokens']}"
        print(
            f"  [{r['step_order']:>2}] {r['step_name']:<22} {str(r['duration_ms'])+'ms':>8} "
            f"{r['status']:<7}{tok}  {slim_s}"
        )
    print(f"  ── total instrumented step time: {total}ms ──")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True)
    ap.add_argument("--q", default=None, help="query for vector trace")
    ap.add_argument("--expect", default=None, help="expected answer substring")
    ap.add_argument("--topk", type=int, default=20)
    ap.add_argument("--request-id", default=None)
    ap.add_argument("--steps-latest", action="store_true")
    args = ap.parse_args()

    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        if args.q:
            await _vector_trace(conn, args.bot, args.q, args.expect, args.topk)
        if args.request_id or args.steps_latest:
            await _steps_trace(conn, args.bot, args.request_id)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
