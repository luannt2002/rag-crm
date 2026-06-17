"""Insert helper that maintains the ``audit_log`` hash chain on write.

Every ``AuditLogModel`` INSERT MUST go through ``insert_audit_row`` so
the ``row_hash`` column is populated from the prior row's hash. The DB
trigger ``audit_log_immutable_trigger`` (alembic 010g) blocks UPDATE
and DELETE, so once a row is written it is part of the chain forever.

Concurrency: we use ``SELECT ... ORDER BY created_at DESC, id DESC LIMIT 1
FOR UPDATE`` to serialise readers of the chain tail. Concurrent writers
contend on the most recent row's row-lock; the loser blocks until the
winner commits, then reads the new tail. This pattern preserves chain
linearity without a separate table-level lock.

The session is provided by the caller (so the chain insert participates
in the caller's transaction). Caller is responsible for ``await session
.commit()``.

JSON-vs-SQL-null subtlety: SQLAlchemy's JSONB type maps Python ``None``
to a JSONB ``null`` literal (NOT SQL NULL); when read back as
``::text`` Postgres returns the string ``'null'``. The hasher therefore
treats a Python ``None`` JSON value as the canonical text ``"null"``
so the writer-side hash matches what the verifier sees on re-read.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ragbot.application.services.audit_log_hasher import compute_audit_row_hash
from ragbot.infrastructure.db.models import AuditLogModel


async def insert_audit_row(
    session: AsyncSession,
    *,
    record_tenant_id: _uuid.UUID | None,
    workspace_id: str,
    actor_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
) -> AuditLogModel:
    """Insert an ``audit_log`` row with computed ``row_hash``.

    The function does NOT commit — the caller owns the transaction.
    Returns the constructed ``AuditLogModel`` (already added to the
    session) so callers can ``await session.flush()`` to populate ``id``
    if they need it pre-commit.
    """
    # 1. Lock & read the current tail hash (per-tenant scope when one is
    # provided; tenant-less rows form a separate chain segment for
    # platform-level events).
    if record_tenant_id is not None:
        tail_sql = (
            "SELECT row_hash FROM audit_log "
            "WHERE record_tenant_id = :tid "
            "ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE"
        )
        params: dict[str, Any] = {"tid": record_tenant_id}
    else:
        tail_sql = (
            "SELECT row_hash FROM audit_log "
            "WHERE record_tenant_id IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE"
        )
        params = {}

    tail_row = (await session.execute(text(tail_sql), params)).first()
    prev_hash = (tail_row.row_hash if tail_row else "") or ""

    # 2. Compute hash with NOW() as the timestamp — must match the
    # ``created_at`` server_default. We pre-stamp ``created_at`` so the
    # ORM uses the same value the hasher saw (not a later NOW()).
    now = datetime.now(tz=timezone.utc)

    row_hash = compute_audit_row_hash(
        prev_hash=prev_hash,
        record_tenant_id=record_tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=before_json,
        after_json=after_json,
        reason=reason,
        trace_id=trace_id,
        created_at=now,
    )

    # 3. Insert with explicit created_at + row_hash so the stored bytes
    # match the hasher's input bit-for-bit.
    model = AuditLogModel(
        record_tenant_id=record_tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=before_json,
        after_json=after_json,
        reason=reason,
        trace_id=trace_id,
        created_at=now,
        row_hash=row_hash,
    )
    session.add(model)
    return model


__all__ = ["insert_audit_row"]
