#!/usr/bin/env python3
"""8-step RAG debug workflow — verify the whole pipeline with DATA, end-to-end.

A RAG project is debugged in 8 steps across 3 phases; each step is verified
with a real query/probe, and a failure is traced BACKWARD (answer → prompt →
chunks → retrieval → corpus) to the owning layer:

  INGEST  1 PARSE   file → structured markdown (heading/table/atomic kept?)
          2 CHUNK   strategy right? atomic uncut? parent-child? structural_path?
          3 EMBED   null_leaf == 0? dim correct? (parents NULL by design)
          4 STORE   tsvector + embedding searchable? stats-index? KG?
  QUERY   5 RETRIEVE answer chunk in top-K? (dense+BM25+RRF+stats+rerank)
          6 GENERATE LLM answers from context? no false-refuse? no fabricate?
          7 GUARD    grounding/refusal correct?
  EVAL    8 SCORE   COVERAGE + HALLU + layer-attribution

Steps 1–4 are pure read-only DB SELECTs (fast, no server). Steps 5–8 need the
live server + scenario fixtures and are delegated to ``eval_rag_endtoend.py``
(run with ``--live``). Each step prints PASS / WARN / FAIL + the evidence.

Usage::

    set -a && source .env && set +a
    python scripts/debug_rag_8step.py                 # ingest health (1-4)
    python scripts/debug_rag_8step.py --live          # full 1-8 (needs server)
    python scripts/debug_rag_8step.py --output-md reports/debug_rag.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DEFAULT_BOTS = ("chinh-sach-xe", "test-spa-id", "thong-tu-09-2020-tt-nhnn")
PASS, WARN, FAIL = "✅ PASS", "⚠️  WARN", "❌ FAIL"

_lines: list[str] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    _lines.append(s)


async def _ingest_steps(dsn: str, bots: tuple[str, ...]) -> list[str]:
    """Steps 1–4 (read-only). Returns per-step verdict codes."""
    eng = create_async_engine(dsn)
    verdicts: list[str] = []
    try:
        c = await (await eng.connect()).execution_options(isolation_level="AUTOCOMMIT")

        # ── STEP 1+2 — PARSE + CHUNK structure ───────────────────────────
        emit("## STEP 1+2 — PARSE + CHUNK (structure)")
        rows = list(await c.execute(text(
            """
            SELECT b.bot_id, count(*) total,
              count(*) FILTER (WHERE dc.chunk_type='table') tbl,
              count(*) FILTER (WHERE dc.content LIKE '%[%>%]%') path,
              count(*) FILTER (WHERE dc.parent_chunk_id IS NOT NULL) children,
              count(*) FILTER (WHERE dc.embedding IS NULL) parents,
              round(avg(dc.chunk_chars)) avgc
            FROM document_chunks dc JOIN bots b ON b.id=dc.record_bot_id
            WHERE b.bot_id = ANY(:b) AND dc.doc_deleted_at IS NULL
            GROUP BY b.bot_id ORDER BY b.bot_id
            """), {"b": list(bots)}))
        ok = bool(rows) and all(r.total > 0 for r in rows)
        for r in rows:
            emit(f"  {r.bot_id:<28} chunks={r.total} table={r.tbl} "
                 f"struct_path={r.path} children={r.children} parents={r.parents} "
                 f"avg_chars={r.avgc}")
        v = PASS if ok else FAIL
        emit(f"  → {v} (chunks exist + structured: table chunks for CSV, "
             "structural_path for hierarchical docs)")
        verdicts.append(v)
        emit("")

        # ── STEP 3 — EMBED ───────────────────────────────────────────────
        emit("## STEP 3 — EMBED (leaf coverage)")
        rows = list(await c.execute(text(
            """
            SELECT b.bot_id,
              count(*) FILTER (WHERE dc.embedding IS NOT NULL) embedded,
              count(*) FILTER (WHERE dc.embedding IS NULL AND NOT EXISTS(
                SELECT 1 FROM document_chunks ch WHERE ch.parent_chunk_id=dc.id)
              ) null_leaf
            FROM document_chunks dc JOIN bots b ON b.id=dc.record_bot_id
            WHERE b.bot_id = ANY(:b) AND dc.doc_deleted_at IS NULL
            GROUP BY b.bot_id ORDER BY b.bot_id
            """), {"b": list(bots)}))
        bad = [r for r in rows if r.null_leaf > 0]
        for r in rows:
            emit(f"  {r.bot_id:<28} embedded={r.embedded} null_leaf_BAD={r.null_leaf}")
        v = FAIL if bad else PASS
        emit(f"  → {v} (null_leaf must be 0 — a leaf with no vector is invisible; "
             "parents NULL is by-design small-to-big)")
        verdicts.append(v)
        emit("")

        # ── STEP 4 — STORE ───────────────────────────────────────────────
        emit("## STEP 4 — STORE (searchable surfaces)")
        rows = list(await c.execute(text(
            """
            SELECT b.bot_id,
              count(*) FILTER (WHERE dc.search_vector IS NOT NULL) ts,
              count(*) total
            FROM document_chunks dc JOIN bots b ON b.id=dc.record_bot_id
            WHERE b.bot_id = ANY(:b) AND dc.doc_deleted_at IS NULL
            GROUP BY b.bot_id ORDER BY b.bot_id
            """), {"b": list(bots)}))
        si = dict(list(await c.execute(text(
            """SELECT b.bot_id, count(*) FROM document_service_index si
               JOIN bots b ON b.id=si.record_bot_id WHERE b.bot_id = ANY(:b)
               GROUP BY b.bot_id"""), {"b": list(bots)})))
        kg = list(await c.execute(text("SELECT count(*) FROM knowledge_edges")))[0][0]
        ts_bad = [r for r in rows if r.ts < r.total]
        for r in rows:
            emit(f"  {r.bot_id:<28} tsvector={r.ts}/{r.total} "
                 f"stats_index={si.get(r.bot_id, 0)}")
        emit(f"  knowledge_edges (KG): {kg}  "
             f"{'(empty — KG not populated at ingest)' if kg == 0 else ''}")
        v = FAIL if ts_bad else (WARN if kg == 0 else PASS)
        emit(f"  → {v} (tsvector must be 100% for BM25; stats-index drives "
             "price/list; KG=0 means graph-retrieval is dormant)")
        verdicts.append(v)
        emit("")
        await c.close()
    finally:
        await eng.dispose()
    return verdicts


def _query_steps(bots: tuple[str, ...]) -> list[str]:
    """Steps 5–8 — delegate to eval_rag_endtoend (live)."""
    emit("## STEP 5-8 — RETRIEVE / GENERATE / GUARD / SCORE (live)")
    out_json = _ROOT / "reports" / "debug_rag_8step_eval.json"
    cmd = [
        sys.executable, str(_ROOT / "scripts" / "eval_rag_endtoend.py"),
        "--output-json", str(out_json),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except (subprocess.SubprocessError, OSError) as exc:
        emit(f"  {FAIL} — could not run eval_rag_endtoend: {exc}")
        return [FAIL]
    if r.returncode != 0 or not out_json.exists():
        emit(f"  {FAIL} — eval_rag_endtoend exit {r.returncode}")
        emit("  " + (r.stderr or r.stdout or "")[-400:])
        return [FAIL]
    data = json.loads(out_json.read_text())
    covs, recs, hallus = [], [], []
    for b in data.get("bots", []):
        covs.append(b["coverage"])
        recs.append(b["chunk_recall"])
        hallus.append(b["hallu_rate"])
        emit(f"  {b['bot_id']:<28} COVERAGE={b['coverage']:.2f} "
             f"CHUNK_RECALL={b['chunk_recall']:.2f} HALLU={b['hallu_rate']:.2f} "
             f"retr_miss={b['retrieval_miss']} llm_miss={b['llm_miss']}")
    mean_cov = sum(covs) / len(covs) if covs else 0.0
    max_hallu = max(hallus) if hallus else 0.0
    # Step 5 RETRIEVE verdict = chunk recall; 6 GENERATE = coverage; 7 GUARD = hallu
    v5 = PASS if (recs and sum(recs) / len(recs) >= 0.4) else WARN
    v6 = PASS if mean_cov >= 0.8 else (WARN if mean_cov >= 0.6 else FAIL)
    v7 = PASS if max_hallu == 0 else FAIL
    v8 = PASS if (mean_cov >= 0.8 and max_hallu == 0) else WARN
    emit(f"  → STEP 5 RETRIEVE {v5} · STEP 6 GENERATE {v6} "
         f"(COVERAGE mean {mean_cov:.2f}) · STEP 7 GUARD {v7} "
         f"(HALLU max {max_hallu:.2f}) · STEP 8 SCORE {v8}")
    emit("")
    return [v5, v6, v7, v8]


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="debug_rag_8step")
    p.add_argument("--bots", default=",".join(DEFAULT_BOTS))
    p.add_argument("--live", action="store_true",
                   help="also run STEP 5-8 (needs server + scenarios)")
    p.add_argument("--output-md", type=Path, default=None)
    args = p.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2
    bots = tuple(b.strip() for b in args.bots.split(",") if b.strip())

    emit("# RAG 8-step debug workflow")
    emit(f"bots: {', '.join(bots)} · live: {args.live}")
    emit("")
    verdicts = await _ingest_steps(dsn, bots)
    if args.live:
        verdicts += _query_steps(bots)
    else:
        emit("## STEP 5-8 — skipped (pass --live to run the query/eval side)")
        emit("")

    n_fail = sum(1 for v in verdicts if v == FAIL)
    n_warn = sum(1 for v in verdicts if v == WARN)
    overall = FAIL if n_fail else (WARN if n_warn else PASS)
    emit("## OVERALL")
    emit(f"  {overall} — {len(verdicts)} steps checked · "
         f"{n_fail} FAIL · {n_warn} WARN")

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text("\n".join(_lines), encoding="utf-8")
    return 1 if n_fail else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
