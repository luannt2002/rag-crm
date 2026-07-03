"""Build the persistable served-chunk list for an answered turn.

Truth-audit verification requirement (owner, 2026-07-03): every stored answer
must be auditable against EXACTLY what the LLM saw — question, answer, and the
served chunks — without needing debug mode at ask-time. This helper normalizes
the pipeline's chunk dicts into a compact, capped, JSON-safe list persisted to
``chat_histories.served_chunks`` (and reusable by any other transport).

Pure + domain-neutral: no DB, no I/O, shape-only field lifting.
"""
from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    SERVED_CHUNKS_PERSIST_MAX_CHARS,
    SERVED_CHUNKS_PERSIST_MAX_ITEMS,
)


def build_served_chunks(chunks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize + cap the chunks the LLM actually saw for persistence.

    Keeps: chunk_id, score (first of score/rerank_score/rrf_score), source,
    document_name, content head (capped). Empty/None input → [] (a refusal or
    zero-context answer persists an empty list, distinguishable from NULL =
    row written before this feature).
    """
    out: list[dict[str, Any]] = []
    for ch in (chunks or [])[:SERVED_CHUNKS_PERSIST_MAX_ITEMS]:
        if not isinstance(ch, dict):
            continue
        score = ch.get("score")
        if score is None:
            score = ch.get("rerank_score")
        if score is None:
            score = ch.get("rrf_score")
        text = str(ch.get("content") or ch.get("text") or "")
        out.append({
            "chunk_id": str(ch.get("chunk_id") or ch.get("id") or ""),
            "score": float(score) if score is not None else None,
            "source": str(ch.get("source") or ""),
            "document_name": str(ch.get("document_name") or ""),
            "content": text[:SERVED_CHUNKS_PERSIST_MAX_CHARS],
        })
    return out


__all__ = ["build_served_chunks"]
