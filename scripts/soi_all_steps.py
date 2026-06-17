"""SOI ALL STEPS — unified reusable debug flow (1 lệnh soi toàn pipeline).

Cho mỗi bot, dump CHI TIẾT từng step end-to-end vào 1 file markdown:

  INGEST (per document):
    raw file → format detect (.md/CSV/flat) → analyze_document profile
    → select_strategy (method chọn) → MỌI chunk đã cắt (index/type/chars/
    cut-quality/chunk_context/content) → embedding (dim/coverage/vector head)

  QUERY (per sample question):
    embed query → pgvector cosine topK (per-chunk score) → answer-in-topK?
    → all request_steps (timing/tokens/status) → LLM answer

Tái dùng: chạy lại bất cứ lúc nào để debug. Output: reports/debug_traces/
SOI_<bot>.md (người đọc).

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/soi_all_steps.py                 # 3 bot mặc định
    .venv/bin/python scripts/soi_all_steps.py test-spa-id     # 1 bot
    .venv/bin/python scripts/soi_all_steps.py test-spa-id --q "Laser Carbon giá?"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

from ragbot.shared.chunking import analyze_document, select_strategy
from ragbot.shared.constants import (
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
)

_FRAG = DEFAULT_CHUNK_ORPHAN_THRESHOLD
_OVER = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS * 2
_OUT = "reports/debug_traces"
_PROFILE_KEYS = (
    "total_headings", "table_count", "is_csv_format", "vn_hierarchical_markers",
    "mixed_content_score", "avg_text_length", "total_words",
)


def _fmt_detect(raw: str) -> str:
    head = raw[:2000]
    tags = []
    if any(l.lstrip().startswith("#") for l in raw.splitlines()[:40]):
        tags.append("ATX-heading")
    if raw.count("|") > 5:
        tags.append("pipe-table(markdown)")
    if "## Page" in raw:
        tags.append("PDF-page-marker")
    if "," in head and head.count(",") > head.count("|"):
        tags.append("CSV/comma(raw,NOT-md)")
    return ", ".join(tags) or "flat-text"


async def _ingest(conn, bot, L):
    docs = await conn.fetch(
        """SELECT d.id, d.document_name dn, d.mime_type mt, d.source_url su,
                  d.raw_content rc
           FROM documents d JOIN bots b ON b.id=d.record_bot_id
           WHERE b.bot_id=$1 ORDER BY d.document_name""", bot)
    L.append(f"\n# SOI ALL STEPS — bot `{bot}`\n\n## STEP 1-3 — INGEST (upload→.md→chunk→embed)\n")
    for d in docs:
        raw = d["rc"] or ""
        prof = analyze_document(raw)
        strat, conf = select_strategy(prof, text=raw)
        chunks = await conn.fetch(
            """SELECT chunk_index ci, chunk_type ct, chunk_chars cc, chunk_context ctx,
                      content, (embedding IS NOT NULL) emb, vector_dims(embedding) dim
               FROM document_chunks WHERE record_document_id=$1 ORDER BY chunk_index""",
            d["id"])
        sizes = [c["cc"] or 0 for c in chunks]
        frag = sum(1 for s in sizes if 0 < s < _FRAG)
        over = sum(1 for s in sizes if s > _OVER)
        emb_n = sum(1 for c in chunks if c["emb"])
        dim = next((c["dim"] for c in chunks if c["emb"]), None)
        src = "LOCAL(file)" if (d["su"] or "").startswith("local") else "HTTPS"
        L.append(f"\n### doc `{d['dn']}` · {d['mt']} · {src} · raw={len(raw)}c")
        L.append(f"- **raw→.md**: format=`{_fmt_detect(raw)}` · head: `{raw[:100].replace(chr(10),'|')}`")
        L.append(f"- **profile**: " + ", ".join(f"{k}={prof[k]}" for k in _PROFILE_KEYS if k in prof))
        L.append(f"- **strategy**: `{strat}` (conf {conf:.2f})")
        L.append(f"- **chunk**: n={len(chunks)} · ok={len(chunks)-frag-over} · "
                 f"fragment(<{_FRAG})={frag} · oversized(>{_OVER})={over}")
        L.append(f"- **embed**: {emb_n}/{len(chunks)} · dim={dim}")
        L.append(f"- **TẤT CẢ chunk đã cắt** (item-by-item, kiểm cắt đúng):")
        for c in chunks:
            q = "⚠️FRAG" if 0 < (c["cc"] or 0) < _FRAG else ("⚠️OVER" if (c["cc"] or 0) > _OVER else "ok")
            ctx = (c["ctx"] or "")[:40]
            body = (c["content"] or "").replace("\n", "|")[:90]
            L.append(f"    - #{c['ci']:<3} `{c['ct']}` {c['cc']}c [{q}] emb={'Y' if c['emb'] else 'N'} "
                     f"ctx='{ctx}' :: {body}")


async def _query(conn, bot, query, L):
    from ragbot.application.dto.ai_specs import EmbeddingSpec
    from ragbot.bootstrap import Container
    import uuid as _uuid
    botrow = await conn.fetchrow(
        "SELECT id, record_tenant_id FROM bots WHERE bot_id=$1 ORDER BY created_at LIMIT 1", bot)
    if botrow is None:
        return
    cont = Container(); emb = cont.embedder(); s = cont.settings()
    spec = EmbeddingSpec(binding_id=_uuid.uuid4(), model_name=s.embedding.model_name,
                         provider=getattr(s.embedding, "provider", "zeroentropy"),
                         dimension=s.embedding.dimension,
                         model_version=getattr(s.embedding, "model_version", "zembed-1"), task="query")
    L.append(f"\n## STEP 4-5 — QUERY (embed→vector→topK) · q=`{query}`\n")
    try:
        vec = await emb.embed_one(query, spec=spec, record_tenant_id=botrow["record_tenant_id"])
    finally:
        cl = getattr(emb, "close", None)
        if cl:
            await cl()
    L.append(f"- **query embed**: dim={len(vec)} head={[round(x,4) for x in vec[:6]]}")
    lit = "[" + ",".join(str(x) for x in vec) + "]"
    rows = await conn.fetch(
        """SELECT chunk_index ci, chunk_type ct, 1-(embedding <=> $1::vector) cos, content
           FROM document_chunks WHERE record_bot_id=$2 AND embedding IS NOT NULL
           ORDER BY embedding <=> $1::vector LIMIT 10""", lit, botrow["id"])
    L.append("- **pgvector cosine top-10** (zembed-1):")
    for i, r in enumerate(rows, 1):
        L.append(f"    #{i:<2} cos={r['cos']:.4f} idx={r['ci']} `{r['ct']}` :: "
                 f"{(r['content'] or '').replace(chr(10),'|')[:70]}")
    # latest request_steps for this bot (full pipeline timing)
    rid = await conn.fetchval(
        """SELECT rs.record_request_id FROM request_steps rs JOIN request_logs rl ON rl.request_id=rs.record_request_id
           JOIN bots b ON b.id=rl.record_bot_id WHERE b.bot_id=$1 AND rs.step_name='generate'
           ORDER BY rs.started_at DESC LIMIT 1""", bot)
    if rid:
        steps = await conn.fetch(
            """SELECT step_name sn, step_order so, duration_ms ms, input_tokens it, output_tokens ot, status st
               FROM request_steps WHERE record_request_id=$1 ORDER BY step_order""", rid)
        L.append(f"\n## STEP 6+ — QUERY PIPELINE ({len(steps)} step, request gần nhất)\n")
        tot = 0
        for r in steps:
            tot += r["ms"] or 0
            tok = f" tok={r['it']}/{r['ot']}" if r["it"] else ""
            L.append(f"    [{r['so']:>2}] {r['sn']:<22} {str(r['ms'])+'ms':>8} {r['st']}{tok}")
        L.append(f"    ── tổng {len(steps)} step = {tot}ms ──")


async def main(bots, query) -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    os.makedirs(_OUT, exist_ok=True)
    try:
        for bot in bots:
            L: list[str] = []
            await _ingest(conn, bot, L)
            if query:
                await _query(conn, bot, query, L)
            path = f"{_OUT}/SOI_{bot}.md"
            with open(path, "w") as fh:
                fh.write("\n".join(L) + "\n")
            print(f"wrote {path}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("bots", nargs="*", default=None)
    ap.add_argument("--q", default=None, help="sample query for STEP 4-5 vector trace")
    a = ap.parse_args()
    _bots = a.bots or ["test-spa-id", "chinh-sach-xe", "thong-tu-09-2020-tt-nhnn"]
    raise SystemExit(asyncio.run(main(_bots, a.q)))
