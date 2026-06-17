"""GetJobStatusUseCase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ragbot.application.dto.chat_dto import JobStatusDTO
from ragbot.application.queries.chat_queries import GetJobStatusQuery
from ragbot.shared.errors import JobNotFound
from ragbot.shared.types import JobId, JobStatus

if TYPE_CHECKING:
    from ragbot.application.ports.repository_ports import JobRepositoryPort


class GetJobStatusUseCase:
    def __init__(self, job_repo: JobRepositoryPort) -> None:
        self._jobs = job_repo

    async def execute(self, query: GetJobStatusQuery) -> JobStatusDTO:
        row = await self._jobs.get(query.job_id, record_tenant_id=query.record_tenant_id)
        if row is None:
            raise JobNotFound(f"job {query.job_id} not found")
        return JobStatusDTO(
            job_id=JobId(row["id"]),  # type: ignore[arg-type]
            status=row.get("status", "queued"),  # type: ignore[arg-type]
            created_at=row["created_at"],  # type: ignore[arg-type]
            completed_at=row.get("completed_at"),  # type: ignore[arg-type]
            error=row.get("error"),  # type: ignore[arg-type]
            result=row.get("result"),  # type: ignore[arg-type]
        )


__all__ = ["GetJobStatusUseCase"]
