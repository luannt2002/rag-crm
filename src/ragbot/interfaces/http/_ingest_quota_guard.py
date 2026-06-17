"""Shared ingest-quota enforcement for the upload routes.

Closes P2-H 🐛 IQ-1: ``IngestQuotaService.check_and_increment`` existed and
was correct, but no production upload route called it — the per-tenant
daily document gate ran only on a demo route, so the real ``POST
/documents/create`` and ``POST /documents/stream-upload`` paths let any
tenant flood the worker pipeline (noisy-neighbour → HNSW bloat → shared
embed-budget burn).

Both routes now enforce the gate through this single helper (one flow, one
place to evolve when the workspace-tier cascade lands — ADR-W2-D2 §c). The
check runs inside a tenant-scoped session so the ``quotas`` row lock is
RLS-bound to the caller; ``QuotaExceeded`` propagates to the registered
exception handler (→ HTTP 429).
"""

from __future__ import annotations

from uuid import UUID

import structlog

from ragbot.infrastructure.db.engine import session_with_tenant

logger = structlog.get_logger(__name__)


async def enforce_ingest_quota(
    container: object,
    *,
    record_tenant_id: UUID,
    workspace_id: str,
    increment_by: int = 1,
) -> tuple[int, int]:
    """Charge the tenant's daily ingest quota before a document is queued.

    Opens a short tenant-scoped transaction (RLS-bound) and runs the
    atomic SELECT FOR UPDATE + increment. Returns ``(new_count, limit)``
    so the caller can echo remaining headroom.

    Raises:
        QuotaExceeded: daily cap reached, or the tenant's quota row is
            missing (mis-provisioned tenant — fail loud). Both surface as
            HTTP 429 via the registered handler.

    @param container: the DI container (``app.state.container``).
    @param record_tenant_id: internal tenant UUID PK (from JWT state).
    @param workspace_id: resolved workspace slug — carried for the
        forthcoming workspace-tier cascade (ADR-W2-D2 §c); today the gate
        is tenant-scoped and the slug is recorded for observability only.
    @param increment_by: 1 for a single upload, ``len(documents)`` for a
        batch so the whole batch is admitted or rejected atomically.
    """
    svc = container.ingest_quota_service()
    factory = container.session_factory()
    async with session_with_tenant(
        factory, record_tenant_id=record_tenant_id,
    ) as session:
        new_count, limit = await svc.check_and_increment(
            session,
            record_tenant_id=record_tenant_id,
            increment_by=increment_by,
        )
        await session.commit()
    logger.debug(
        "ingest_quota_charged",
        record_tenant_id=str(record_tenant_id),
        workspace_id=workspace_id,
        new_count=new_count,
        limit=limit,
    )
    return new_count, limit
