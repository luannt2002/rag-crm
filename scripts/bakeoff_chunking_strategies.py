#!/usr/bin/env python3
"""Chunking-strategy bake-off — does ragbot's adaptive selector pick the best?

For every live document we reconstruct its source text, re-chunk it with EACH
of ragbot's prose strategies (recursive / hdt / semantic / hybrid /
proposition) at a common size budget, score every output with the Ekimetrics
5-metric **lexical** intrinsic suite, and compare:

    adaptive_pick  = what ``select_strategy(analyze_document(text))`` chooses
    oracle_best    = the strategy with the highest composite this doc

This is the project-local analogue of the paper's "Adaptive Chunking" row
(Table 3): an adaptive selector is only worth its complexity if it tracks the
per-document oracle. We report the agreement rate and the composite GAP
(oracle_best − adaptive) — the headroom a better selector could recover.

WHY this is valid despite the lexical metric's weak absolutes: RC is constant
across strategies for a fixed document (same xref markers), so it cancels in
the ranking; the *relative* ordering of strategies under one self-consistent
metric is the signal we trust here (NOT the absolute composite vs the paper's
embedder numbers).

Pure CPU re-chunking + read-only SELECT. No Jina, no DB writes. Exit 0.

Usage::

    python scripts/bakeoff_chunking_strategies.py \\
        --output-md reports/bakeoff_chunking_20260620.md \\
        --output-json reports/bakeoff_chunking_20260620.json
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

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import structlog  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.chunking.analyze import (  # noqa: E402
    analyze_document,
    select_strategy,
)
from ragbot.shared.constants import DEFAULT_CHUNK_SIZE  # noqa: E402
from ragbot.shared.intrinsic_metrics import (  # noqa: E402
    IntrinsicMetrics,
    compute_intrinsic_metrics,
)

logger = structlog.get_logger("ragbot.bakeoff_chunking")
EVAL_STEP_NAME = "bakeoff_chunking_strategies"

DEFAULT_BOTS: tuple[str, ...] = (
    "chinh-sach-xe",
    "test-spa-id",
    "thong-tu-09-2020-tt-nhnn",
)

# Prose strategies smart_chunk dispatches for free-text docs.
STRATEGIES: tuple[str, ...] = ("recursive", "hdt", "semantic", "hybrid", "proposition")

_METRIC_WEIGHT = 0.2


def _composite(m: IntrinsicMetrics) -> float:
    return _METRIC_WEIGHT * (m.RC + m.ICC + m.DCC + m.BI + m.SC)


@dataclass(frozen=True)
class DocBakeoff:
    bot_id: str
    doc_id: str
    adaptive_pick: str
    adaptive_confidence: float
    scores: dict[str, float]  # strategy -> composite

    @property
    def oracle_best(self) -> str:
        return max(self.scores, key=lambda s: self.scores[s])

    @property
    def adaptive_composite(self) -> float:
        # Adaptive pick may be a strategy we didn't bake (e.g. table_csv);
        # fall back to recursive's score as the realised baseline.
        return self.scores.get(self.adaptive_pick, self.scores.get("recursive", 0.0))

    @property
    def gap(self) -> float:
        return self.scores[self.oracle_best] - self.adaptive_composite


async def _fetch_doc_texts(dsn: str, bots: tuple[str, ...]) -> dict[tuple[str, str], str]:
    """Reconstruct each doc's source text = concat(parents) else concat(leaves)."""
    eng = create_async_engine(dsn)
    try:
        conn = await eng.connect()
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        rows = list(
            await conn.execute(
                sql_text(
                    """
                    SELECT b.bot_id AS bot_id,
                           dc.record_document_id::text AS doc_id,
                           dc.chunk_index AS chunk_index,
                           dc.content AS content,
                           (dc.embedding IS NOT NULL) AS is_leaf
                    FROM document_chunks dc
                    JOIN bots b ON b.id = dc.record_bot_id
                    WHERE b.bot_id = ANY(:bots) AND dc.doc_deleted_at IS NULL
                    ORDER BY b.bot_id, dc.record_document_id, dc.chunk_index
                    """
                ),
                {"bots": list(bots)},
            )
        )
        await conn.close()
    finally:
        await eng.dispose()

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r.bot_id, r.doc_id)].append(dict(r._mapping))

    out: dict[tuple[str, str], str] = {}
    for key, chunks in grouped.items():
        parents = [c for c in chunks if not c["is_leaf"] and c["content"]]
        leaves = [c for c in chunks if c["is_leaf"] and c["content"]]
        source = parents if parents else leaves
        out[key] = "\n\n".join(c["content"] for c in source)
    return out


def bakeoff_document(bot_id: str, doc_id: str, full_text: str) -> DocBakeoff | None:
    if not full_text or not full_text.strip():
        return None
    profile = analyze_document(full_text)
    adaptive_pick, adaptive_conf = select_strategy(profile)

    scores: dict[str, float] = {}
    for strat in STRATEGIES:
        chunks = smart_chunk(full_text, strategy=strat)
        chunks = [c for c in chunks if c and c.strip()]
        if not chunks:
            continue
        m = compute_intrinsic_metrics(
            full_text, blocks=chunks, chunks=chunks,
            target_chunk_chars=DEFAULT_CHUNK_SIZE,
        )
        scores[strat] = round(_composite(m), 4)

    if not scores:
        return None
    return DocBakeoff(
        bot_id=bot_id, doc_id=doc_id,
        adaptive_pick=adaptive_pick, adaptive_confidence=round(adaptive_conf, 4),
        scores=scores,
    )


def render_markdown(results: list[DocBakeoff]) -> str:
    lines = [
        "# Chunking-strategy bake-off (live corpus)",
        "",
        "Composite = Ekimetrics 5-metric lexical, uniform 0.2 weight, scored "
        f"at a common {DEFAULT_CHUNK_SIZE}-char budget. RC is constant per doc "
        "(cancels in ranking). **oracle_best** = highest-composite strategy; "
        "**adaptive_pick** = what `select_strategy` chose.",
        "",
        "## Per-document",
        "",
        "| bot | doc | adaptive_pick (conf) | oracle_best | "
        + " | ".join(STRATEGIES)
        + " | gap |",
        "| --- | --- | --- | --- | "
        + " | ".join("---:" for _ in STRATEGIES)
        + " | ---: |",
    ]
    for r in sorted(results, key=lambda x: (x.bot_id, x.doc_id)):
        cells = []
        for s in STRATEGIES:
            v = r.scores.get(s)
            cell = "—" if v is None else f"{v:.3f}"
            if s == r.oracle_best:
                cell = f"**{cell}**"
            if s == r.adaptive_pick:
                cell = f"_{cell}_"
            cells.append(cell)
        agree = "✅" if r.adaptive_pick == r.oracle_best else "⚠️"
        lines.append(
            f"| {r.bot_id} | {r.doc_id[:8]} | {r.adaptive_pick} "
            f"({r.adaptive_confidence:.2f}) | {agree} {r.oracle_best} | "
            + " | ".join(cells)
            + f" | {r.gap:.3f} |"
        )

    # Aggregate.
    n = len(results)
    agree_n = sum(1 for r in results if r.adaptive_pick == r.oracle_best)
    mean_adaptive = sum(r.adaptive_composite for r in results) / n if n else 0.0
    mean_oracle = sum(r.scores[r.oracle_best] for r in results) / n if n else 0.0
    mean_recursive = (
        sum(r.scores.get("recursive", 0.0) for r in results) / n if n else 0.0
    )
    lines += [
        "",
        "## Aggregate",
        "",
        f"- Documents: **{n}**",
        f"- Adaptive == oracle_best: **{agree_n}/{n}** "
        f"({100 * agree_n / n:.0f}%)" if n else "- (no docs)",
        f"- Mean composite — adaptive **{mean_adaptive:.3f}** · "
        f"oracle ceiling **{mean_oracle:.3f}** · recursive baseline "
        f"**{mean_recursive:.3f}**",
        f"- Selector headroom (oracle − adaptive): **{mean_oracle - mean_adaptive:.3f}**",
        f"- Adaptive lift over recursive baseline: "
        f"**{mean_adaptive - mean_recursive:+.3f}**",
        "",
    ]
    return "\n".join(lines)


def render_json(results: list[DocBakeoff]) -> str:
    n = len(results) or 1
    payload = {
        "schema_version": 1,
        "metric_impl": "lexical",
        "budget_chars": DEFAULT_CHUNK_SIZE,
        "strategies": list(STRATEGIES),
        "documents": [
            {
                "bot_id": r.bot_id,
                "doc_id": r.doc_id,
                "adaptive_pick": r.adaptive_pick,
                "adaptive_confidence": r.adaptive_confidence,
                "oracle_best": r.oracle_best,
                "scores": r.scores,
                "gap": round(r.gap, 4),
            }
            for r in sorted(results, key=lambda x: (x.bot_id, x.doc_id))
        ],
        "aggregate": {
            "documents": len(results),
            "adaptive_eq_oracle": sum(
                1 for r in results if r.adaptive_pick == r.oracle_best
            ),
            "mean_adaptive": round(sum(r.adaptive_composite for r in results) / n, 4),
            "mean_oracle": round(sum(r.scores[r.oracle_best] for r in results) / n, 4),
            "mean_recursive": round(
                sum(r.scores.get("recursive", 0.0) for r in results) / n, 4
            ),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bakeoff_chunking_strategies")
    p.add_argument("--bots", type=str, default=",".join(DEFAULT_BOTS))
    p.add_argument("--output-json", type=Path, default=None)
    p.add_argument("--output-md", type=Path, default=None)
    args = p.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2
    bots = tuple(b.strip() for b in args.bots.split(",") if b.strip())

    doc_texts = await _fetch_doc_texts(dsn, bots)
    results: list[DocBakeoff] = []
    for (bot_id, doc_id), full_text in doc_texts.items():
        r = bakeoff_document(bot_id, doc_id, full_text)
        if r is not None:
            results.append(r)
            logger.info(
                "bakeoff_doc_scored",
                step_name=EVAL_STEP_NAME,
                bot_id=bot_id,
                doc_id=doc_id[:8],
                adaptive_pick=r.adaptive_pick,
                oracle_best=r.oracle_best,
                gap=round(r.gap, 4),
            )

    md = render_markdown(results)
    js = render_json(results)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(js, encoding="utf-8")
    sys.stdout.write(md + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
