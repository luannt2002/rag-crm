"""Document CRUD routes (list / add / upload / delete) for the test_chat package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving).
"""

from __future__ import annotations

import uuid
from datetime import datetime as _dt, timezone as _tz

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import text

from ragbot.application.services import google_link_service
from ragbot.shared.workspace_id_validator import resolve_workspace_id

from .schemas import AddDocumentRequest
from ._shared import (
    _doc_service,
    _find_bot_uuid,
    _page_limit,
    _resolve_body_tenant_int,
    _sf,
    logger,
)

router = APIRouter(tags=["test"])


@router.get("/bots/{bot_id}/{channel_type}/documents")
async def list_documents(
    bot_id: str, channel_type: str, request: Request,
    tenant_id: int | None = None, limit: int | None = None,
    workspace_id: str | None = None,  # 4-key: per-bot workspace
) -> dict:
    """Liệt kê tài liệu của bot kèm số chunk.
    @param bot_id, channel_type: định danh bot
    @param tenant_id: legacy upstream INT (optional query; falls back to JWT
        ``record_tenant_id`` UUID)
    @return: danh sách tài liệu

    Either the legacy INT query (translated to UUID via
    ``tenants.config->>'upstream_tenant_id'``) or the JWT-bound
    ``record_tenant_id`` UUID is required to scope the listing.
    """
    page = _page_limit(limit)
    sf = _sf(request)
    if tenant_id is not None:
        record_tenant_uuid = await _resolve_body_tenant_int(request, tenant_id)
    else:
        raw = getattr(request.state, "record_tenant_id", None)
        record_tenant_uuid = raw if isinstance(raw, uuid.UUID) else None
    if record_tenant_uuid is None:
        raise HTTPException(
            status_code=422,
            detail="3-key identity violation: tenant required (query INT or JWT UUID)",
        )
    # Workspace is an OPTIONAL disambiguator on this read-path. When the caller
    # supplies it, scope strictly to that workspace (cross-workspace same-slug
    # safety). When omitted (e.g. demo UI listing a bot by its unique slug),
    # resolve by (bot_id, channel, tenant) alone — otherwise a bot that lives
    # in a non-default workspace would list zero docs for any caller that does
    # not know its slug.
    _ws_explicit = workspace_id is not None and str(workspace_id).strip() != ""
    _ws_clause = "AND b.workspace_id = :ws" if _ws_explicit else ""
    _params = {
        "bot_id": bot_id, "ch": channel_type,
        "record_tenant_id": record_tenant_uuid, "lim": page,
    }
    if _ws_explicit:
        _params["ws"] = resolve_workspace_id(
            workspace_id, record_tenant_id=record_tenant_uuid,
        )
    async with sf() as session:
        result = await session.execute(
            text(f"""
                SELECT d.id, d.document_name, d.source_url, d.content_hash, d.created_at,
                    d.state, d.content_chars,
                    count(dc.id) as chunk_count,
                    d.current_step, d.progress_percent,
                    d.chunks_total, d.chunks_processed, d.progress_updated_at
                FROM documents d
                JOIN bots b ON d.record_bot_id = b.id
                LEFT JOIN document_chunks dc ON dc.record_document_id = d.id
                WHERE b.bot_id = :bot_id AND b.channel_type = :ch
                    {_ws_clause}
                    AND b.record_tenant_id = :record_tenant_id AND b.is_deleted = false
                    AND d.deleted_at IS NULL
                GROUP BY d.id
                ORDER BY d.created_at DESC
                LIMIT :lim
            """),
            _params,
        )
        rows = result.fetchall()

    # Derive user-friendly status from state + chunks_count.
    # State machine (lưu DB column documents.state):
    #   DRAFT       → "preparing"  (Action 1 OK, worker chưa pick)
    #   processing  → "processing" (worker đang chunk/enrich/embed)
    #   active      → "ready"      (worker xong, có chunks > 0)
    #   failed      → "failed"     (worker fail, có error)
    # UI hiển thị badge dựa trên `status` field.
    def _derive_status(state: str | None, chunks: int) -> tuple[str, bool]:
        if state == "active" and chunks > 0:
            return "ready", True
        if state == "active" and chunks == 0:
            return "processing", False  # active set but no chunks yet (rare)
        if state == "DRAFT":
            return "preparing", False
        if state == "failed":
            return "failed", False
        if state == "processing":
            return "processing", False
        return state or "unknown", False

    docs = []
    for r in rows:
        _state = r[5]
        _chunks = r[7]
        _status, _ready = _derive_status(_state, _chunks)
        # Progress columns (alembic 0093). NULL on rows ingested before
        # the migration — UI falls back to chunk_count signal.
        _current_step = r[8]
        _progress_percent = r[9]
        _chunks_total = r[10]
        _chunks_processed = r[11]
        _progress_updated_at = r[12]

        # ETA estimate: if we know progress + age, extrapolate remaining.
        _eta_seconds = None
        if (
            _progress_percent is not None
            and 0 < _progress_percent < 100
            and r[4] is not None
        ):
            import datetime as _dt  # noqa: PLC0415
            _age_s = (
                _dt.datetime.now(_dt.timezone.utc) - r[4]
            ).total_seconds()
            if _age_s > 1:
                _eta_seconds = int(
                    _age_s * (100 - _progress_percent) / _progress_percent,
                )

        docs.append({
            "id": str(r[0]),
            "document_name": r[1],
            "source_url": r[2],
            "content_hash": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "state": _state,           # raw DB state (DRAFT|active|failed|processing)
            "status": _status,         # user-friendly (preparing|processing|ready|failed)
            "ready": _ready,           # boolean → bot có thể trả lời dựa trên doc này
            "content_chars": r[6],
            "chunk_count": _chunks,
            # Progress fields (NULL until worker writes them).
            "current_step": _current_step,
            "progress_percent": _progress_percent,
            "chunks_total": _chunks_total,
            "chunks_processed": _chunks_processed,
            "progress_updated_at": (
                _progress_updated_at.isoformat()
                if _progress_updated_at else None
            ),
            "eta_seconds": _eta_seconds,
        })
    return {"ok": True, "total": len(rows), "documents": docs}


@router.post("/bots/{bot_id}/{channel_type}/documents", status_code=202)
async def add_document(bot_id: str, channel_type: str, req: AddDocumentRequest, request: Request) -> dict:
    """Thêm tài liệu — văn bản thuần hoặc link Google Docs/Sheets.

    Async pattern (post-Phase A): validate + fetch + save row → return 202
    ngay. Worker (``ragbot-document-worker``) consume event ``document.uploaded.v1``
    để chunk + enrich + embed background. Tránh 504 khi corpus lớn (98s+
    sync block trước đây).

    UI poll ``GET /api/ragbot/test/bots/{bot_id}/{channel_type}/documents``
    để check chunks count khi worker xong.

    @param req: tiêu đề, nội dung hoặc URL tài liệu
    @return: {ok, document_id, status, message} — status=queued|inline
    """
    bot_uuid = await _find_bot_uuid(
        request, bot_id, channel_type, workspace_id=req.workspace_id,
    )
    content = req.content or ""
    source_url = req.url or ""

    # Defense-in-depth: if user pasted Google URL into content field by mistake,
    # promote to req.url so fetch branch below runs.
    _trimmed = content.strip()
    if (
        not req.url
        and 0 < len(_trimmed) <= 500
        and "\n" not in _trimmed
        and (_trimmed.startswith("https://") or _trimmed.startswith("http://"))
    ):
        _probe = await google_link_service.validate_link(_trimmed)
        if _probe.ok:
            req = req.model_copy(update={"url": _trimmed, "content": None})
            content = ""
            source_url = _trimmed

    # Async path 2-action:
    #   Action 1 (sync, ~3-10s): VALIDATE URL + FETCH content + CHECK valid data
    #   Action 2 (async background): chunk + enrich + embed (worker)
    # Trả 202 sau Action 1 thành công → UI biết "đã nhận tài liệu valid".
    if req.url and not req.content:
        # ─── Action 1a: Validate link (HTTP probe ~1-2s) ───
        validation = await google_link_service.validate_link(req.url)
        if not validation.ok:
            raise HTTPException(status_code=400, detail=validation.error)
        source_url = req.url

        # ─── Action 1b: Fetch + verify content có data ───
        # Đây là quan trọng — verify document THẬT SỰ có nội dung trước khi
        # queue worker. Nếu fetch fail / content rỗng → trả 400 NGAY, không
        # queue job rác. User biết ngay "đọc được data không".
        try:
            fetched = await google_link_service.fetch_content(req.url, validation.doc_type)
        except Exception as exc:  # noqa: BLE001 — fetch is external, fail-loud to client
            raise HTTPException(status_code=400, detail=f"Không thể lấy nội dung tài liệu: {str(exc)[:200]}") from exc
        if not fetched or not fetched.strip():
            raise HTTPException(status_code=400, detail="Tài liệu trống — không có data để xử lý")

        _content_chars = len(fetched)

        # ─── Action 1c: Size check (fail fast trước khi queue) ───
        # Worker sẽ check max_ingest_content_chars; nếu Action 1 không check
        # trước, queue job sẽ fail ở worker → user thấy state="failed" sau
        # 30s. Fail fast ở đây tốt hơn.
        try:
            container = request.app.state.container
            _cfg = container.system_config_service()
            _max_chars = await _cfg.get_int("max_ingest_content_chars", 2_000_000)
        except (AttributeError, Exception):  # noqa: BLE001 — fallback default
            _max_chars = 2_000_000
        if _content_chars > _max_chars:
            raise HTTPException(
                status_code=413,  # Payload Too Large
                detail=(
                    f"Tài liệu quá lớn: {_content_chars:,} ký tự (max {_max_chars:,}). "
                    f"Vui lòng tách thành nhiều tài liệu nhỏ hơn, hoặc liên hệ admin "
                    f"để tăng limit cho tenant."
                ),
            )

        logger.info("ingest_action1_validated",
                    source_url=req.url[:200],
                    doc_type=validation.doc_type,
                    content_chars=_content_chars,
                    max_chars=_max_chars)

        # ─── Action 2: INSERT documents row + emit event 202 ngay ───
        # Doc phải LƯU NGAY sau fetch valid, KHÔNG mất khi worker fail/restart —
        # F5 list documents phải thấy doc với status="preparing".
        #
        # KHÔNG dùng IngestDocumentUseCase vì idempotency cache (job_id match URL +
        # tenant) khiến retry KHÔNG INSERT row mới khi job cũ đã failed → mất
        # visibility. Strategy: INSERT documents trực tiếp (state="DRAFT").
        # raw_content được lưu cho visibility/size, NHƯNG worker chỉ tái dùng nó cho
        # nguồn local://; URL Google luôn được worker re-fetch + parse STRUCTURED.
        import uuid as _uuid  # noqa: PLC0415
        from ragbot.shared.types import TraceId  # noqa: PLC0415

        record_tenant = getattr(request.state, "record_tenant_id", None)
        if record_tenant is None:
            raise HTTPException(status_code=403, detail="missing tenant context")

        document_id = _uuid.uuid4()
        trace_id = getattr(request.state, "trace_id", None) or str(_uuid.uuid4())

        # INSERT documents row + outbox event in single transaction
        from sqlalchemy import text as _sql_text  # noqa: PLC0415
        import hashlib as _hashlib  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        content_hash = _hashlib.sha256(fetched.encode("utf-8")).hexdigest()
        # 4-key: honour the bot's workspace from the request (per-bot workspace
        # seed), falling back to the tenant slug when omitted (back-compat).
        workspace_slug = resolve_workspace_id(
            req.workspace_id, record_tenant_id=record_tenant,
        )

        sf = _sf(request)
        async with sf() as session:
            # Check if doc with same content_hash đã tồn tại + active
            existing = await session.execute(_sql_text("""
                SELECT id, state FROM documents
                WHERE record_bot_id = :bot_id AND content_hash = :ch
                  AND deleted_at IS NULL
                LIMIT 1
            """), {"bot_id": bot_uuid, "ch": content_hash})
            row = existing.fetchone()
            if row:
                # Idempotent return — doc cùng content đã exist
                return {
                    "ok": True,
                    "validation": {
                        "valid": True,
                        "content_chars": _content_chars,
                        "doc_type": validation.doc_type,
                        "message": "Tài liệu này đã tồn tại, không upload lại.",
                    },
                    "document_id": str(row[0]),
                    "state": row[1],
                    "status": "ready" if row[1] == "active" else "preparing",
                    "ready": row[1] == "active",
                    "message": f"Doc đã có sẵn (state={row[1]}). Không insert duplicate.",
                }

            # Per-tenant daily document quota gate (alembic 010i / vấn đề 6C
            # multi-tenant fairness). Atomic SELECT FOR UPDATE + UPDATE
            # inside the same tx that INSERTs the document → quota debit
            # cannot leak past a failed INSERT, and the row lock prevents
            # two concurrent uploads from double-spending.
            from ragbot.application.services.ingest_quota_service import (  # noqa: PLC0415
                IngestQuotaService,
            )
            from ragbot.shared.errors import QuotaExceeded as _QuotaExceeded  # noqa: PLC0415
            try:
                _quota_count, _quota_limit = await IngestQuotaService().check_and_increment(
                    session, record_tenant_id=record_tenant,
                )
            except _QuotaExceeded as exc:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "INGEST_DAILY_QUOTA_EXCEEDED",
                        "message": str(exc),
                        "retry_after_hint": "next UTC midnight rollover",
                    },
                ) from exc

            # Hard-delete soft-deleted doc cùng tool_name to avoid
            # uq_doc_tool UNIQUE(tenant, bot, tool_name) violation.
            # uq_doc_tool is NOT partial (no WHERE deleted_at IS NULL),
            # so a soft-deleted row still blocks re-upload with same title.
            await session.execute(_sql_text("""
                DELETE FROM documents
                WHERE record_tenant_id = :tenant_id
                  AND record_bot_id = :bot_id
                  AND tool_name = :tool_name
                  AND deleted_at IS NOT NULL
            """), {
                "tenant_id": record_tenant,
                "bot_id": bot_uuid,
                "tool_name": req.title[:255],
            })

            # mime_type reflects the EXPORT format ``fetch_content`` already used
            # (``to_export_url``): a Sheet's ``raw_content`` is CSV → ``text/csv``
            # so the ingest routes it to GoogleSheetsParser (row-as-chunk), NOT the
            # HTML parser. A Doc is re-fetched + parsed structured by the worker, so
            # a generic placeholder is fine. The old hardcoded ``text/html`` made
            # EVERY uploaded sheet ingest as HTML (multi-row chunks → value mis-bind).
            _upload_mime = "text/csv" if validation.doc_type == "sheets" else "text/html"
            # INSERT new doc state="DRAFT" with raw_content saved
            await session.execute(_sql_text("""
                INSERT INTO documents (
                    id, record_tenant_id, workspace_id, record_bot_id,
                    source_url, document_name, tool_name, mime_type,
                    language, state, version, content_hash, acl,
                    metadata_json, content_chars, raw_content
                ) VALUES (
                    :id, :tenant_id, :workspace_id, :bot_id,
                    :source_url, :document_name, :tool_name, :mime_type,
                    'vi', 'DRAFT', 1, :content_hash, '{}',
                    CAST('{}' AS jsonb), :content_chars, :raw_content
                )
            """), {
                "id": document_id,
                "tenant_id": record_tenant,
                "workspace_id": workspace_slug,
                "bot_id": bot_uuid,
                "source_url": source_url,
                "document_name": req.title,
                "tool_name": req.title[:255],
                "mime_type": _upload_mime,
                "content_hash": content_hash,
                "content_chars": _content_chars,
                "raw_content": fetched,
            })

            # INSERT outbox event document.uploaded.v1 → worker pickup
            job_id = _uuid.uuid4()
            event_payload = {
                "event_id": str(_uuid.uuid4()),
                "event_type": "document.uploaded.v1",
                "schema_version": 1,
                "occurred_at": _dt.now(tz=_tz.utc).isoformat(),
                "record_tenant_id": str(record_tenant),
                "trace_id": trace_id,
                "workspace_id": workspace_slug,
                "job_id": str(job_id),
                "record_bot_id": str(bot_uuid),
                "document_id": str(document_id),
                "source_url": source_url,
                "document_name": req.title,
                "tool_name": req.title[:255],
                "mime_type": "text/html",
                "uploaded_by": getattr(request.state, "user_id", "demo"),
                "force_reingest": False,
            }
            await session.execute(_sql_text("""
                INSERT INTO outbox (
                    id, subject, payload, headers, trace_id,
                    record_tenant_id, workspace_id, channel_type,
                    retry_count, status, metadata_json
                ) VALUES (
                    :id, 'document.uploaded.v1', :payload,
                    CAST('{}' AS jsonb), :trace_id,
                    :tenant_id, :workspace_id, 'web', 0, 'pending',
                    CAST('{}' AS jsonb)
                )
            """), {
                "id": _uuid.uuid4(),
                "payload": _json.dumps(event_payload).encode("utf-8"),  # bytea column
                "trace_id": trace_id,
                "tenant_id": record_tenant,
                "workspace_id": workspace_slug,
            })
            await session.commit()

        logger.info("ingest_action2_doc_persisted",
                    document_id=str(document_id),
                    job_id=str(job_id),
                    state="DRAFT")

        # Operator debug aid (2026-05-18): dump parsed Markdown to disk so
        # operators can open the file in any text editor to debug chunking
        # decisions (table-with-footer, heading hierarchy, line-break issues).
        # Disk write is fail-soft — DB row is already committed source-of-truth.
        from ragbot.application.services.parsed_md_dump import dump_parsed_md  # noqa: PLC0415
        from datetime import datetime as _now_dt, timezone as _now_tz  # noqa: PLC0415
        dump_parsed_md(
            record_tenant_id=record_tenant,
            document_id=document_id,
            document_name=req.title,
            source_url=source_url,
            bot_id=bot_id,
            channel_type=channel_type,
            content=fetched,
            uploaded_at=_now_dt.now(tz=_now_tz.utc).isoformat(),
        )

        return {
            "ok": True,
            "validation": {
                "valid": True,
                "content_chars": _content_chars,
                "doc_type": validation.doc_type,
                "message": "Đã đọc data từ tài liệu, hợp lệ.",
            },
            "document_id": str(document_id),
            "job_id": str(job_id),
            "state": "DRAFT",
            "status": "preparing",
            "ready": False,
            "message": "Tài liệu đã lưu (state=DRAFT, đang chuẩn bị dữ liệu). Worker đang chunk + embed background. F5 list để xem progress.",
        }

    # Inline content path (small text body): keep sync since no fetch needed
    # and chunking small content is fast.
    if not content.strip():
        raise HTTPException(status_code=400, detail="Nội dung tài liệu trống")

    doc_svc = _doc_service(request)
    result = await doc_svc.ingest(
        record_bot_id=bot_uuid, title=req.title, content=content,
        source_url=source_url, source_type=req.source_type,
        record_tenant_id=getattr(request.state, "record_tenant_id", None),
    )
    return {"ok": True, "document_id": str(result.document_id), "title": result.title,
            "chunks": result.chunks, "embedded": result.embedded, "status": "inline"}


@router.post("/bots/{bot_id}/{channel_type}/documents/upload")
async def upload_document_file(
    bot_id: str,
    channel_type: str,
    request: Request,
    title: str = Form(...),  # type: ignore[name-defined]  # noqa: F821 — Form imported below
    file: UploadFile = File(...),
    workspace_id: str | None = Form(default=None),  # 4-key: per-bot workspace
) -> dict:
    """Upload a binary document (.pdf etc.) — routes through the parser
    registry inside ``DocumentService.ingest`` so the per-format size guard
    + asyncio Semaphore in ``pdf_parser`` are exercised. Plain-text bodies
    keep using ``add_document`` above.

    Workspace-aware (parity with ``add_document``): a bot living in a non-default
    workspace (post alembic 0213) can only be resolved with its ``workspace_id``;
    omitting it falls back to the tenant-default slug.
    """
    record_tenant = getattr(request.state, "record_tenant_id", None)
    bot_uuid = await _find_bot_uuid(
        request, bot_id, channel_type, workspace_id=workspace_id,
    )
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    mime = (file.content_type or "").strip() or "application/octet-stream"
    workspace_slug = resolve_workspace_id(
        workspace_id, record_tenant_id=record_tenant,
    )
    doc_svc = _doc_service(request)
    try:
        result = await doc_svc.ingest(
            record_bot_id=bot_uuid,
            title=title or file.filename,
            content="",
            source_url="",
            source_type="upload",
            mime_type=mime,
            raw_bytes=raw,
            file_name=file.filename,
            record_tenant_id=record_tenant,
            workspace_id=workspace_slug,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "document_id": str(result.document_id),
        "title": result.title,
        "chunks": result.chunks,
        "embedded": result.embedded,
        "filename": file.filename,
        "bytes": len(raw),
    }


@router.delete("/documents/{doc_uuid}")
async def delete_document(doc_uuid: str, request: Request) -> dict:
    """Xóa tài liệu và các chunk liên quan.
    @param doc_uuid: UUID của tài liệu
    @return: {ok: true}
    """
    try:
        did = uuid.UUID(doc_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document UUID")

    sf = _sf(request)
    async with sf() as session:
        result = await session.execute(
            text("SELECT id FROM documents WHERE id = :id AND deleted_at IS NULL"), {"id": did},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Document not found")

    doc_svc = _doc_service(request)
    await doc_svc.delete_document(
        did,
        record_tenant_id=getattr(request.state, "record_tenant_id", None),
    )
    return {"ok": True}


__all__ = [
    "router",
    "list_documents",
    "add_document",
    "upload_document_file",
    "delete_document",
]
