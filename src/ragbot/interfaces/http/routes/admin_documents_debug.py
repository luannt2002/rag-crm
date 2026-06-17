"""Operator debug endpoint — fetch the parsed representation of a document.

Companion to ``application/services/parsed_md_dump.py``. The dump service
writes ``{PARSED_MD_DIR}/{tenant}/{doc_id}.md`` on every Action 1 upload.
This endpoint serves that file back, with two safety gates:

1. RBAC level 60 (admin) — same gate as other admin routes.
2. Tenant ownership — the caller's JWT-bound ``record_tenant_id`` MUST
   match the document's ``record_tenant_id``. Filesystem ACL alone is
   not enough; a tenant admin must not be able to read another
   tenant's parsed representation by guessing UUIDs.

Response shapes (selected via ``?format=`` query param):

* ``GET /admin/documents/{id}/debug-view?format=md`` (default) — returns
  the raw ``.md`` body as ``text/markdown`` so a browser saves the file
  directly.
* ``GET /admin/documents/{id}/debug-view?format=md&inline=true`` — returns
  JSON envelope so admin UIs can render the Markdown in a panel without
  triggering a browser download.

Path is generic on purpose: future formats (``?format=html``,
``?format=json``) reuse the same URL without breaking clients.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text as _sql_text

from ragbot.application.services.parsed_md_dump import (
    read_dump,
    resolve_dump_path,
)
from ragbot.shared.constants import (
    DEBUG_VIEW_FORMATS_ALLOWED,
    DEFAULT_ADMIN_LEVEL,
    DEFAULT_DEBUG_VIEW_FORMAT,
)
from ragbot.shared.rbac import require_min_level

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin/documents-debug"])


def _require_admin_tenant(request: Request) -> UUID:
    """Enforce admin level + return the caller's record_tenant_id from JWT."""
    require_min_level(request, DEFAULT_ADMIN_LEVEL)
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="missing tenant context")
    if isinstance(record_tenant, UUID):
        return record_tenant
    return UUID(str(record_tenant))


async def _verify_document_owner(
    request: Request,
    *,
    document_id: UUID,
    caller_tenant: UUID,
) -> tuple[str | None, str | None, int | None]:
    """Confirm ``document_id`` belongs to ``caller_tenant`` and return name/source.

    Raises 404 when the document is missing OR owned by a different tenant
    (uniform 404 prevents cross-tenant UUID enumeration). Returns the
    document_name + source_url + content_chars for the JSON envelope.
    """
    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        row = await session.execute(
            _sql_text(
                """
                SELECT document_name, source_url, content_chars
                FROM documents
                WHERE id = :doc_id
                  AND record_tenant_id = :tenant_id
                  AND deleted_at IS NULL
                LIMIT 1
                """,
            ),
            {"doc_id": document_id, "tenant_id": caller_tenant},
        )
        record = row.fetchone()
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    return record[0], record[1], record[2]


@router.get(
    "/admin/documents/{document_id}/debug-view",
    summary="Download or inline-view the parsed representation of a document",
)
async def get_document_debug_view(
    document_id: UUID,
    request: Request,
    inline: bool = Query(
        default=False,
        description="True → JSON envelope (for UI). False → raw file download.",
    ),
    format: str = Query(
        default=DEFAULT_DEBUG_VIEW_FORMAT,
        description=(
            "Parsed-representation format. Today only 'md' is implemented; "
            "future values ('html', 'json') reuse the same path. Invalid "
            "values return 422."
        ),
    ),
) -> Any:
    """Serve the parsed representation of a document.

    Two-step gate:
    1. Admin level 60 (`require_min_level`).
    2. Tenant ownership of the document (`_verify_document_owner` returns
       404 if mismatch — same shape as a missing document so a tenant
       admin cannot probe for other tenants' UUIDs).

    Returns 410 Gone when the dump was disabled at upload time
    (`RAGBOT_PARSED_MD_DIR=`) — the DB row exists but the file was never
    written. Operator must re-upload to regenerate.

    Returns 422 when ``format`` is outside ``DEBUG_VIEW_FORMATS_ALLOWED``.
    """
    if format not in DEBUG_VIEW_FORMATS_ALLOWED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unsupported format '{format}'. "
                f"Allowed: {sorted(DEBUG_VIEW_FORMATS_ALLOWED)}"
            ),
        )
    caller_tenant = _require_admin_tenant(request)
    doc_name, source_url, content_chars = await _verify_document_owner(
        request, document_id=document_id, caller_tenant=caller_tenant,
    )

    path = resolve_dump_path(
        record_tenant_id=caller_tenant,
        document_id=document_id,
    )
    if path is None:
        raise HTTPException(
            status_code=410,
            detail="parsed_md_dump_disabled (RAGBOT_PARSED_MD_DIR env empty)",
        )

    content = read_dump(
        record_tenant_id=caller_tenant,
        document_id=document_id,
    )
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"parsed_md_file_not_found at {path}. "
                "Possible causes: (a) document was uploaded before the dump "
                "feature shipped — re-upload to regenerate; (b) disk error "
                "during write (check ragbot-api logs for "
                "'parsed_md_dump_failed' event)."
            ),
        )

    if inline:
        return {
            "ok": True,
            "data": {
                "document_id": str(document_id),
                "document_name": doc_name,
                "source_url": source_url,
                "content_chars": content_chars,
                "file_path": str(path),
                "markdown": content,
            },
        }

    # Raw download — Content-Disposition with safe filename derived from
    # document_id (not document_name) so user-provided names cannot
    # inject CRLF / quote characters into the header.
    filename = f"{document_id}.md"
    return PlainTextResponse(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


__all__ = ["router"]
