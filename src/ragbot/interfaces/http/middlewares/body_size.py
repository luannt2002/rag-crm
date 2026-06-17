"""Body-size limit middleware — rejects oversized requests with 413.

Different limits per path:
- /api/ragbot/test/chat, /api/ragbot/chat — 256 KB (user messages)
- /api/ragbot/documents, /api/ragbot/sync — 16 MB (doc payloads, prod ingest)
- default — 10 MB (covers /api/ragbot/test/bots/.../documents/upload demo
  path and admin routes; bumped from 512 KB on 2026-05-26 after demo
  upload of a 906 KB legal corpus hit the old cap)

Rationale: Starlette default = unlimited body. Client can POST oversized
content → blocks event loop on json.loads + fills Redis Stream. Reject
early before body is read / auth processed.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.shared.constants import (
    DEFAULT_MAX_BODY_CHAT_BYTES,
    DEFAULT_MAX_BODY_DEFAULT_BYTES,
    DEFAULT_MAX_BODY_INGEST_BYTES,
)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length header exceeds a per-route cap.

    Runs outermost in the middleware stack so oversized payloads are refused
    before GZip/Trace/Auth/Logging do any work.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check Content-Length; return 413 if over the per-path limit."""
        try:
            content_length = int(request.headers.get("content-length", "0") or "0")
        except ValueError:
            content_length = 0

        path = request.url.path
        if path.startswith("/api/ragbot/test/chat") or path.startswith("/api/ragbot/chat"):
            limit = DEFAULT_MAX_BODY_CHAT_BYTES
        elif path.startswith("/api/ragbot/documents") or path.startswith("/api/ragbot/sync"):
            limit = DEFAULT_MAX_BODY_INGEST_BYTES
        else:
            limit = DEFAULT_MAX_BODY_DEFAULT_BYTES

        # Reject chunked transfer (no Content-Length) on capped paths to prevent
        # streaming-bypass of the size gate.
        is_chunked = (request.headers.get("transfer-encoding") or "").lower() == "chunked"
        if is_chunked and request.method in ("POST", "PUT", "PATCH"):
            trace_id = getattr(request.state, "trace_id", "") if hasattr(request, "state") else ""
            return JSONResponse(
                status_code=411,
                content={
                    "ok": False,
                    "error": {
                        "code": "LENGTH_REQUIRED",
                        "message": "Content-Length header required (chunked transfer rejected)",
                    },
                    "data": None,
                    "trace_id": trace_id,
                },
            )

        if content_length > limit:
            trace_id = getattr(request.state, "trace_id", "") if hasattr(request, "state") else ""
            return JSONResponse(
                status_code=413,
                content={
                    "ok": False,
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds {limit} bytes limit",
                        "details": {"content_length": content_length, "limit": limit},
                    },
                    "data": None,
                    "trace_id": trace_id,
                },
            )
        return await call_next(request)


__all__ = ["BodySizeLimitMiddleware"]
