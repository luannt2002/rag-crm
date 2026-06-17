"""Log the full INGEST flow for a bot after upload → 1 markdown file per bot.

Captures, per document, the exact pipeline the user asked to trace:
  raw file → (format/convert to .md) → chunk (strategy/count/type/quality)
  → embed (dim/coverage/vector).

Run AFTER uploading docs to a bot (worker finished chunk+embed):
    set -a && source .env && set +a
    .venv/bin/python scripts/log_upload_flow.py <bot_id>

Writes reports/debug_traces/UPLOAD_FLOW_<bot>.md (overwrite). The query-side
flow (retrieve→rerank→topK→LLM) is logged separately by debug_query_trace.py.
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

_FRAG = DEFAULT_CHUNK_ORPHAN_THRESHOLD
_OVER = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS * 2
_OUT = "reports/debug_traces"


def _looks_markdown(raw: str) -> str:
    has_h = any(ln.lstrip().startswith("#") for ln in raw.splitlines()[:50])
    has_pipe = "|" in raw and raw.count("|") > 5
    has_page = "## Page" in raw
    has_csv = "," in raw and raw.count(",") > raw.count("|")
    tags = []
    if has_h:
        tags.append("ATX-heading")
    if has_pipe:
        tags.append("pipe-table")
    if has_page:
        tags.append("page-marker(PDF)")
    if has_csv and not has_pipe:
        tags.append("CSV/comma(raw, NOT markdown)")
    return ", ".join(tags) or "flat-text"


async def main(bot: str) -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    os.makedirs(_OUT, exist_ok=True)
    lines: list[str] = [f"# Upload flow trace — bot `{bot}`\n"]
    docs = await conn.fetch(
        """
        SELECT d.id, d.document_name, d.mime_type, d.source_url, d.raw_content,
               d.current_step, d.progress_percent
        FROM documents d JOIN bots b ON b.id = d.record_bot_id
        WHERE b.bot_id = $1 ORDER BY d.created_at
        """,
        bot,
    )
    if not docs:
        lines.append("\n_No documents yet — upload first, then re-run._\n")
    for d in docs:
        raw = d["raw_content"] or ""
        src = "LOCAL(file)" if (d["source_url"] or "").startswith("local") else "HTTPS(link)"
        prof = analyze_document(raw)
        strat, conf = select_strategy(prof, text=raw)
        chunks = await conn.fetch(
            """
            SELECT chunk_index, chunk_type, chunk_chars, chunk_context, content,
                   (embedding IS NOT NULL) AS emb, vector_dims(embedding) AS dim
            FROM document_chunks WHERE record_document_id = $1 ORDER BY chunk_index
            """,
            d["id"],
        )
        n = len(chunks)
        sizes = [c["chunk_chars"] or 0 for c in chunks]
        frag = sum(1 for s in sizes if 0 < s < _FRAG)
        over = sum(1 for s in sizes if s > _OVER)
        types: dict[str, int] = {}
        for c in chunks:
            types[c["chunk_type"] or "?"] = types.get(c["chunk_type"] or "?", 0) + 1
        emb_n = sum(1 for c in chunks if c["emb"])
        dim = next((c["dim"] for c in chunks if c["emb"]), None)
        whole = sum(1 for c in chunks if (c["chunk_chars"] or 0) > 800)

        lines.append(f"\n## doc `{d['document_name']}` · {d['mime_type']} · {src}\n")
        lines.append(f"- **Step1 raw→.md**: raw={len(raw)} chars · format detected = "
                     f"`{_looks_markdown(raw)}`")
        lines.append(f"  - raw head: `{raw[:120].replace(chr(10), '|')}`")
        lines.append(f"- **Step2 chunk**: strategy=`{strat}` (conf {conf:.2f}) · "
                     f"n={n} · avg={round(sum(sizes)/n,1) if n else 0}c · types={types}")
        lines.append(f"  - quality: ok={n-frag-over} · fragment(<{_FRAG})={frag} · "
                     f"oversized(>{_OVER})={over} · whole-table/big(>800c)={whole}")
        lines.append(f"- **Step3 embed**: {emb_n}/{n} embedded · dim={dim}")
        if d["progress_percent"] is not None and n == 0:
            lines.append(f"  - ⚠️ prog={d['progress_percent']}% but 0 chunks — "
                         f"INGEST FAIL (check source_url fetch)")
        for c in chunks[:4]:
            ctx = (c["chunk_context"] or "")[:40]
            body = (c["content"] or "").replace("\n", "|")[:80]
            lines.append(f"    - #{c['chunk_index']} `{c['chunk_type']}` "
                         f"{c['chunk_chars']}c ctx='{ctx}' :: {body}")
        if n > 4:
            lines.append(f"    - …(+{n-4} more)")

    path = f"{_OUT}/UPLOAD_FLOW_{bot}.md"
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    await conn.close()
    print(f"wrote {path}  ({len(docs)} docs)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: log_upload_flow.py <bot_id>")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
