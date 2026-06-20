#!/usr/bin/env python3
"""Runtime proof — canonical DELETE→CREATE re-ingest lifecycle (#3a + #3b).

Drives the REAL ``IngestDocumentUseCase`` + ``DeleteDocumentUseCase`` against
the REAL Postgres (real ``uq_doc_tool`` constraint) + REAL Redis idempotency,
via the DI ``Container`` — no HTTP, no server restart, no worker dependency
(the two bugs live at enqueue/save time, upstream of the worker).

Uses a THROWAWAY ``tool_name`` on an existing bot so the real corpora are
untouched, and hard-purges the probe row in ``finally``.

Asserts:
  #3a — second CREATE after archive does NOT raise IntegrityError, and reuses
        the surviving row's PK (one row for the tool_name, id unchanged).
  #3b — second CREATE reactivates the row (state→DRAFT) instead of
        short-circuiting on the stale 24h source_url Redis key.

Run::  set -a && source .env && set +a && python scripts/verify_reingest_lifecycle.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

PROBE_BOT = "test-spa-id"
PROBE_CHANNEL = "web"
PROBE_NAME = "zzz canary reactivate probe"  # → tool_name slug
PROBE_TOOL = "zzz-canary-reactivate-probe"
# Reuse a small, proven-fetchable source_url (spa-3, 3 chunks). Content is
# irrelevant — we only exercise the create/delete lifecycle, not retrieval.
PROBE_URL = (
    "https://docs.google.com/spreadsheets/d/143GvpDMCEjhNfyAPsybMoJFv0Hca6P"
)


async def _resolve_ids(dsn: str) -> tuple[UUID, UUID, str]:
    eng = create_async_engine(dsn)
    try:
        async with eng.connect() as c:
            row = (await c.execute(text(
                "SELECT id, record_tenant_id, workspace_id FROM bots "
                "WHERE bot_id=:b AND channel_type=:ch"),
                {"b": PROBE_BOT, "ch": PROBE_CHANNEL})).fetchone()
            if row is None:
                raise SystemExit(f"bot {PROBE_BOT}/{PROBE_CHANNEL} not found")
            return row[0], row[1], row[2]
    finally:
        await eng.dispose()


async def _purge_probe(dsn: str, record_bot_id: UUID) -> None:
    eng = create_async_engine(dsn)
    try:
        c = await (await eng.connect()).execution_options(
            isolation_level="AUTOCOMMIT")
        ids = [r[0] for r in await c.execute(text(
            "SELECT id FROM documents WHERE record_bot_id=:b AND tool_name=:t"),
            {"b": record_bot_id, "t": PROBE_TOOL})]
        for did in ids:
            await c.execute(text(
                "DELETE FROM document_chunks WHERE record_document_id=:d"),
                {"d": did})
        await c.execute(text(
            "DELETE FROM documents WHERE record_bot_id=:b AND tool_name=:t"),
            {"b": record_bot_id, "t": PROBE_TOOL})
        await c.close()
        print(f"  [cleanup] purged {len(ids)} probe doc row(s) + chunks")
    finally:
        await eng.dispose()


async def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL required\n")
        return 2

    from ragbot.application.commands.document_commands import (
        DeleteDocumentCommand,
        IngestDocumentCommand,
    )
    from ragbot.bootstrap import Container
    from ragbot.config.logging import bind_request_context
    from ragbot.shared.types import TenantId, TraceId, WorkspaceId

    record_bot_id, record_tenant_id, workspace_id = await _resolve_ids(dsn)
    # The UnitOfWork binds RLS from the request contextvars (normally set by
    # TenantContextMiddleware). Bind them here so the standalone driver mirrors
    # a real request.
    bind_request_context(
        trace_id="verify-reingest-probe",
        record_tenant_id=record_tenant_id,
        workspace_id=workspace_id,
        bot_id=record_bot_id,
    )
    print(f"probe bot={PROBE_BOT}/{PROBE_CHANNEL} record_bot_id={record_bot_id} "
          f"tenant={record_tenant_id} ws={workspace_id!r}")

    # Start from a clean slate in case a prior run left a probe row.
    await _purge_probe(dsn, record_bot_id)

    container = Container()
    ingest_uc = container.ingest_document_uc()
    delete_uc = container.delete_document_uc()
    docs = container.document_repo()

    def _create_cmd() -> IngestDocumentCommand:
        return IngestDocumentCommand(
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=record_bot_id,
            workspace_id=WorkspaceId(workspace_id),
            source_url=PROBE_URL,
            document_name=PROBE_NAME,
            mime_type=None,
            language="vi",
            trace_id=TraceId("verify-reingest-probe"),
        )

    ok = True
    try:
        # ── 1. First CREATE ──────────────────────────────────────────────
        r1 = await ingest_uc.execute(_create_cmd())
        doc1 = await docs.get_by_tool_name(
            record_bot_id, PROBE_TOOL, record_tenant_id=TenantId(record_tenant_id))
        assert doc1 is not None, "first CREATE did not persist a doc row"
        print(f"  [1 CREATE] job={r1.job_id} doc_id={doc1.id} state={doc1.state}")

        # ── 2. Canonical DELETE (archives the row, keeps tool_name) ──────
        await delete_uc.execute(DeleteDocumentCommand(
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=record_bot_id,
            workspace_id=WorkspaceId(workspace_id),
            tool_name=PROBE_TOOL,
            trace_id=TraceId("verify-reingest-probe"),
        ))
        doc_arch = await docs.get_by_tool_name(
            record_bot_id, PROBE_TOOL, record_tenant_id=TenantId(record_tenant_id))
        assert doc_arch is not None, "DELETE removed the row (expected archive)"
        print(f"  [2 DELETE] row survives id={doc_arch.id} state={doc_arch.state}")

        # ── 3. Second CREATE — THE TEST (#3a uq_doc_tool, #3b stale idem) ─
        r2 = await ingest_uc.execute(_create_cmd())  # must NOT raise
        doc2 = await docs.get_by_tool_name(
            record_bot_id, PROBE_TOOL, record_tenant_id=TenantId(record_tenant_id))
        print(f"  [3 RE-CREATE] job={r2.job_id} doc_id={doc2.id} state={doc2.state}")

        # #3a — reactivated in place: SAME PK, exactly one row, no uq collision.
        eng = create_async_engine(dsn)
        async with eng.connect() as c:
            n = (await c.execute(text(
                "SELECT count(*) FROM documents WHERE record_bot_id=:b AND tool_name=:t"),
                {"b": record_bot_id, "t": PROBE_TOOL})).scalar()
        await eng.dispose()

        checks = {
            "#3a no IntegrityError (re-create survived)": True,
            "#3a reused PK (doc2.id == doc1.id)": doc2.id == doc1.id,
            "#3a exactly one row for tool_name": n == 1,
            "#3b reactivated (state DRAFT, not stale-archived)": doc2.state == "DRAFT",
            "#3b new job enqueued (not stale short-circuit)": str(r2.job_id) != str(r1.job_id),
        }
        print("\n  RESULTS:")
        for label, passed in checks.items():
            print(f"    {'✅' if passed else '❌'} {label}")
            ok = ok and passed
    except Exception as exc:  # noqa: BLE001 — top-level probe driver: report + fail loud
        ok = False
        print(f"  ❌ EXCEPTION (likely the bug still present): "
              f"{type(exc).__name__}: {exc}")
    finally:
        await _purge_probe(dsn, record_bot_id)

    print(f"\n{'✅ VERIFIED' if ok else '❌ FAILED'} — canonical re-ingest lifecycle")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
