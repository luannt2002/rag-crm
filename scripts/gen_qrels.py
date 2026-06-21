#!/usr/bin/env python3
"""gen_qrels.py — auto-generate a larger eval set + qrels from the stats index
(Phase B-2 power). Weak-point #1: 42 hand queries lack statistical power → no
significance, no fair AdapChunk comparison. This derives MANY factoid queries
with GROUND-TRUTH (the stats price) + the relevant source chunk (record_chunk_id)
straight from document_service_index — no LLM, no labelling, fully deterministic.

Output: a scenario JSON (eval_rag_endtoend / eval_rigor consume it) + a qrels map
{qid: [relevant_chunk_id]} for retrieval Hit@K/MRR.

Usage:
  python scripts/gen_qrels.py --bot chinh-sach-xe --workspace xe --n 30 \
      --out tests/scenarios/_gen_chinh-sach-xe_scenario.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = scheme.split("+", 1)[0] + "://" + rest
    if not raw:
        raise SystemExit("DATABASE_URL required")
    return raw


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gen_qrels")
    p.add_argument("--bot", required=True)
    p.add_argument("--workspace", default="")
    p.add_argument("--channel", default="web")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--out", required=True)
    p.add_argument("--qrels-out", default="")
    a = p.parse_args(argv)

    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    # Real catalog entities with a price + a source chunk FK (ground-truth +
    # qrel). Skip noise (field-like name, has a price, has chunk FK). Sample
    # spread across the corpus (ORDER BY a hash of id for determinism without
    # Math.random).
    cur.execute(
        """
        SELECT entity_name, COALESCE(price_primary, price_secondary) AS price,
               record_chunk_id
        FROM document_service_index si JOIN bots b ON si.record_bot_id = b.id
        WHERE b.bot_id = %s
          AND COALESCE(price_primary, price_secondary) IS NOT NULL
          -- plausible VND price range: excludes barcodes / date-codes /
          -- quantities mis-parsed into the price column (e.g. 2025122435548).
          AND COALESCE(price_primary, price_secondary) BETWEEN 1000 AND 500000000
          AND record_chunk_id IS NOT NULL
          AND char_length(entity_name) BETWEEN 4 AND 60
          -- a clean single-product name (no comma = not a synonym/variant list);
          -- a real user queries one product, not a mega-cell of spelling variants.
          AND entity_name NOT LIKE '%%,%%'
          AND entity_name !~* 'google|http|^date|^question|^quantity|^chunk|Đoạn '
          -- UNAMBIGUOUS ground-truth: the name must map to exactly ONE price.
          -- Stats has duplicate-name rows (same SKU, 2 list prices) → a query
          -- on that name has no single correct answer (the bot may quote either,
          -- a false-negative). Keep only names with a single distinct price.
          AND si.entity_name IN (
              SELECT s2.entity_name FROM document_service_index s2
              WHERE s2.record_bot_id = b.id
              GROUP BY s2.entity_name
              HAVING count(DISTINCT COALESCE(s2.price_primary, s2.price_secondary)) = 1
          )
        ORDER BY md5(si.id::text)
        LIMIT %s
        """,
        (a.bot, a.n),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    questions = []
    qrels: dict = {}
    for i, (name, price, chunk_id) in enumerate(rows):
        qid = f"g{i + 1:03d}"
        # ground-truth expect = the price as a plain integer string (the eval
        # number-normaliser matches "1.199.000" vs "1199000").
        questions.append({
            "id": qid, "flow": "gen_price_factoid",
            "q": f"{name} giá bao nhiêu?",
            "expect": str(int(price)),
        })
        qrels[f"{a.bot}|{qid}"] = [str(chunk_id)]

    scenario = {
        "bot_id": a.bot, "channel_type": a.channel,
        "workspace_id": a.workspace or a.bot,
        "note": "AUTO-GENERATED from document_service_index (ground-truth price + "
                "qrel chunk). Deterministic, no LLM. B-2 power booster.",
        "questions": questions,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(scenario, f, ensure_ascii=False, indent=2)
    if a.qrels_out:
        with open(a.qrels_out, "w", encoding="utf-8") as f:
            json.dump(qrels, f, ensure_ascii=False, indent=2)
    print(f"generated {len(questions)} ground-truth factoid queries → {a.out}")
    if a.qrels_out:
        print(f"qrels (qid→relevant chunk) → {a.qrels_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
