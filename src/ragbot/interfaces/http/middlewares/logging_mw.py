"""Request logging + Prometheus counter."""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ragbot.infrastructure.observability.metrics import http_requests_total

logger = structlog.get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        t0 = time.monotonic()
        method = request.method
        route = request.url.path
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:  # noqa: BLE001 — log 500 status then re-raise
            status = 500
            raise
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            http_requests_total.labels(method=method, route=route, status=str(status)).inc()
            logger.info(
                "http.request",
                method=method,
                path=route,
                status=status,
                duration_ms=duration_ms,
            )


__all__ = ["LoggingMiddleware"]
