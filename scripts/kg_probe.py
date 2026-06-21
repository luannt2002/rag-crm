#!/usr/bin/env python3
"""kg_probe.py — measure-FIRST dry-run for the dormant Knowledge Graph.

The KG is empty because system_config.graph_rag_default_mode="disabled". Before
flipping that switch + paying for a full backfill, JUDGE the extraction quality
on a small sample of EXISTING chunks: extract triples, print them, store NOTHING.

This produces the evidence the KG-at-ingest gate needs (rule #0: don't enable a
feature whose output you haven't looked at). No DB writes, no config change — it
reads document_chunks.content and runs the real KnowledgeGraphService.extract.

Usage:
  python scripts/kg_probe.py --bot chinh-sach-xe --n 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ragbot.infrastructure.graph.knowledge_graph import KnowledgeGraphService  # noqa: E402
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_LLM_MAX_TOKENS,
)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if "://" in raw and "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = scheme.split("+", 1)[0] + "://" + rest
    if not raw:
        raise SystemExit("DATABASE_URL required")
    return raw


def _fetch(bot: str, n: int) -> tuple[str, list[tuple[int, str]]]:
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    # extraction model from system_config (same fallback chain as ingest_core)
    cur.execute(
        "SELECT value FROM system_config WHERE key = 'graph_rag_entity_extraction_model'"
    )
    row = cur.fetchone()
    model = (row[0] if row and row[0] else "").strip()
    if not model:
        cur.execute("SELECT value FROM system_config WHERE key = 'llm_default_model'")
        row = cur.fetchone()
        model = (row[0] if row and row[0] else "").strip()
    # a spread of real chunks for this bot (deterministic order, skip tiny ones)
    cur.execute(
        """
        SELECT dc.chunk_index, dc.content
        FROM document_chunks dc JOIN bots b ON dc.record_bot_id = b.id
        WHERE b.bot_id = %s AND char_length(dc.content) > 80
        ORDER BY md5(dc.id::text)
        LIMIT %s
        """,
        (bot, n),
    )
    chunks = [(int(i), c) for i, c in cur.fetchall()]
    cur.close()
    conn.close()
    if not model:
        raise SystemExit("no extraction model resolvable from system_config")
    return model, chunks


class _MiniLLM:
    """Minimal LLM adapter — mirrors ingest_core._extract_graph_entities."""

    def __init__(self, model: str) -> None:
        self._model = model

    async def complete(self, _cfg: Any, messages: list[dict], **kwargs: Any) -> dict:
        import litellm as _litellm

        resp = await _litellm.acompletion(
            model=self._model,
            messages=messages,
            temperature=kwargs.get("temperature", 0.0),
            max_tokens=kwargs.get("max_tokens", DEFAULT_LLM_MAX_TOKENS),
            timeout=DEFAULT_HTTP_TIMEOUT_S,
        )
        choice = resp.choices[0]
        return {"text": choice.message.content or "", "finish_reason": choice.finish_reason or "stop"}


class _MiniResolver:
    async def resolve_runtime(self, **_kw: Any) -> None:
        return None


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kg_probe")
    p.add_argument("--bot", required=True)
    p.add_argument("--n", type=int, default=10)
    a = p.parse_args(argv)

    model, chunks = _fetch(a.bot, a.n)
    print(f"DRY-RUN KG probe · bot={a.bot} · model={model} · {len(chunks)} sample chunks")
    print("(extracts triples, stores NOTHING — judge quality before flipping the switch)\n")

    svc = KnowledgeGraphService()
    llm, resolver = _MiniLLM(model), _MiniResolver()
    total = 0
    empties = 0
    for idx, text in chunks:
        triples = await svc.extract_entities(
            chunk_content=text,
            document_name=f"{a.bot} chunk#{idx}",
            llm=llm,
            model_resolver=resolver,
            max_triples=10,
        )
        if not triples:
            empties += 1
            print(f"  chunk#{idx}: (0 triples) — {text[:70].strip()!r}…")
            continue
        total += len(triples)
        print(f"  chunk#{idx}: {len(triples)} triples — {text[:50].strip()!r}…")
        for t in triples:
            print(f"      ({t['subject']}) —[{t['relation']}]→ ({t['object']})")
    print(
        f"\nSUMMARY: {total} triples from {len(chunks)} chunks "
        f"({empties} empty) · avg {total / max(1, len(chunks)):.1f}/chunk"
    )
    print("Judge: are these triples FAITHFUL + useful for multi-hop? If noise → don't enable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
