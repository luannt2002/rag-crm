#!/usr/bin/env python3
"""[T1-Smartness] Backfill document_chunks.chunk_context for existing NULL rows.

Populates the ``chunk_context`` column (alembic 010l) for chunks that were
ingested before Contextual Retrieval was enabled. Designed to be safe to
re-run: already-populated chunks (non-NULL ``chunk_context``) are skipped.

USAGE
-----
    # Dry-run — print what would change, no DB writes
    python scripts/backfill_chunk_context.py --dry-run

    # Backfill up to 10 docs (smoke test)
    python scripts/backfill_chunk_context.py --limit 10

    # Backfill a specific bot only
    python scripts/backfill_chunk_context.py --bot-id my-support-bot

    # Full production backfill
    python scripts/backfill_chunk_context.py

EXIT CODES
----------
    0   completed (with or without skips)
    1   DB connection failure or LLMChunkContextProvider import error

NOTES
-----
- Uses psycopg2 (sync) for bulk SELECT/UPDATE — async is overkill here.
- LLMChunkContextProvider (async) is run via asyncio.run() per-doc.
  ModelResolverService + DynamicLiteLLMRouter are built once from env vars
  using the same pattern as bootstrap.py.
- Commits per-doc (atomic per-document rollback on per-doc failure).
- Per-doc errors are logged and the loop continues — the whole run does NOT abort.
- Cost guard: docs longer than DEFAULT_CR_MAX_DOC_CHARS are skipped.
- Empty-string context results from the provider are not written (leave NULL).
- LLMChunkContextProvider must be importable at runtime; if not, the script
  aborts before touching the DB with a clear ImportError message.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

# ---------------------------------------------------------------------------
# Make ``ragbot`` package importable when invoked directly from repo root.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# ---------------------------------------------------------------------------
# Load .env before importing ragbot (mirrors preflight_check.py pattern).
# dotenv is optional — env vars may already be exported (CI, Docker).
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # rely on already-exported env vars


# ---------------------------------------------------------------------------
# Import LLMChunkContextProvider — fail loudly if adapter not yet shipped.
# ImportError surfaces BEFORE any DB connection so the operator sees a clear
# message.
# ---------------------------------------------------------------------------
try:
    from ragbot.infrastructure.llm.llm_chunk_context_provider import (
        LLMChunkContextProvider,
    )
except ImportError as _import_err:
    print(
        "ERROR: ragbot.infrastructure.llm.llm_chunk_context_provider could not be "
        "imported. The LLMChunkContextProvider adapter has not been shipped yet.\n"
        f"Detail: {_import_err}",
        file=sys.stderr,
    )
    raise SystemExit(1) from _import_err

from ragbot.application.services.chunk_context_enricher import ChunkContextEnricher
from ragbot.shared.constants import (
    DEFAULT_CHUNK_CONTEXT_MAX_TOKENS,
    DEFAULT_CR_MAX_DOC_CHARS,
)


# ---------------------------------------------------------------------------
# DB helpers — psycopg2 sync for bulk SELECT / UPDATE.
# ---------------------------------------------------------------------------

def _open_sync_connection():  # type: ignore[return]
    """Return a psycopg2 connection from DATABASE_URL_SYNC / DATABASE_URL."""
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            "ERROR: psycopg2 is not installed. Install it with:\n"
            "  pip install psycopg2-binary",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    dsn = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not dsn:
        print(
            "ERROR: DB env missing — set DATABASE_URL_SYNC (or DATABASE_URL) "
            "before running this script.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    normalized = (
        dsn.replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )
    u = urlparse(normalized)
    try:
        import psycopg2  # type: ignore[import-not-found]  # noqa: F811
        conn = psycopg2.connect(
            host=u.hostname,
            port=u.port or 5432,
            user=u.username,
            password=u.password,
            dbname=(u.path or "/").lstrip("/"),
            connect_timeout=10,
        )
    except Exception as exc:
        print(
            f"ERROR: Cannot connect to database: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    return conn


# ---------------------------------------------------------------------------
# Async provider construction — mirrors bootstrap.py wiring.
# ---------------------------------------------------------------------------

async def _build_provider(
    record_tenant_id: UUID,
    record_bot_id: UUID,
) -> LLMChunkContextProvider:
    """Build LLMChunkContextProvider with minimal async deps from env vars."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from ragbot.infrastructure.repositories.ai_config_repository import (
        SqlAlchemyAIConfigRepository,
    )
    from ragbot.infrastructure.cache.redis_cache import (
        RedisCache,
        create_redis_client,
    )
    from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
    from ragbot.application.services.model_resolver import ModelResolverService
    from ragbot.shared.clock import SystemClock

    # Async DB engine — use DATABASE_URL (asyncpg driver) for the ORM layer.
    db_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL env var required for async provider build.")
    # Ensure asyncpg driver prefix.
    async_url = (
        db_url.replace("postgresql://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(async_url, pool_size=2, max_overflow=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    # Redis cache.
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_client = create_redis_client(redis_url)
    cache = RedisCache(redis_client)

    ai_config_repo = SqlAlchemyAIConfigRepository(session_factory)
    clock = SystemClock()
    model_resolver = ModelResolverService(
        repo=ai_config_repo,
        cache=cache,
        clock=clock,
    )

    llm = DynamicLiteLLMRouter(
        ai_config_repo=ai_config_repo,
        redis_client=redis_client,
        token_meter=None,  # offline backfill — token metering not required
    )

    return LLMChunkContextProvider(
        llm=llm,
        model_resolver=model_resolver,
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
    )


async def _enrich_doc(
    record_tenant_id: UUID,
    record_bot_id: UUID,
    doc_content: str,
    chunk_texts: list[str],
) -> list[str]:
    """Build provider for this doc and run ChunkContextEnricher."""
    provider = await _build_provider(record_tenant_id, record_bot_id)
    enricher = ChunkContextEnricher(
        provider=provider,
        max_context_tokens=DEFAULT_CHUNK_CONTEXT_MAX_TOKENS,
        max_doc_chars=DEFAULT_CR_MAX_DOC_CHARS,
    )
    return await enricher.generate_contexts(doc_content, chunk_texts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill document_chunks.chunk_context for NULL rows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be updated without executing any DB writes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most N documents (0 = no limit).",
    )
    parser.add_argument(
        "--bot-id",
        default=None,
        metavar="SLUG",
        help="Only backfill chunks belonging to this bot_id slug.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def run_backfill(args: argparse.Namespace) -> int:
    """Execute the backfill. Returns 0 on success, 1 on fatal error."""
    dry_run: bool = args.dry_run
    limit: int = args.limit
    bot_id_filter: str | None = args.bot_id

    print(
        f"[backfill_chunk_context] starting "
        f"dry_run={dry_run} limit={limit or 'none'} bot_id={bot_id_filter or 'all'}"
    )

    conn = _open_sync_connection()
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # 1. Fetch documents that have at least one NULL chunk_context.
    # ------------------------------------------------------------------
    if bot_id_filter:
        cur.execute(
            """
            SELECT DISTINCT d.id, d.record_bot_id, d.record_tenant_id, d.raw_content
            FROM documents d
            JOIN document_chunks dc ON dc.record_document_id = d.id
            JOIN bots b ON b.id = d.record_bot_id
            WHERE d.deleted_at IS NULL
              AND dc.chunk_context IS NULL
              AND b.bot_id = %s
            ORDER BY d.id
            """,
            (bot_id_filter,),
        )
    else:
        cur.execute(
            """
            SELECT DISTINCT d.id, d.record_bot_id, d.record_tenant_id, d.raw_content
            FROM documents d
            JOIN document_chunks dc ON dc.record_document_id = d.id
            WHERE d.deleted_at IS NULL
              AND dc.chunk_context IS NULL
            ORDER BY d.id
            """
        )

    all_docs = cur.fetchall()

    if limit > 0:
        all_docs = all_docs[:limit]

    total_docs = len(all_docs)
    if total_docs == 0:
        print(
            "[backfill_chunk_context] no documents with NULL chunk_context found"
            " — nothing to do."
        )
        conn.close()
        return 0

    print(f"[backfill_chunk_context] {total_docs} document(s) to process.")

    # ------------------------------------------------------------------
    # 2. Per-doc loop
    # ------------------------------------------------------------------
    total_chunks_populated = 0
    total_chunks_skipped_empty = 0
    total_docs_skipped_too_long = 0
    total_docs_errored = 0
    t_start = time.monotonic()

    for doc_idx, (doc_id, record_bot_id, record_tenant_id, doc_content) in enumerate(
        all_docs, start=1
    ):
        doc_id_str = str(doc_id)
        record_bot_id_uuid = UUID(str(record_bot_id))
        record_tenant_id_uuid = UUID(str(record_tenant_id))

        # Fetch NULL chunks for this document.
        cur.execute(
            """
            SELECT id, chunk_index, content
            FROM document_chunks
            WHERE record_document_id = %s
              AND chunk_context IS NULL
            ORDER BY chunk_index
            """,
            (doc_id,),
        )
        chunks = cur.fetchall()  # list of (id, chunk_index, content)

        if not chunks:
            print(
                f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
                "chunks=0 (already backfilled) — skip"
            )
            continue

        n_chunks = len(chunks)
        doc_content_str = doc_content or ""
        doc_content_len = len(doc_content_str)

        # Cost guard — matches ChunkContextEnricher internal logic.
        if doc_content_len > DEFAULT_CR_MAX_DOC_CHARS:
            print(
                f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
                f"chunks={n_chunks} doc_chars={doc_content_len} "
                f"> DEFAULT_CR_MAX_DOC_CHARS={DEFAULT_CR_MAX_DOC_CHARS} — SKIP (cost guard)"
            )
            total_docs_skipped_too_long += 1
            continue

        chunk_texts = [c[2] or "" for c in chunks]

        # Dry-run: print intent without calling LLM or writing DB.
        if dry_run:
            print(
                f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
                f"chunks={n_chunks} doc_chars={doc_content_len} "
                "[DRY-RUN] would generate contexts"
            )
            continue

        # ------------------------------------------------------------------
        # Generate contexts via LLM provider.
        # ------------------------------------------------------------------
        try:
            contexts: list[str] = asyncio.run(
                _enrich_doc(
                    record_tenant_id=record_tenant_id_uuid,
                    record_bot_id=record_bot_id_uuid,
                    doc_content=doc_content_str,
                    chunk_texts=chunk_texts,
                )
            )
        except Exception as exc:
            print(
                f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
                f"ERROR generating contexts: {type(exc).__name__}: {exc} — skip"
            )
            total_docs_errored += 1
            continue

        # ------------------------------------------------------------------
        # Write results — per-doc transaction for atomic per-doc rollback.
        # ------------------------------------------------------------------
        n_populated = 0
        n_empty = 0
        try:
            for (chunk_id, _chunk_index, _chunk_text), context in zip(chunks, contexts):
                if not context:
                    # Provider returned empty — leave NULL (do not write empty string).
                    n_empty += 1
                    total_chunks_skipped_empty += 1
                    continue
                cur.execute(
                    "UPDATE document_chunks SET chunk_context = %s WHERE id = %s",
                    (context, chunk_id),
                )
                n_populated += 1

            conn.commit()
            total_chunks_populated += n_populated
        except Exception as exc:
            conn.rollback()
            print(
                f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
                f"ERROR writing to DB: {type(exc).__name__}: {exc} — rolled back, skip"
            )
            total_docs_errored += 1
            continue

        print(
            f"[doc {doc_idx}/{total_docs}] doc_id={doc_id_str} "
            f"chunks={n_chunks} populated={n_populated} skipped_empty={n_empty}"
        )

    # ------------------------------------------------------------------
    # Final report
    # ------------------------------------------------------------------
    elapsed = time.monotonic() - t_start
    docs_processed = total_docs - total_docs_skipped_too_long - total_docs_errored
    print(
        f"\n[backfill_chunk_context] DONE in {elapsed:.1f}s\n"
        f"  docs_processed:         {docs_processed}\n"
        f"  docs_skipped_too_long:  {total_docs_skipped_too_long}\n"
        f"  docs_errored:           {total_docs_errored}\n"
        f"  chunks_populated:       {total_chunks_populated}\n"
        f"  chunks_skipped_empty:   {total_chunks_skipped_empty}\n"
        f"  dry_run:                {dry_run}"
    )

    conn.close()
    return 0


def main() -> int:
    args = _parse_args()
    return run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(main())
