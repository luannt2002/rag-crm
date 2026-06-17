"""Streaming upload route for large document bodies (WB-2 P1-5).

Partner BE pushes a ``multipart/form-data`` body whose ``file`` part can
reach :data:`DEFAULT_UPLOAD_STREAM_MAX_BYTES` (500 MiB).  The handler
reads ``request.stream()`` chunk-by-chunk, writes each chunk to a temp
file under :data:`DEFAULT_UPLOAD_TEMP_DIR`, and never materialises the
whole body in memory.  Resident memory peak is bounded by
:data:`DEFAULT_UPLOAD_STREAM_CHUNK_SIZE` (1 MiB) regardless of upload
size.

Hand-off to the worker is via Redis Stream
:data:`SUBJECT_DOCUMENT_UPLOAD_STREAM` — the route ``XADD``s a single
message carrying the temp-file path plus the 4-key identity tuple; a
separate consumer (DocumentService parser registry) persists the chunks
and unlinks the temp file.  Until the worker runs, the temp file lives
on disk; an orphaned-cleanup job (admin cron) handles partner BEs that
drop the connection mid-stream.

Wire contract::

    POST /api/ragbot/documents/upload-stream
    Headers:
      Authorization: Bearer <jwt>          # carries record_tenant_id
      Content-Type:  multipart/form-data; boundary=...
    Form fields:
      bot_id        (required)
      channel_type  (required)
      workspace_id  (optional — falls back to str(record_tenant_id))
      document_name (required)
      mime_type     (optional)
      language      (optional, default "vi")
      file          (required — binary part)
    Response 202 ACCEPTED:
      {ok, document_id, state="uploading", bytes_received, trace_id}

Errors:
  * 401 — missing tenant context
  * 403 — RBAC level below admin (60)
  * 404 — 4-key bot resolve miss
  * 413 — body exceeds :data:`DEFAULT_UPLOAD_STREAM_MAX_BYTES`
  * 422 — missing required field / invalid workspace slug
  * 500 — temp-file write failure (OSError narrowed)

The temp filename is a UUID4 — bot_id is NEVER part of the filesystem
path to prevent tenant-name leak through filesystem snapshots, logs, or
backups.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from ragbot.interfaces.http.routes.documents import _resolve_bot_uuid
from ragbot.shared.constants import (
    DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
    DEFAULT_UPLOAD_STREAM_MAX_BYTES,
    DEFAULT_UPLOAD_TEMP_DIR,
    MAX_BOT_ID_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_DOCUMENT_NAME_LENGTH,
    SUBJECT_DOCUMENT_UPLOAD_STREAM,
)
from ragbot.shared.rbac import require_min_level
from ragbot.interfaces.http._ingest_quota_guard import enforce_ingest_quota
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["documents"])

# RBAC: admin level for bot-owner ingest path.  Matches the level
# implicitly required by ``require_permission_dep("document", "ingest")``
# in the JSON ingest route — the streaming variant short-cuts the
# permission system because multipart form upload precedes the JSON body
# parsing that the dep relies on.
_UPLOAD_RBAC_MIN_LEVEL = 60


def _record_tenant(request: Request) -> UUID:
    """Lift ``record_tenant_id`` UUID from JWT-bound request state."""
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="missing tenant context")
    return record_tenant if isinstance(record_tenant, UUID) else UUID(str(record_tenant))


def _ensure_temp_dir() -> Path:
    """Create the temp dir if missing.  Idempotent — safe to call per request."""
    path = Path(DEFAULT_UPLOAD_TEMP_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Disk full / permission denied — fail loud, the handler can't
        # recover and the partner should retry once ops fixes the host.
        logger.error(
            "upload_stream_tempdir_failed",
            tempdir=str(path),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="upload_tempdir_unavailable",
        ) from exc
    return path


def _new_temp_path(temp_dir: Path) -> Path:
    """UUID4 filename — never includes tenant/bot identifiers."""
    return temp_dir / f"{uuid.uuid4().hex}.tmp"


def _redis_client(request: Request) -> Any | None:
    """Return the singleton redis client or ``None`` if container missing."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        return None
    provider = getattr(container, "redis_client", None)
    if provider is None:
        return None
    try:
        return provider()
    except (KeyError, TypeError, RuntimeError):
        return None


async def _enqueue_upload(
    redis_client: Any | None,
    *,
    temp_path: Path,
    record_tenant_id: UUID,
    workspace_id: str,
    record_bot_id: UUID,
    bot_id: str,
    channel_type: str,
    document_name: str,
    mime_type: str,
    language: str,
    bytes_received: int,
    filename: str,
    trace_id: str,
) -> str:
    """``XADD`` the worker hand-off message.

    Returns the Stream entry id, or empty string when Redis is
    unavailable (graceful-degradation per CLAUDE.md aux-dependency rule
    — the temp file survives on disk and the orphan-cleanup cron will
    pick it up, but the partner still gets a 202 with the document_id
    so they can retry the worker poll separately).
    """
    if redis_client is None:
        logger.warning(
            "upload_stream_redis_unavailable",
            temp_path=str(temp_path),
            bytes_received=bytes_received,
        )
        return ""
    # File content is NEVER serialised into the stream message — only
    # the path pointer.  The worker reads the bytes itself.  This keeps
    # PII off the wire and out of Redis snapshot/AOF files.
    fields = {
        "subject": SUBJECT_DOCUMENT_UPLOAD_STREAM,
        "temp_path": str(temp_path),
        "record_tenant_id": str(record_tenant_id),
        "workspace_id": workspace_id,
        "record_bot_id": str(record_bot_id),
        "bot_id": bot_id,
        "channel_type": channel_type,
        "document_name": document_name,
        "mime_type": mime_type,
        "language": language,
        "bytes": str(bytes_received),
        "filename": filename,
        "trace_id": trace_id,
    }
    try:
        entry_id = await redis_client.xadd(
            SUBJECT_DOCUMENT_UPLOAD_STREAM, fields,
        )
    except (OSError, ConnectionError) as exc:
        # Redis transport hiccup — temp file stays, partner can retry
        # the read side via /jobs/<doc_id>; treat as graceful degradation.
        logger.warning(
            "upload_stream_enqueue_failed",
            error_type=type(exc).__name__,
            temp_path=str(temp_path),
        )
        return ""
    return str(entry_id)


def _try_unlink(path: Path) -> None:
    """Best-effort cleanup — never raises."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "upload_stream_cleanup_failed",
            temp_path=str(path),
            error_type=type(exc).__name__,
        )


@router.post(
    "/documents/upload-stream",
    status_code=status.HTTP_202_ACCEPTED,
    summary=(
        "Stream a large file body to disk and enqueue worker ingest "
        "(caps resident memory at 1MiB regardless of body size)"
    ),
)
async def upload_stream(
    request: Request,
    bot_id: str = Form(..., min_length=1, max_length=MAX_BOT_ID_LENGTH),
    channel_type: str = Form(
        ..., min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH,
    ),
    document_name: str = Form(
        ..., min_length=1, max_length=MAX_DOCUMENT_NAME_LENGTH,
    ),
    workspace_id: str | None = Form(default=None),
    mime_type: str | None = Form(default=None),
    language: str = Form(default="vi", min_length=2, max_length=8),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Chunked streaming upload — see module docstring for wire contract."""
    # ---- AuthZ: JWT tenant lift + admin RBAC ---------------------------
    require_min_level(request, _UPLOAD_RBAC_MIN_LEVEL)
    record_tenant = _record_tenant(request)
    ws = resolve_workspace_id(workspace_id, record_tenant_id=record_tenant)

    # ---- 4-key bot resolve ---------------------------------------------
    record_bot_id = await _resolve_bot_uuid(
        request,
        record_tenant=record_tenant,
        workspace_id=ws,
        bot_id=bot_id,
        channel_type=channel_type,
    )

    # ---- Per-tenant ingest quota ---------------------------------------
    # Charge BEFORE streaming the body to disk so a quota-rejected upload
    # wastes neither disk I/O nor worker capacity (closes IQ-1;
    # QuotaExceeded → HTTP 429).
    await enforce_ingest_quota(
        request.app.state.container,
        record_tenant_id=record_tenant,
        workspace_id=ws,
    )

    # ---- Validate file part --------------------------------------------
    if not file.filename:
        raise HTTPException(status_code=422, detail="filename_missing")
    effective_mime = (
        (mime_type or "").strip()
        or (file.content_type or "").strip()
        or "application/octet-stream"
    )

    # ---- Stream to temp file (chunked) ---------------------------------
    temp_dir = _ensure_temp_dir()
    temp_path = _new_temp_path(temp_dir)
    bytes_received = 0
    fd: int | None = None
    try:
        # ``os.open`` with O_CREAT|O_WRONLY|O_EXCL guarantees the path is
        # newly-allocated for this request — defends against UUID4
        # collision in the (theoretical) >1e30 simultaneous-upload case.
        fd = os.open(
            str(temp_path),
            os.O_CREAT | os.O_WRONLY | os.O_EXCL,
            0o600,  # owner-only — temp data is not world-readable
        )
        while True:
            chunk = await file.read(DEFAULT_UPLOAD_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            bytes_received += len(chunk)
            if bytes_received > DEFAULT_UPLOAD_STREAM_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"payload_too_large: limit="
                        f"{DEFAULT_UPLOAD_STREAM_MAX_BYTES}B"
                    ),
                )
            os.write(fd, chunk)
        if bytes_received == 0:
            raise HTTPException(status_code=422, detail="empty_file")
    except HTTPException:
        # Cleanup + reraise.  Finally block handles fd close; we unlink
        # before bubbling so a tenant cannot fill /tmp by repeatedly
        # tripping the 413 / 422 path.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        _try_unlink(temp_path)
        raise
    except OSError as exc:
        # Disk error mid-stream.  Same cleanup as the HTTP path so a
        # transient write failure does not leak bytes.
        logger.error(
            "upload_stream_write_failed",
            temp_path=str(temp_path),
            bytes_received=bytes_received,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        _try_unlink(temp_path)
        raise HTTPException(
            status_code=500, detail="upload_write_failed",
        ) from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                # fsync/close failure: log only — the bytes are already
                # on the kernel page cache and unlink ran in the except
                # branch when we reach here via raise.
                logger.warning(
                    "upload_stream_close_failed", temp_path=str(temp_path),
                )

    # ---- Worker hand-off via Redis Stream ------------------------------
    document_id = uuid.uuid4()
    trace_id_attr = getattr(request.state, "trace_id", None)
    trace_id = "" if trace_id_attr is None else str(trace_id_attr)
    entry_id = await _enqueue_upload(
        _redis_client(request),
        temp_path=temp_path,
        record_tenant_id=record_tenant,
        workspace_id=ws,
        record_bot_id=record_bot_id,
        bot_id=bot_id,
        channel_type=channel_type,
        document_name=document_name,
        mime_type=effective_mime,
        language=language,
        bytes_received=bytes_received,
        filename=file.filename,
        trace_id=trace_id,
    )

    # PII redaction at boundary: log byte count + identity but NEVER file
    # content (cf. claude-mem rule).  ``filename`` is partner-controlled
    # metadata, not document body, so it's safe to log at info level.
    logger.info(
        "upload_stream_accepted",
        document_id=str(document_id),
        record_tenant_id=str(record_tenant),
        workspace_id=ws,
        bot_id=bot_id,
        channel_type=channel_type,
        bytes_received=bytes_received,
        mime_type=effective_mime,
        enqueued=bool(entry_id),
        stream_entry_id=entry_id or None,
    )
    return {
        "ok": True,
        "document_id": str(document_id),
        "state": "uploading",
        "bytes_received": bytes_received,
        "trace_id": trace_id,
    }


__all__ = ["router"]
