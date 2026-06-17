"""Exception → HTTP response mapping."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse

from ragbot.shared.errors import (
    DomainError,
    InfrastructureError,
    JobNotFound,
    PolicyViolation,
    QuotaExceeded,
    RagbotError,
    TenantIsolationViolation,
    UnauthorizedError,
    WorkspaceIdInvalid,
)

logger = structlog.get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TenantIsolationViolation)
    async def _tenant_violation(_: Request, exc: TenantIsolationViolation) -> ORJSONResponse:
        logger.error("tenant_isolation_violation", details=exc.details)
        return _envelope(403, exc, _.state.trace_id if hasattr(_, "state") else "")

    @app.exception_handler(QuotaExceeded)
    async def _quota_exceeded(req: Request, exc: QuotaExceeded) -> ORJSONResponse:
        return _envelope(429, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(JobNotFound)
    async def _job_not_found(req: Request, exc: JobNotFound) -> ORJSONResponse:
        return _envelope(404, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(req: Request, exc: UnauthorizedError) -> ORJSONResponse:
        return _envelope(401, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(PolicyViolation)
    async def _policy(req: Request, exc: PolicyViolation) -> ORJSONResponse:
        return _envelope(403, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(DomainError)
    async def _domain(req: Request, exc: DomainError) -> ORJSONResponse:
        return _envelope(exc.http_status, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(WorkspaceIdInvalid)
    async def _workspace_id_invalid(
        req: Request, exc: WorkspaceIdInvalid,
    ) -> ORJSONResponse:
        # Slug-format validation failure surfaces as a 422 client error
        # rather than the generic 500 the catch-all would produce.
        return _envelope(exc.http_status, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(InfrastructureError)
    async def _infra(req: Request, exc: InfrastructureError) -> ORJSONResponse:
        logger.exception("infrastructure_error", code=exc.code)
        return _envelope(exc.http_status, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(RagbotError)
    async def _ragbot(req: Request, exc: RagbotError) -> ORJSONResponse:
        return _envelope(exc.http_status, exc, getattr(req.state, "trace_id", ""))

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("unhandled_exception", error=str(exc))
        envelope = {
            "ok": False,
            "data": None,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "an unexpected error occurred",
                "details": {},
            },
            "trace_id": getattr(req.state, "trace_id", ""),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        return ORJSONResponse(envelope, status_code=500)


def _envelope(status: int, exc: RagbotError, trace_id: str) -> ORJSONResponse:
    return ORJSONResponse(
        {
            "ok": False,
            "data": None,
            "error": exc.to_envelope(),
            "trace_id": trace_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        },
        status_code=status,
    )


__all__ = ["register_exception_handlers"]
