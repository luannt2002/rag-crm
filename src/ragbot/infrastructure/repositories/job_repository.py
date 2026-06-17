"""Job repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from ragbot.application.ports.repository_ports import JobRepositoryPort
from ragbot.infrastructure.db.models import JobModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG
from ragbot.shared.types import JobId, JobStatus, TenantId


class SqlAlchemyJobRepository(TenantScopedRepository, JobRepositoryPort):
    """Repository cho bảng jobs — theo dõi trạng thái các tác vụ bất đồng bộ."""

    async def create(
        self,
        *,
        job_id: JobId,
        record_tenant_id: TenantId,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        """Tạo job mới với trạng thái queued.
        @param job_id: UUID job
        @param kind: loại tác vụ (chat, document, ...)
        @param payload: dữ liệu đầu vào
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
        """Cập nhật trạng thái job (running, success, failed, ...).
        @param job_id: UUID job
        @param status: trạng thái mới
        @param error: thông báo lỗi (nếu failed)
        """
        # H4 — record_tenant_id=None allowed for fail-before-lookup / system error paths
        # where we haven't resolved the internal tenant UUID yet. Skip tenant
        # scoping filter in that case (job_id UUID itself is unique).
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
        """Lấy thông tin job theo ID.
        @param job_id: UUID job
        @return: dict thông tin job hoặc None
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
