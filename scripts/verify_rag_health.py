#!/usr/bin/env python3
"""Read-only RAG health audit — invariants that actually matter, with CORRECT metrics.

Born from a 2026-06-19 false alarm: a raw ``embedding IS NULL`` count looked like a
catastrophic 16% coverage gap, but every NULL chunk was a small-to-big PARENT
(intentionally un-embedded; only leaf children carry vectors). The real invariant
is ``null_non_parent == 0`` (a LEAF with no vector), which the ingest gate already
enforces. This script encodes the correct checks so the red herring can't recur.

Pure SELECT + Redis reads. No writes, no Container boot. Exit 0 if no FAIL.

Usage: python scripts/verify_rag_health.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_PASS, _WARN, _FAIL = "PASS", "WARN", "FAIL"


def _line(status: str, name: str, detail: str) -> tuple[str, str, str]:
    icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[status]
    print(f"  {icon} [{status}] {name}: {detail}")
    return status, name, detail


async def _db_checks(results: list) -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    c = await eng.connect()
    c = await c.execution_options(isolation_level="AUTOCOMMIT")
    try:
        # 1. Leaf embedding coverage — the invariant the ingest gate enforces.
        rows = list(
            await c.execute(
                text(
                    """
                    SELECT b.bot_id,
                      count(*) FILTER (
                        WHERE dc.embedding IS NULL AND NOT EXISTS (
                          SELECT 1 FROM document_chunks ch
                          WHERE ch.parent_chunk_id = dc.id)
                      ) AS null_leaf,
                      count(*) FILTER (WHERE dc.embedding IS NULL) AS null_total
                    FROM document_chunks dc JOIN bots b ON b.id = dc.record_bot_id
                    GROUP BY b.bot_id ORDER BY b.bot_id
                    """
                )
            )
        )
        bad = [r for r in rows if int(r[1]) > 0]
        if bad:
            results.append(
                _line(
                    _FAIL,
                    "leaf_embedding_coverage",
                    "; ".join(f"{r[0]}: {r[1]} leaf chunks missing vector" for r in bad),
                )
            )
        else:
            note = ", ".join(f"{r[0]}({r[2]} parent-NULL ok)" for r in rows)
            results.append(_line(_PASS, "leaf_embedding_coverage", f"0 leaf NULL — {note}"))

        # 2. active docs must have zero leaf NULL (state/coverage consistency).
        bad_docs = (
            await c.execute(
                text(
                    """
                    SELECT count(DISTINCT d.id) FROM documents d
                    JOIN document_chunks dc ON dc.record_document_id = d.id
                    WHERE d.state = 'active' AND dc.embedding IS NULL
                      AND NOT EXISTS (SELECT 1 FROM document_chunks ch
                                      WHERE ch.parent_chunk_id = dc.id)
                    """
                )
            )
        ).scalar()
        results.append(
            _line(
                _FAIL if bad_docs else _PASS,
                "active_doc_consistency",
                f"{bad_docs} active docs with leaf NULL"
                if bad_docs
                else "all active docs fully embedded at leaf level",
            )
        )

        # 3. config provider/model coherence (the cohere-on-jina drift class).
        cfg = {
            r[0]: r[1]
            for r in await c.execute(
                text(
                    "SELECT key, value FROM system_config WHERE key IN "
                    "('reranker_provider','reranker_model','embedding_provider',"
                    "'embedding_model','embedding_dimension')"
                )
            )
        }

        def _coherent(provider: str, model: str) -> bool:
            p, m = (provider or "").lower(), (model or "").lower()
            return p in m or (p in {"jina", "jina_ai"} and m.startswith("jina"))

        for prov_k, mod_k in [
            ("reranker_provider", "reranker_model"),
            ("embedding_provider", "embedding_model"),
        ]:
            ok = _coherent(cfg.get(prov_k, ""), cfg.get(mod_k, ""))
            results.append(
                _line(
                    _PASS if ok else _FAIL,
                    f"config_{mod_k}",
                    f"provider={cfg.get(prov_k)} model={cfg.get(mod_k)}"
                    + ("" if ok else " — MODEL DOES NOT MATCH PROVIDER"),
                )
            )

        # 4. config embedding_dimension == actual stored leaf-vector dimension.
        real_dim = (
            await c.execute(
                text("SELECT vector_dims(embedding) FROM document_chunks "
                     "WHERE embedding IS NOT NULL LIMIT 1")
            )
        ).scalar()
        cfg_dim = int(cfg.get("embedding_dimension") or 0)
        results.append(
            _line(
                _PASS if cfg_dim == real_dim else _FAIL,
                "embedding_dimension",
                f"config={cfg_dim} stored={real_dim}"
                + ("" if cfg_dim == real_dim else " — MISMATCH (query vec will not match column)"),
            )
        )

        # 5. RLS enforcement — runtime role must NOT bypass RLS.
        role = (await c.execute(text("SELECT current_user"))).scalar()
        bypass = (
            await c.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar()
        has_system_dsn = bool(os.environ.get("DATABASE_URL_SYSTEM"))
        results.append(
            _line(
                _WARN if bypass else _PASS,
                "rls_enforcement",
                f"runtime role={role} bypassrls={bypass} "
                f"DATABASE_URL_SYSTEM={'set' if has_system_dsn else 'unset'}"
                + (" — RLS INERT (isolation relies on app-layer filter only)" if bypass else ""),
            )
        )

        # 6. guardrail rules seeded?
        gr = (await c.execute(text("SELECT count(*) FROM guardrail_rules"))).scalar()
        results.append(
            _line(_WARN if gr == 0 else _PASS, "guardrail_rules", f"{gr} rules")
        )
    finally:
        await c.close()
        await eng.dispose()


async def _redis_checks(results: list) -> None:
    try:
        import redis.asyncio as redis
    except ImportError:
        results.append(_line(_WARN, "redis", "redis lib unavailable — skipped"))
        return
    url = os.environ.get("REDIS_URL") or "redis://localhost:6380/0"
    r = redis.from_url(url)
    try:
        # 7. orphan streams: a stream with messages but no consumer group is a black hole.
        for subject in ["document.upload_stream.v1", "document.uploaded.v1"]:
            try:
                xlen = await r.xlen(subject)
            except Exception:  # noqa: BLE001 — missing stream key = 0
                xlen = 0
            try:
                groups = await r.xinfo_groups(subject)
            except Exception:  # noqa: BLE001 — no groups
                groups = []
            if xlen > 0 and not groups:
                results.append(
                    _line(_FAIL, f"stream:{subject}", f"{xlen} msgs, NO consumer group — orphan/black-hole")
                )
            else:
                results.append(
                    _line(_PASS, f"stream:{subject}", f"{xlen} msgs, {len(groups)} group(s)")
                )
        # 8. DLQ depth.
        for dlq in ["document.uploaded.v1:dlq"]:
            try:
                n = await r.xlen(dlq)
            except Exception:  # noqa: BLE001
                n = 0
            results.append(
                _line(_WARN if n else _PASS, f"dlq:{dlq}", f"{n} dead-letter entries")
            )
    finally:
        await r.aclose()


async def main() -> int:
    print("=== RAG health audit (read-only) ===")
    results: list = []
    await _db_checks(results)
    await _redis_checks(results)
    n_fail = sum(1 for s, _, _ in results if s == _FAIL)
    n_warn = sum(1 for s, _, _ in results if s == _WARN)
    print(f"\n  SUMMARY: {len(results)} checks — {n_fail} FAIL, {n_warn} WARN, "
          f"{len(results) - n_fail - n_warn} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
