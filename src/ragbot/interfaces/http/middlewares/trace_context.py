"""Trace ID middleware."""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ragbot.config.logging import bind_request_context, clear_request_context

# Caller-supplied trace ids are echoed into structured logs and a response
# header — sanitize against newline/escape injection that would break log
# parsing or smuggle into header value.
_TRACE_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class TraceContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw = request.headers.get("X-Trace-Id")
        trace_id = raw if raw and _TRACE_ID_SAFE_RE.match(raw) else str(uuid4())
        request.state.trace_id = trace_id
        bind_request_context(trace_id=trace_id)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["X-Trace-Id"] = trace_id
        return response


__all__ = ["TraceContextMiddleware"]
