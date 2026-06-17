"""``audit_log`` hash-chain verifier.

Scans rows in ``(created_at, id)`` order, recomputes each row's expected
``row_hash`` from the prior row's hash + canonical field bytes (same
algorithm as :mod:`ragbot.application.services.audit_log_hasher`), and
reports any row whose stored ``row_hash`` mismatches the recomputed
value.

A mismatch implies either:
  - a row was UPDATEd (bypassing or disabling the
    ``audit_log_immutable_trigger``);
  - a row was DELETEd (the next row's ``prev_hash`` reference is gone);
  - a row was INSERTed retroactively without recomputing the chain.

Both cases break forensic integrity and the verdict surfaces them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.services.audit_log_hasher import compute_audit_row_hash
from ragbot.shared.pagination import page_limit


@dataclass(slots=True)
class AuditChainMismatch:
    """One broken-chain row."""
    row_id: str
    created_at: str
    expected_hash: str
    actual_hash: str


@dataclass(slots=True)
class AuditVerifyResult:
    """Verifier verdict."""
    total_rows_scanned: int
    mismatches: list[AuditChainMismatch] = field(default_factory=list)
    ok: bool = True
    # When the chain is intact, ``last_row_hash`` is the tail digest the
    # next INSERT must feed in as ``prev_hash``. Surfaced for ops dashboards.
    last_row_hash: str | None = None


class AuditVerifier:
    """Scan ``audit_log`` and check the hash chain end-to-end (scoped)."""

    # Per-tenant scoping: ALWAYS filter by ``record_tenant_id``. The
    # chain is *global* on disk (rn ordered across all tenants in the
    # backfill) but for verify endpoints exposed to tenant admins we
    # only show their own rows; we recompute the chain locally over the
    # tenant subset by carrying ``prev_hash`` across the iteration.
    #
    # NOTE: this means a tenant-scoped verify catches tamper within
    # that tenant only. Cross-tenant chain validation is reserved for
    # the platform super-admin endpoint (level 90+).

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def verify_audit_chain(
        self,
        *,
        record_tenant_id: UUID,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AuditVerifyResult:
        """Scan rows for ``record_tenant_id`` (optionally filtered by
        ``since`` on ``created_at``) and return verdict.

        Re-uses ``page_limit`` defaults so large tenants paginate the scan
        across multiple calls when needed.
        """
        scan_limit = page_limit(limit, default=1000, max_limit=10000)
        params: dict[str, Any] = {
            "tid": record_tenant_id,
            "scan_limit": scan_limit,
        }
        where_since = ""
        if since is not None:
            where_since = "AND created_at >= :since"
            params["since"] = since

        # Pull JSON columns as ::text so the verifier sees the *exact*
        # bytes the alembic backfill (and the writer) hashed — bypasses
        # asyncpg's JSONB → dict deserialisation which converts JSON ``null``
        # literals to Python ``None`` and changes other equality details.
        sql = (
            "SELECT id, record_tenant_id, workspace_id, actor_user_id, action, "
            "       resource_type, resource_id, "
            "       before_json::text AS before_json_text, "
            "       after_json::text AS after_json_text, "
            "       reason, trace_id, created_at, row_hash "
            "FROM audit_log "
            "WHERE record_tenant_id = :tid "
            f"{where_since} "
            "ORDER BY created_at, id "
            "LIMIT :scan_limit"
        )

        async with self._sf() as session:
            rows = (await session.execute(text(sql), params)).all()

        result = AuditVerifyResult(total_rows_scanned=len(rows))
        prev_hash = ""
        for r in rows:
            expected = compute_audit_row_hash(
                prev_hash=prev_hash,
                record_tenant_id=r.record_tenant_id,
                workspace_id=r.workspace_id,
                actor_user_id=r.actor_user_id,
                action=r.action,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                # Pass the canonical ``jsonb::text`` straight through — the
                # hasher accepts pre-serialised JSON strings and uses them
                # verbatim, avoiding asyncpg's lossy JSONB → dict round-trip.
                before_json=r.before_json_text,
                after_json=r.after_json_text,
                reason=r.reason,
                trace_id=r.trace_id,
                created_at=r.created_at,
            )
            actual = (r.row_hash or "").strip()
            if expected != actual:
                result.mismatches.append(AuditChainMismatch(
                    row_id=str(r.id),
                    created_at=r.created_at.isoformat() if r.created_at else "",
                    expected_hash=expected,
                    actual_hash=actual,
                ))
                # Continue using the *stored* (actual) hash for downstream
                # rows so each subsequent legitimate chain segment also
                # validates rather than cascading the error indefinitely.
                prev_hash = actual
            else:
                prev_hash = expected

        result.ok = not result.mismatches
        result.last_row_hash = prev_hash or None
        return result


__all__ = ["AuditChainMismatch", "AuditVerifier", "AuditVerifyResult"]
