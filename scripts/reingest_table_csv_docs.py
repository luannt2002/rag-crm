"""Re-ingest documents that need new chunking strategy applied.

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 4 ops.

After Phase 5 flip ``table_csv_emit_header_footer_chunks_enabled=true``,
existing documents already in DB still carry chunks emitted under the
old ``_chunk_table_csv`` splitter (row-only, no header/footer). This
script reuses the application-layer ``RechunkDocumentUseCase`` for each
target document so the rest of the pipeline (delete existing chunks +
DocumentUploaded outbox event + worker rechunk + re-embed) runs through
the exact same path as the live HTTP ``/documents/rechunk`` endpoint.

Scope safety: ONLY operates on documents matching the (record_tenant_id,
bot_id) tuple supplied via CLI. Never global. Never cross-tenant.

Usage:
    # Dry-run: list targets without enqueuing
    python scripts/reingest_table_csv_docs.py \\
        --tenant=c2f66cb2-9911-5d34-a46e-a4a6da068e23 \\
        --bot=test-spa-id --dry-run

    # Apply: enqueue re-ingest jobs
    python scripts/reingest_table_csv_docs.py \\
        --tenant=c2f66cb2-9911-5d34-a46e-a4a6da068e23 \\
        --bot=test-spa-id --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

import psycopg2
import psycopg2.extras
import structlog

logger = structlog.get_logger(__name__)


def _resolve_dsn() -> str:
    import os
    raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = scheme.split("+", 1)[0] + "://" + rest
    if not raw:
        raise RuntimeError("DATABASE_URL_SYNC or DATABASE_URL env var required")
    return raw


def _list_targets(
    record_tenant_id: UUID, bot_id: str,
) -> list[dict]:
    """Return documents in scope of (tenant, bot) that have ≥1 chunk."""
    dsn = _resolve_dsn()
    with psycopg2.connect(dsn) as conn:
        conn.set_session(autocommit=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT d.id AS document_id, d.document_name, d.source_url,
                       d.tool_name, d.mime_type,
                       d.record_bot_id, d.workspace_id, d.record_tenant_id,
                       COUNT(dc.id) AS n_chunks
                FROM documents d
                JOIN bots b ON b.id = d.record_bot_id
                LEFT JOIN document_chunks dc ON dc.record_document_id = d.id
                WHERE d.record_tenant_id = %s
                  AND b.bot_id = %s
                  AND d.source_url IS NOT NULL
                GROUP BY d.id
                ORDER BY d.document_name
                """,
                (str(record_tenant_id), bot_id),
            )
            return [dict(r) for r in cur.fetchall()]


async def _rechunk_one(target: dict, container) -> bool:
    """Run RechunkDocumentUseCase.execute_by_document_id for one doc.

    Bug #2 fix (260525) — switched from source_url-keyed rechunk to
    document_id-keyed rechunk. Bots with multiple documents sharing a
    single source_url (Google Sheets workbook tabs, etc.) now rechunk
    the exact targeted doc instead of an arbitrary first match.
    """
    from ragbot.application.commands.document_commands import (
        RechunkByDocumentIdCommand,
    )
    from ragbot.config.logging import bind_request_context
    from ragbot.shared.types import TenantId, TraceId

    # The UnitOfWork reads tenant_id_ctx (a contextvar set by
    # TenantContextMiddleware in the HTTP path). For an ops CLI we set
    # it manually per-document so RLS-scoped queries inside the UoW work.
    trace_id = f"reingest-{target['document_id']}"
    bind_request_context(
        trace_id=trace_id,
        record_tenant_id=target["record_tenant_id"],
    )

    uc = container.rechunk_document_uc()
    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=TenantId(UUID(str(target["record_tenant_id"]))),
        record_bot_id=UUID(str(target["record_bot_id"])),
        workspace_id=str(target["workspace_id"]),
        document_id=UUID(str(target["document_id"])),
        trace_id=TraceId(trace_id),
    )
    try:
        result = await uc.execute_by_document_id(cmd)
        print(f"  OK   {target['document_name']:40s} job={result.job_id}")
        return True
    except Exception as exc:
        print(f"  ERR  {target['document_name']:40s} {exc!r}", file=sys.stderr)
        return False


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="record_tenant_id UUID")
    parser.add_argument("--bot", required=True, help="bot_id slug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("ERROR: pass --dry-run or --apply", file=sys.stderr)
        return 1

    try:
        record_tenant = UUID(args.tenant)
    except ValueError:
        print(f"ERROR: invalid tenant UUID {args.tenant!r}", file=sys.stderr)
        return 1

    targets = _list_targets(record_tenant, args.bot)
    print(f"Targets ({len(targets)}):")
    for t in targets:
        print(
            f"  {t['document_name']:40s} | {t['n_chunks']:4d} chunks "
            f"| {t['source_url'][:80]}",
        )

    if args.dry_run:
        print("\n[dry-run] No re-ingest enqueued. Pass --apply to execute.")
        return 0

    if not targets:
        print("Nothing to do.")
        return 0

    print()
    # Share one Container across calls so factories cache + open one
    # engine pool instead of N.
    from ragbot.bootstrap import Container
    container = Container()
    n_ok = 0
    for t in targets:
        if await _rechunk_one(t, container):
            n_ok += 1

    print(f"\nEnqueued {n_ok}/{len(targets)} re-ingest jobs.")
    print("Worker will pick up from outbox → Redis Streams. Watch logs:")
    print("  journalctl -fu ragbot-document-worker.service")
    return 0 if n_ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
