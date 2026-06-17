"""Per-tenant daily document ingest quota gate.

Multi-tenant fairness — vấn đề 6C of the upload-flow audit
(``reports/UPLOAD_FLOW_AUDIT_RAM_REDIS_20260516.md``).

Without this gate, one tenant flooding ``POST /documents/upload`` can:

1. Exhaust worker capacity → starves other tenants' bots
2. Bloat ``document_chunks`` → HNSW index quality degrades globally
3. Burn embed-API budget shared across the platform

Contract
--------
:meth:`check_and_increment` is an atomic SELECT FOR UPDATE + UPDATE
inside a single transaction:

1. Lock the tenant's ``quotas`` row.
2. Roll the daily counter over if ``documents_today_reset_at < now()``.
3. ``documents_per_day_limit = 0`` → unlimited (premium tenant) → pass.
4. ``documents_today_count + 1 > limit`` → raise :class:`QuotaExceeded`.
5. Else ``documents_today_count += 1`` + COMMIT.

The route handler MUST call this BEFORE ``INSERT INTO documents`` so a
rejected upload never reaches the worker pipeline.

Tenant scope
------------
The DB session passed in carries ``app.tenant_id`` via
``session_with_tenant`` (RLS enforced). ``quotas`` is row-scoped on
``record_tenant_id`` so the lock + update naturally stays within the
caller's tenant.

Missing-row policy
------------------
``quotas`` is seeded per tenant at provisioning. A missing row means a
mis-provisioned tenant — fail loud (NOT silently allow unlimited)
because silent-allow is the exact noisy-neighbour bug this gate exists
to prevent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import text

from ragbot.shared.constants import DEFAULT_DOCUMENTS_PER_DAY_LIMIT
from ragbot.shared.errors import QuotaExceeded

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class IngestQuotaService:
    """Stateless service — caller owns the AsyncSession lifecycle.

    Construction is cheap; one instance per request is fine. The DB
    session is passed per-call so the lock + update commit inside the
    caller's outer transaction (route handler typically holds the same
    session open across quota check + document INSERT).
    """

    async def check_and_increment(
        self,
        session: "AsyncSession",
        *,
        record_tenant_id: UUID,
        increment_by: int = 1,
    ) -> tuple[int, int]:
        """Atomically check + increment the daily document counter.

        Returns ``(new_count, limit)`` so the caller can echo headroom
        in the HTTP response (partner SLA: see remaining → throttle
        client-side before hitting the cap).

        Raises:
            QuotaExceeded: when ``count + increment_by > limit``.
            QuotaExceeded: when the tenant's quota row is missing
                (``mis-provisioned-tenant`` reason — fail loud).

        @param session: caller's open AsyncSession with RLS bound.
        @param record_tenant_id: internal tenant UUID PK.
        @param increment_by: usually 1 (single document upload) but
            batch ingest passes ``len(documents)`` for atomic check.
        """
        row = await session.execute(
            text(
                """
                SELECT documents_per_day_limit,
                       documents_today_count,
                       documents_today_reset_at
                FROM quotas
                WHERE record_tenant_id = :tenant_id
                FOR UPDATE
                """,
            ),
            {"tenant_id": record_tenant_id},
        )
        record = row.fetchone()

        if record is None:
            # Mis-provisioned tenant — quotas seed missed during onboarding.
            # Fail loud so ops sees the regression instead of silently
            # letting an un-quota'd tenant abuse the platform.
            logger.error(
                "ingest_quota_missing_row",
                record_tenant_id=str(record_tenant_id),
            )
            raise QuotaExceeded(
                f"quota row missing for tenant {record_tenant_id} — "
                "tenant not fully provisioned",
            )

        limit, count, reset_at = record[0], record[1], record[2]
        now = datetime.now(tz=timezone.utc)

        # Daily rollover. The reset anchor is set forward at provision
        # time (next UTC midnight) and bumped here every time it elapses.
        if reset_at is not None and reset_at < now:
            count = 0
            # Next rollover = the next UTC midnight after ``now``. We
            # compute it inline rather than passing arithmetic to SQL
            # so the comparison stays language-agnostic for tests.
            next_midnight = datetime(
                now.year, now.month, now.day, tzinfo=timezone.utc,
            ).replace(hour=0, minute=0, second=0, microsecond=0)
            # Advance to the next midnight (today's midnight already passed)
            next_midnight = next_midnight.replace(day=now.day) + _ONE_DAY
            logger.info(
                "ingest_quota_daily_rollover",
                record_tenant_id=str(record_tenant_id),
                next_reset_at=next_midnight.isoformat(),
            )
            reset_at = next_midnight

        # 0 = unlimited (premium tenant override seeded by ops).
        if limit > 0 and count + increment_by > limit:
            logger.warning(
                "ingest_quota_exceeded",
                record_tenant_id=str(record_tenant_id),
                count=count,
                increment=increment_by,
                limit=limit,
            )
            raise QuotaExceeded(
                f"daily document quota {limit} reached "
                f"(used {count}, requested {increment_by})",
            )

        new_count = count + increment_by
        await session.execute(
            text(
                """
                UPDATE quotas
                SET documents_today_count = :count,
                    documents_today_reset_at = :reset_at
                WHERE record_tenant_id = :tenant_id
                """,
            ),
            {
                "count": new_count,
                "reset_at": reset_at,
                "tenant_id": record_tenant_id,
            },
        )

        return new_count, limit


# Module-level constant — moved out of the hot path to avoid recomputing
# ``timedelta(days=1)`` on every call.
from datetime import timedelta  # noqa: E402

_ONE_DAY = timedelta(days=1)

# Sanity check — keeps default in sync with constants.py SSoT so a future
# rename doesn't silently break ``alembic 010i`` server_default backfill.
assert DEFAULT_DOCUMENTS_PER_DAY_LIMIT > 0  # noqa: S101 — module-load assertion


__all__ = ["IngestQuotaService"]
