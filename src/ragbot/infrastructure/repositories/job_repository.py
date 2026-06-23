"""Job repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update

from ragbot.application.ports.repository_ports import JobRepositoryPort
from ragbot.infrastructure.db.models import JobModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG
from ragbot.shared.types import JobId, JobStatus, TenantId

logger = structlog.get_logger(__name__)


class SqlAlchemyJobRepository(TenantScopedRepository, JobRepositoryPort):
    """Repository for the jobs table — tracks async task status."""

    async def create(
        self,
        *,
        job_id: JobId,
        record_tenant_id: TenantId,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        """Create a new job in the queued state.

        @param job_id: job UUID
        @param kind: task kind (chat, document, ...)
        @param payload: input data
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            session.add(
                JobModel(
                    id=job_id,
                    record_tenant_id=tid,
                    workspace_id=WORKSPACE_SYSTEM_SLUG,
                    kind=kind,
                    status="queued",
                    payload=dict(payload),
                ),
            )
            await session.commit()

    async def update_status(
        self,
        job_id: JobId,
        *,
        record_tenant_id: TenantId | None,
        status: JobStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        """Update a job's status (running, success, failed, ...).

        @param job_id: job UUID
        @param status: new status
        @param error: error message (when failed)
        """
        # record_tenant_id=None is allowed only for fail-before-lookup / system
        # error paths where the internal tenant UUID has not yet been resolved;
        # the tenant scoping filter is skipped in that case (the job_id UUID is
        # itself globally unique). This bypasses the tenant fence, so emit a
        # structured warning to keep the unscoped write observable.
        if record_tenant_id is None:
            logger.warning(
                "job_update_unscoped",
                job_id=str(job_id),
                status=status,
                reason="record_tenant_id unresolved — pre-lookup/system error path",
            )
        async with self._new_session() as session:
            values: dict[str, Any] = {"status": status}
            if status in {"success", "failed", "cancelled", "dlq"}:
                values["completed_at"] = datetime.now(tz=timezone.utc)
            if status == "running":
                values["started_at"] = datetime.now(tz=timezone.utc)
            if result is not None:
                values["result"] = dict(result)
            if error is not None:
                values["error"] = error
            stmt = update(JobModel).where(JobModel.id == job_id)
            if record_tenant_id is not None:
                stmt = stmt.where(JobModel.record_tenant_id == record_tenant_id)
            await session.execute(stmt.values(**values))
            await session.commit()

    async def get(
        self,
        job_id: JobId,
        *,
        record_tenant_id: TenantId,
    ) -> dict[str, object] | None:
        """Fetch a job by ID.

        @param job_id: job UUID
        @return: job info dict, or None
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.scalar(
                select(JobModel).where(
                    JobModel.id == job_id, JobModel.record_tenant_id == tid,
                ),
            )
            if row is None:
                return None
            return {
                "id": row.id,
                "tenant_id": row.record_tenant_id,
                "kind": row.kind,
                "status": row.status,
                "payload": row.payload,
                "result": row.result,
                "error": row.error,
                "created_at": row.created_at,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
            }


__all__ = ["SqlAlchemyJobRepository"]
