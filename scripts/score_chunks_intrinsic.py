#!/usr/bin/env python3
"""Intrinsic chunk-quality scorer (Ekimetrics 5-metric) on LIVE stored chunks.

Ground-truth-FREE audit. Reads ``document_chunks`` per bot, reconstructs each
source document, and scores the five Ekimetrics intrinsic metrics
(RC / ICC / DCC / BI / SC) via ``ragbot.shared.intrinsic_metrics`` — the
project's **lexical** port (Jaccard / regex / size-band; NO embedder, NO
coreference). This is what makes the audit cheap: pure SELECT + pure Python,
zero Jina spend, runnable any time.

HONEST CAVEAT (rule #0 — no overclaim): ragbot's ICC/DCC/RC are *lexical
approximations* of the paper's embedder-cosine (ICC/DCC) and Maverick-coref
(RC) versions, so those three are NOT directly comparable to the paper's
Table-3 numbers. SC and BI align in spirit (size band / block integrity).
Treat the composite as an internal, self-consistent quality signal — good for
ranking ragbot's own strategies against each other (the bake-off), not for
claiming parity with the published benchmark.

Reference — Ekimetrics LREC 2026 (arXiv:2603.25333) Table 3, intrinsic mean %
across 3 domains (embedder + coref impl, NOT this lexical one):
    Adaptive 91.07 · LLM-regex 89.80 · LangChain-recursive 88.62 ·
    Semantic 76.49 · Sentence 73.26

Pure read-only SELECT. Exit 0 (audit, not a CI gate).

Usage::

    python scripts/score_chunks_intrinsic.py \\
        --output-json reports/intrinsic_live_$(date +%Y%m%d).json \\
        --output-md   reports/intrinsic_live_$(date +%Y%m%d).md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Allow ``python scripts/score_chunks_intrinsic.py`` from any worktree.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import structlog  # noqa: E402 — after sys.path bootstrap
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_CHILD_CHUNK_SIZE,
)
from ragbot.shared.intrinsic_metrics import (  # noqa: E402
    IntrinsicMetrics,
    compute_intrinsic_metrics,
)

logger = structlog.get_logger("ragbot.score_chunks_intrinsic")

EVAL_STEP_NAME = "score_chunks_intrinsic"

# Default cohort = the three bots with curated scenarios + live corpora.
DEFAULT_BOTS: tuple[str, ...] = (
    "chinh-sach-xe",
    "test-spa-id",
    "thong-tu-09-2020-tt-nhnn",
)

# Cited external benchmark for the rendered comparison column. NOT a tunable —
# verbatim published values from the paper's Table 3 (embedder+coref impl).
PAPER_TABLE3_MEAN: dict[str, float] = {
    "Adaptive": 91.07,
    "LLM-regex": 89.80,
    "LangChain-recursive": 88.62,
    "Semantic": 76.49,
    "Sentence": 73.26,
}

# Uniform metric weight, matching the paper's find_best_method (0.2 each).
_METRIC_WEIGHT = 0.2


@dataclass(frozen=True)
class DocScore:
    """Intrinsic metrics for one reconstructed document."""

    bot_id: str
    record_document_id: str
    n_leaf: int
    n_parent: int
    mean_leaf_chars: float
    metrics: IntrinsicMetrics

    @property
    def composite(self) -> float:
        """Uniform-weighted mean of the 5 metrics (paper convention)."""
        m = self.metrics
        return _METRIC_WEIGHT * (m.RC + m.ICC + m.DCC + m.BI + m.SC)


def _composite(m: IntrinsicMetrics) -> float:
    return _METRIC_WEIGHT * (m.RC + m.ICC + m.DCC + m.BI + m.SC)


async def _fetch_chunks(dsn: str, bots: tuple[str, ...]) -> list[dict]:
    """Pull live (non-deleted) chunks for ``bots``, ordered for reconstruction.

    Returns one row dict per chunk with the columns the scorer needs. Pure
    SELECT; AUTOCOMMIT so we never hold a write txn.
    """
    eng = create_async_engine(dsn)
    try:
        conn = await eng.connect()
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        rows = list(
            await conn.execute(
                text(
                    """
                    SELECT b.bot_id                       AS bot_id,
                           dc.record_document_id::text    AS doc_id,
                           dc.chunk_index                 AS chunk_index,
                           dc.content                     AS content,
                           (dc.embedding IS NOT NULL)     AS is_leaf,
                           COALESCE(dc.chunk_chars, length(dc.content)) AS chunk_chars
                    FROM document_chunks dc
                    JOIN bots b ON b.id = dc.record_bot_id
                    WHERE b.bot_id = ANY(:bots)
                      AND dc.doc_deleted_at IS NULL
                    ORDER BY b.bot_id, dc.record_document_id, dc.chunk_index
                    """
                ),
                {"bots": list(bots)},
            )
        )
        await conn.close()
        return [dict(r._mapping) for r in rows]
    finally:
        await eng.dispose()


def score_document(
    bot_id: str,
    doc_id: str,
    chunks: list[dict],
    *,
    target_chunk_chars: int,
) -> DocScore | None:
    """Score one document's stored chunks with the 5 intrinsic metrics.

    Reconstruction contract:
      * ``leaves``  = embedded chunks (the retrieval units) ordered by index.
      * ``parents`` = un-embedded big sections (small-to-big) ordered by index.
      * ``full_text`` = concat(parents) when present (best whole-doc proxy),
        else concat(leaves). Used for RC / DCC gist.
      * SC + BI + ICC are scored on the LEAF chunks — what actually gets
        retrieved.

    Returns ``None`` when the doc has no leaf chunks (nothing to score).
    """
    leaves = [c for c in chunks if c["is_leaf"]]
    parents = [c for c in chunks if not c["is_leaf"]]
    if not leaves:
        return None

    leaf_texts = [c["content"] for c in leaves if c["content"]]
    if not leaf_texts:
        return None

    source = parents if parents else leaves
    full_text = "\n\n".join(c["content"] for c in source if c["content"])

    metrics = compute_intrinsic_metrics(
        full_text,
        blocks=leaf_texts,
        chunks=leaf_texts,
        target_chunk_chars=target_chunk_chars,
    )
    mean_chars = sum(int(c["chunk_chars"]) for c in leaves) / len(leaves)

    return DocScore(
        bot_id=bot_id,
        record_document_id=doc_id,
        n_leaf=len(leaves),
        n_parent=len(parents),
        mean_leaf_chars=round(mean_chars, 1),
        metrics=metrics,
    )


def aggregate_by_bot(scores: list[DocScore]) -> dict[str, IntrinsicMetrics]:
    """Mean of each metric across a bot's documents."""
    by_bot: dict[str, list[DocScore]] = defaultdict(list)
    for s in scores:
        by_bot[s.bot_id].append(s)
    out: dict[str, IntrinsicMetrics] = {}
    for bot, docs in by_bot.items():
        n = len(docs)
        out[bot] = IntrinsicMetrics(
            RC=sum(d.metrics.RC for d in docs) / n,
            ICC=sum(d.metrics.ICC for d in docs) / n,
            DCC=sum(d.metrics.DCC for d in docs) / n,
            BI=sum(d.metrics.BI for d in docs) / n,
            SC=sum(d.metrics.SC for d in docs) / n,
        )
    return out


def render_markdown(
    per_bot: dict[str, IntrinsicMetrics],
    scores: list[DocScore],
    *,
    target_chunk_chars: int,
) -> str:
    """Human-readable scorecard with the paper Table-3 reference."""
    lines = [
        "# Intrinsic chunk-quality scorecard (live corpus)",
        "",
        "Ekimetrics 5-metric (RC/ICC/DCC/BI/SC), **lexical** impl "
        "(`ragbot.shared.intrinsic_metrics`). Composite = uniform 0.2 weight.",
        "",
        f"SC band scored against target = `DEFAULT_CHILD_CHUNK_SIZE` "
        f"({target_chunk_chars} chars). Values are 0–1 (×100 = %).",
        "",
        "> CAVEAT: ICC/DCC/RC are lexical (Jaccard/regex), NOT the paper's "
        "embedder-cosine + coref. Use for ranking ragbot's own strategies, "
        "not for claiming parity with the published benchmark.",
        "",
        "## Per-bot mean",
        "",
        "| bot | docs | RC | ICC | DCC | BI | SC | **composite** |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    doc_counts: dict[str, int] = defaultdict(int)
    for s in scores:
        doc_counts[s.bot_id] += 1
    for bot in sorted(per_bot):
        m = per_bot[bot]
        lines.append(
            f"| {bot} | {doc_counts[bot]} | {m.RC:.3f} | {m.ICC:.3f} | "
            f"{m.DCC:.3f} | {m.BI:.3f} | {m.SC:.3f} | "
            f"**{_composite(m):.3f}** |"
        )

    # Overall (mean across bots, so each bot weighs equally).
    if per_bot:
        n = len(per_bot)
        overall = IntrinsicMetrics(
            RC=sum(m.RC for m in per_bot.values()) / n,
            ICC=sum(m.ICC for m in per_bot.values()) / n,
            DCC=sum(m.DCC for m in per_bot.values()) / n,
            BI=sum(m.BI for m in per_bot.values()) / n,
            SC=sum(m.SC for m in per_bot.values()) / n,
        )
        lines.append(
            f"| **ALL (mean)** | {len(scores)} | {overall.RC:.3f} | "
            f"{overall.ICC:.3f} | {overall.DCC:.3f} | {overall.BI:.3f} | "
            f"{overall.SC:.3f} | **{_composite(overall):.3f}** |"
        )

    lines += [
        "",
        "## Paper Table-3 reference (embedder+coref impl — context only)",
        "",
        "| Method | mean % |",
        "| --- | ---: |",
    ]
    for method, val in PAPER_TABLE3_MEAN.items():
        lines.append(f"| {method} | {val:.2f} |")

    lines += [
        "",
        "## Per-document detail",
        "",
        "| bot | doc | leaves | parents | mean_chars | RC | ICC | DCC | BI | SC | composite |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in sorted(scores, key=lambda x: (x.bot_id, x.record_document_id)):
        m = s.metrics
        lines.append(
            f"| {s.bot_id} | {s.record_document_id[:8]} | {s.n_leaf} | "
            f"{s.n_parent} | {s.mean_leaf_chars} | {m.RC:.3f} | {m.ICC:.3f} | "
            f"{m.DCC:.3f} | {m.BI:.3f} | {m.SC:.3f} | {s.composite:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_json(per_bot: dict[str, IntrinsicMetrics], scores: list[DocScore]) -> str:
    payload = {
        "schema_version": 1,
        "metric_impl": "lexical",
        "paper_table3_mean_reference": PAPER_TABLE3_MEAN,
        "per_bot": {
            bot: {**m.as_dict(), "composite": _composite(m)}
            for bot, m in sorted(per_bot.items())
        },
        "per_document": [
            {
                "bot_id": s.bot_id,
                "record_document_id": s.record_document_id,
                "n_leaf": s.n_leaf,
                "n_parent": s.n_parent,
                "mean_leaf_chars": s.mean_leaf_chars,
                **s.metrics.as_dict(),
                "composite": s.composite,
            }
            for s in sorted(scores, key=lambda x: (x.bot_id, x.record_document_id))
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="score_chunks_intrinsic",
        description="Score live stored chunks with the Ekimetrics 5 intrinsic metrics.",
    )
    p.add_argument(
        "--bots",
        type=str,
        default=",".join(DEFAULT_BOTS),
        help="Comma-separated bot_id slugs (default: the 3 scenario bots).",
    )
    p.add_argument("--output-json", type=Path, default=None)
    p.add_argument("--output-md", type=Path, default=None)
    p.add_argument(
        "--target-chars",
        type=int,
        default=DEFAULT_CHILD_CHUNK_SIZE,
        help="SC band target (chars). Default = DEFAULT_CHILD_CHUNK_SIZE.",
    )
    return p


async def _amain(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2
    bots = tuple(b.strip() for b in args.bots.split(",") if b.strip())

    rows = await _fetch_chunks(dsn, bots)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["bot_id"], r["doc_id"])].append(r)

    scores: list[DocScore] = []
    for (bot_id, doc_id), chunks in grouped.items():
        s = score_document(
            bot_id, doc_id, chunks, target_chunk_chars=args.target_chars
        )
        if s is not None:
            scores.append(s)

    per_bot = aggregate_by_bot(scores)
    for bot, m in sorted(per_bot.items()):
        logger.info(
            "intrinsic_bot_scored",
            step_name=EVAL_STEP_NAME,
            bot_id=bot,
            RC=round(m.RC, 4),
            ICC=round(m.ICC, 4),
            DCC=round(m.DCC, 4),
            BI=round(m.BI, 4),
            SC=round(m.SC, 4),
            composite=round(_composite(m), 4),
        )

    md = render_markdown(per_bot, scores, target_chunk_chars=args.target_chars)
    js = render_json(per_bot, scores)

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(js, encoding="utf-8")
    if not args.output_md and not args.output_json:
        sys.stdout.write(md + "\n")
    else:
        # Always echo the per-bot table so the operator sees numbers inline.
        sys.stdout.write(md.split("## Paper Table-3")[0] + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
