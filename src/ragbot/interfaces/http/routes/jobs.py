"""Job status route."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from ragbot.application.queries.chat_queries import GetJobStatusQuery
from ragbot.interfaces.http.schemas.document_schema import JobStatusResponse
from ragbot.shared.types import JobId, TenantId

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: UUID, request: Request) -> JobStatusResponse:
    container = request.app.state.container
    uc = container.get_job_status_uc()
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=403, detail="missing tenant context")
    query = GetJobStatusQuery(
        record_tenant_id=TenantId(record_tenant),
        job_id=JobId(job_id),
    )
    result = await uc.execute(query)
    return JobStatusResponse(
        job_id=str(result.job_id),
        status=str(result.status),
        created_at=result.created_at,
        completed_at=result.completed_at,
        error=result.error,
        result=result.result,
    )


__all__ = ["router"]
