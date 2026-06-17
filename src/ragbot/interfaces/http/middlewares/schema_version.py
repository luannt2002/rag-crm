"""Schema version negotiation middleware.

REST best practice — schema evolution via ``X-Schema-Version`` header, not
URL path. The URL stays the canonical purpose-named identifier
(``/documents/create``, ``/chat/answer``); the request/response payload
shape is negotiated through the header. This lets us add a v2 payload
shape next to v1 without breaking deployed B2B callers or forcing the
proliferation of ``/api/v1/`` / ``/api/v2/`` parallel router files.

Behaviour
---------
* Header missing  → ``request.state.schema_version = DEFAULT_SCHEMA_VERSION``
* Header present  → must parse as ``int`` and be in
  ``SUPPORTED_SCHEMA_VERSIONS``; otherwise the middleware short-circuits with
  HTTP 400 ``SCHEMA_VERSION_UNSUPPORTED`` (header malformed) or
  ``SCHEMA_VERSION_UNKNOWN`` (parsed int not in supported set).

The middleware is stateless and side-effect-free besides setting
``request.state.schema_version``; downstream handlers may branch on the
value to render the negotiated payload shape. When the supported set is
extended to ``(1, 2)`` no middleware change is required — only the
handler's branching code needs the new branch.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.shared.constants import (
    DEFAULT_SCHEMA_VERSION,
    SCHEMA_VERSION_HEADER,
    SUPPORTED_SCHEMA_VERSIONS,
)


def _error_response(
    status_code: int, code: str, message: str, trace_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": code, "message": message},
            "data": None,
            "trace_id": trace_id,
        },
    )


class SchemaVersionMiddleware(BaseHTTPMiddleware):
    """Read ``X-Schema-Version`` header, lift onto ``request.state``.

    Register AFTER ``TenantContextMiddleware`` so ``request.state.trace_id``
    is already populated (TraceContext runs even earlier in the stack); the
    400 response can then echo the trace id for partner-side correlation.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        raw = request.headers.get(SCHEMA_VERSION_HEADER)
        trace_id = getattr(request.state, "trace_id", "") if hasattr(request, "state") else ""

        if raw is None or raw == "":
            request.state.schema_version = DEFAULT_SCHEMA_VERSION
            return await call_next(request)

        try:
            parsed = int(raw)
        except ValueError:
            return _error_response(
                status_code=400,
                code="SCHEMA_VERSION_UNSUPPORTED",
                message=(
                    f"{SCHEMA_VERSION_HEADER} header must be an integer; "
                    f"got {raw!r}"
                ),
                trace_id=trace_id,
            )

        if parsed not in SUPPORTED_SCHEMA_VERSIONS:
            return _error_response(
                status_code=400,
                code="SCHEMA_VERSION_UNSUPPORTED",
                message=(
                    f"{SCHEMA_VERSION_HEADER}={parsed} not supported; "
                    f"supported versions: "
                    f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}"
                ),
                trace_id=trace_id,
            )

        request.state.schema_version = parsed
        return await call_next(request)


__all__ = ["SchemaVersionMiddleware"]
