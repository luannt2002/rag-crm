#!/usr/bin/env python3
"""L1 intrinsic scorer — REAL embedding-cosine (ekimetrics-style), from STORED
vectors.

The lexical scorer (``score_chunks_intrinsic.py``) approximates ekimetrics'
ICC/DCC/RC with Jaccard/regex (honest caveat: not comparable to the paper's
embedder-cosine). This scorer computes the two metrics that the LEAF chunk
embeddings ALREADY in pgvector can support directly — no re-embedding, no Jina
cost, no SentenceTransformer download:

  * SD  Semantic Dissimilarity (ekimetrics ``compute_semantic_dissimilarity``):
        adjacent chunks should be DISTINCT topics. Sliding-window neighbour
        cosine, length-weighted; score = 1 − weighted_mean_cos. Higher = better
        (chunks don't repeat their neighbours).
  * CC  Contextual Coherence (ekimetrics ``compute_contextual_coherence``,
        doc-centroid variant): each chunk should still fit its document. Mean
        cos(chunk, doc_centroid). Higher = better (chunk belongs to the doc).

ICC (sentence-level) + MRE (coref) need sentence/coref embeddings that are NOT
stored, so they stay in the lexical scorer; this complements it with the two
REAL embedding signals.

Run::  set -a && source .env && set +a && python scripts/score_chunks_embedding.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DEFAULT_BOTS = ("chinh-sach-xe", "test-spa-id", "thong-tu-09-2020-tt-nhnn")
_SD_WINDOW = 5  # ekimetrics default sliding window


def _parse_vec(raw: str) -> np.ndarray:
    # pgvector ::text → "[0.1,0.2,...]"
    return np.fromstring(raw.strip().lstrip("[").rstrip("]"), sep=",", dtype=np.float32)


def _normalize(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


def _semantic_dissimilarity(emb: np.ndarray, lengths: np.ndarray) -> float | None:
    """ekimetrics SD: 1 − length-weighted mean cosine over a sliding window."""
    n = len(emb)
    if n < 2:
        return None
    e = _normalize(emb)
    tot_sim = tot_w = 0.0
    for i in range(n):
        for j in range(i + 1, min(i + _SD_WINDOW + 1, n)):
            sim = float(np.dot(e[i], e[j]))
            w = float(lengths[i] * lengths[j])
            tot_sim += sim * w
            tot_w += w
    if tot_w == 0:
        return None
    return float(np.clip(1.0 - tot_sim / tot_w, 0.0, 1.0))


def _contextual_coherence(emb: np.ndarray) -> float | None:
    """Doc-centroid CC: mean cos(chunk, doc_centroid)."""
    if len(emb) < 2:
        return None
    e = _normalize(emb)
    centroid = e.mean(axis=0)
    cn = np.linalg.norm(centroid)
    if cn == 0:
        return None
    centroid = centroid / cn
    return float(np.clip(np.mean(e @ centroid), 0.0, 1.0))


async def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL required\n")
        return 2
    eng = create_async_engine(dsn)
    print("# L1 intrinsic — REAL embedding-cosine (ekimetrics SD + CC), stored vectors\n")
    print("| bot | doc | leaves | SD (distinct↑) | CC (fits-doc↑) |")
    print("| --- | --- | ---: | ---: | ---: |")
    try:
        async with eng.connect() as c:
            docs = list(await c.execute(text(
                """SELECT b.bot_id, d.id, d.tool_name FROM documents d
                   JOIN bots b ON b.id=d.record_bot_id
                   WHERE b.bot_id = ANY(:b) AND d.deleted_at IS NULL AND d.state='active'
                   ORDER BY b.bot_id, d.tool_name"""), {"b": list(DEFAULT_BOTS)}))
            by_bot: dict[str, list[tuple[float, float]]] = {}
            for bot_id, doc_id, tool in docs:
                rows = list(await c.execute(text(
                    """SELECT embedding::text, COALESCE(chunk_chars, length(content)) ch
                       FROM document_chunks
                       WHERE record_document_id=:d AND embedding IS NOT NULL
                       ORDER BY chunk_index"""), {"d": doc_id}))
                if len(rows) < 2:
                    continue
                emb = np.vstack([_parse_vec(r[0]) for r in rows])
                lengths = np.array([float(r[1] or 1) for r in rows], dtype=np.float32)
                sd = _semantic_dissimilarity(emb, lengths)
                cc = _contextual_coherence(emb)
                if sd is None or cc is None:
                    continue
                by_bot.setdefault(bot_id, []).append((sd, cc))
                print(f"| {bot_id} | {tool} | {len(rows)} | {sd:.3f} | {cc:.3f} |")
            print("\n## Per-bot mean (REAL embedding)")
            print("| bot | docs | SD | CC |")
            print("| --- | ---: | ---: | ---: |")
            for bot_id, vals in by_bot.items():
                sd_m = float(np.mean([v[0] for v in vals]))
                cc_m = float(np.mean([v[1] for v in vals]))
                print(f"| {bot_id} | {len(vals)} | {sd_m:.3f} | {cc_m:.3f} |")
    finally:
        await eng.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
