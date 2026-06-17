"""Bot CRUD + per-bot config routes for the test_chat package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). Covers
bot list/create/update/delete, callback-format, max-history, chunking-info and
custom-vocabulary. Audit / question-gen / quality-dashboard live in the sibling
``bot_insights_routes`` module to keep both files focused (and under the size cap).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, func, select, text

from ragbot.infrastructure.db.models import BotModel, DocumentModel
from ragbot.infrastructure.db.models_monitoring import (
    _document_chunks_table_ref as _doc_chunks_table,
)
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.shared.bot_bindings import ensure_bot_bindings
from ragbot.shared.rbac import check_min_level
from ragbot.shared.constants import (
    DEFAULT_ENRICHMENT_MODEL,
    DEFAULT_MAX_HISTORY,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

from .schemas import (
    CreateBotRequest,
    UpdateBotRequest,
    UpdateMaxHistoryRequest,
    UpdateVocabularyRequest,
)
from ._shared import (
    _PLATFORM_TENANT_FALLBACK_UUID,
    _container,
    _doc_service,
    _find_bot_uuid,
    _page_limit,
    _require_owner,
    _sf,
    _sys_config,
    _tenant_scope,
    logger,
)

router = APIRouter(tags=["test"])


@router.get("/bots")
async def list_bots(request: Request, limit: int | None = None) -> dict:
    """Liệt kê tất cả bot đang hoạt động kèm số doc/chunk.
    @param limit: giới hạn số lượng kết quả
    @return: danh sách bot với thống kê
    """
    page = _page_limit(limit)
    sf = _sf(request)
    scope = _tenant_scope(request)
    # INJ-10 — ORM builder replaces raw text(); identifiers come from mapper
    # metadata so caller-supplied values cannot inject SQL. Counts use
    # ``func.count(DISTINCT col)`` with a CASE for the soft-delete filter on
    # documents, matching the original ``FILTER (WHERE ...)`` clause.
    doc_count_expr = func.count(
        func.distinct(
            case((DocumentModel.deleted_at.is_(None), DocumentModel.id), else_=None),
        ),
    ).label("doc_count")
    chunk_count_expr = func.count(
        func.distinct(_doc_chunks_table.c.id),
    ).label("chunk_count")

    stmt = (
        select(
            BotModel.id,
            BotModel.bot_id,
            BotModel.channel_type,
            BotModel.bot_name,
            BotModel.record_tenant_id,
            BotModel.system_prompt,
            BotModel.setting_options,
            BotModel.created_at,
            BotModel.updated_at,
            doc_count_expr,
            chunk_count_expr,
            BotModel.max_history,
            BotModel.max_documents,
            BotModel.prompt_max_tokens,
            BotModel.rerank_top_n,
            BotModel.plan_limits,
            BotModel.bypass_token_limit,
            BotModel.bypass_rate_limit,
        )
        .select_from(BotModel)
        .outerjoin(DocumentModel, DocumentModel.record_bot_id == BotModel.id)
        .outerjoin(
            _doc_chunks_table,
            _doc_chunks_table.c.record_document_id == DocumentModel.id,
        )
        .where(BotModel.is_deleted.is_(False))
        .group_by(BotModel.id)
        .order_by(BotModel.created_at.desc())
        .limit(page)
    )
    if scope is not None:
        stmt = stmt.where(BotModel.record_tenant_id == scope)

    async with sf() as session:
        result = await session.execute(stmt)
        rows = result.mappings().all()
    return {
        "ok": True,
        "data": [
            {
                "id": str(r["id"]),
                "bot_id": r["bot_id"],
                "channel_type": r["channel_type"],
                "bot_name": r["bot_name"],
                "record_tenant_id": (
                    str(r["record_tenant_id"]) if r["record_tenant_id"] else None
                ),
                "system_prompt": r["system_prompt"] or "",
                "setting_options": r["setting_options"] or {},
                "created_at": (
                    r["created_at"].isoformat() if r["created_at"] else None
                ),
                "updated_at": (
                    r["updated_at"].isoformat() if r["updated_at"] else None
                ),
                "doc_count": r["doc_count"],
                "chunk_count": r["chunk_count"],
                "max_history": r["max_history"],
                "max_documents": r["max_documents"],
                "prompt_max_tokens": r["prompt_max_tokens"],
                "rerank_top_n": r["rerank_top_n"],
                "plan_limits": r["plan_limits"] or {},
                "bypass_token_limit": bool(r["bypass_token_limit"]),
                "bypass_rate_limit": bool(r["bypass_rate_limit"]),
            }
            for r in rows
        ],
    }


@router.post("/bots")
async def create_bot(req: CreateBotRequest, request: Request) -> dict:
    """Tạo bot mới với model bindings mặc định.
    @param req: thông tin bot — tenant lifted from JWT bearer
    @return: {ok, bot_uuid, bot_id}
    """
    container = _container(request)
    repo = container.bot_repo()

    # Tenant authority is JWT bearer (lifted by middleware); platform
    # admin without scope falls back to placeholder UUID for the FK row.
    scope = _tenant_scope(request)
    if scope is None:
        _require_owner(request)
    record_tenant_uuid = scope or _PLATFORM_TENANT_FALLBACK_UUID
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )

    # Hard-delete soft-deleted bot same 4-key to allow recreate.
    # `uq_bots_record_tenant_workspace_bot_channel` is NOT partial — a
    # soft-deleted row (is_deleted=true) still occupies the slot and
    # blocks INSERT with UniqueViolationError. Mirrors pattern used in
    # documents POST endpoint (commit baac301).
    #
    # If the existing row is ACTIVE (is_deleted=false), raise 409 —
    # caller must explicitly DELETE first if intent is recreate.
    sf = _sf(request)
    async with sf() as session:
        existing_row = await session.execute(
            text(
                "SELECT id, is_deleted FROM bots "
                "WHERE record_tenant_id = :tid "
                "  AND workspace_id = :ws "
                "  AND bot_id = :bid "
                "  AND channel_type = :ch "
                "LIMIT 1",
            ),
            {
                "tid": record_tenant_uuid,
                "ws": workspace_slug,
                "bid": req.bot_id,
                "ch": req.channel_type,
            },
        )
        existing_db = existing_row.fetchone()
        if existing_db:
            if not existing_db[1]:
                # Active bot exists — refuse to overwrite
                raise HTTPException(
                    status_code=409,
                    detail=f"Bot {req.bot_id}:{req.channel_type} already exists (active). DELETE first if intent is recreate.",
                )
            # Soft-deleted → hard-delete to free unique slot.
            # Cascade: chunks + documents already soft-deleted with bot;
            # bot_model_bindings + audit_log FK ON DELETE CASCADE.
            await session.execute(
                text("DELETE FROM bots WHERE id = :id"),
                {"id": existing_db[0]},
            )
            await session.commit()
            logger.info(
                "bot_create_recycled_soft_deleted_row",
                bot_id=req.bot_id,
                channel_type=req.channel_type,
                previous_uuid=str(existing_db[0]),
            )

    # Get default models. ai_models.kind uses "llm" for chat/generation
    # models (NOT "chat"); the prior literal mismatched the schema and
    # left newly-created bots without an LLM binding, which made the
    # resolver fall back through every purpose and 500 on first chat.
    sf = _sf(request)
    async with sf() as session:
        result = await session.execute(
            text("SELECT id, kind FROM ai_models WHERE enabled = true AND kind IN ('llm', 'embedding')"),
        )
        model_id = embedding_model_id = None
        for row in result.fetchall():
            if row[1] == "llm" and model_id is None:
                model_id = row[0]
            elif row[1] == "embedding" and embedding_model_id is None:
                embedding_model_id = row[0]

    cfg_svc = _sys_config(request)
    default_temp = await cfg_svc.get_float("llm_default_temperature", 0.3)
    default_max_tok = await cfg_svc.get_int("llm_default_max_tokens", 450)
    default_top_p = await cfg_svc.get_float("llm_default_top_p", 0.4)

    # Validate callback_url if provided
    if req.callback_url:
        from ragbot.shared.callback_validator import validate_callback_url
        ok, msg = await validate_callback_url(req.callback_url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Callback URL validation failed: {msg}")

    setting_options = {
        "frequency_penalty": 0,
        "max_tokens": req.max_tokens if req.max_tokens != 450 else default_max_tok,
        "response_format": "text", "presence_penalty": 0,
        "temperature": req.temperature if req.temperature != 0.3 else default_temp,
        "top_p": default_top_p,
    }

    cfg = await repo.create_bot(
        bot_id=req.bot_id, channel_type=req.channel_type, bot_name=req.bot_name,
        record_tenant_id=record_tenant_uuid,
        workspace_id=workspace_slug,
        model_id=model_id,
        embedding_model_id=embedding_model_id,
        system_prompt=req.system_prompt, setting_options=setting_options,
        callback_url=req.callback_url,
    )

    # Setting bypass_token_limit requires tenant level (80). On 403 we
    # roll back the just-created row so the API stays atomic.
    if req.bypass_token_limit:
        if not check_min_level(request, 80):
            async with sf() as session:
                await session.execute(
                    text("DELETE FROM bots WHERE id = :bid"),
                    {"bid": cfg.id},
                )
                await session.commit()
            raise HTTPException(
                status_code=403,
                detail="bypass_token_limit requires tenant/admin level",
            )
        async with sf() as session:
            await session.execute(
                text("UPDATE bots SET bypass_token_limit = true WHERE id = :bid"),
                {"bid": cfg.id},
            )
            await session.commit()

    # Auto-create bindings
    if model_id or embedding_model_id:
        async with sf() as session:
            await ensure_bot_bindings(
                session, cfg.id, model_id, embedding_model_id,
                record_tenant_id=record_tenant_uuid,
                temperature=req.temperature, max_tokens=req.max_tokens,
            )
            await session.commit()

    registry = container.bot_registry_service()
    await registry.invalidate(
        cfg.record_tenant_id, cfg.workspace_id, req.bot_id, req.channel_type,
    )
    return {"ok": True, "bot_uuid": str(cfg.id), "bot_id": req.bot_id}


@router.patch("/bots/{bot_uuid}")
async def update_bot(bot_uuid: str, req: UpdateBotRequest, request: Request) -> dict:
    """Cập nhật cài đặt bot (tên, prompt, temperature, max_tokens).
    @param bot_uuid: UUID của bot cần cập nhật
    @return: {ok: true}
    """
    container = _container(request)
    repo = container.bot_repo()
    try:
        bid = uuid.UUID(bot_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bot UUID")

    scope = _tenant_scope(request)
    cfg = await repo.get_by_id(bid, record_tenant_id=scope)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    fields: dict[str, Any] = {}
    if req.bot_name is not None:
        fields["bot_name"] = req.bot_name
    if req.system_prompt is not None:
        fields["system_prompt"] = req.system_prompt

    current_opts = cfg.setting_options.model_dump() if cfg.setting_options else {}
    if req.temperature is not None:
        current_opts["temperature"] = req.temperature
    if req.max_tokens is not None:
        current_opts["max_tokens"] = req.max_tokens
    if req.temperature is not None or req.max_tokens is not None:
        fields["setting_options"] = current_opts
    if req.max_history is not None:
        fields["max_history"] = req.max_history
    if req.max_documents is not None:
        fields["max_documents"] = req.max_documents
    if req.prompt_max_tokens is not None:
        fields["prompt_max_tokens"] = req.prompt_max_tokens
    if req.rerank_top_n is not None:
        fields["rerank_top_n"] = req.rerank_top_n
    if req.plan_limits is not None:
        from ragbot.shared.bot_limits import validate_plan_limits
        try:
            fields["plan_limits"] = validate_plan_limits(req.plan_limits)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if req.callback_url is not None:
        if req.callback_url:
            from ragbot.shared.callback_validator import validate_callback_url
            ok, msg = await validate_callback_url(req.callback_url)
            if not ok:
                raise HTTPException(status_code=400, detail=f"Callback URL validation failed: {msg}")
        fields["callback_url"] = req.callback_url or None
    if req.bypass_token_limit is not None:
        if not check_min_level(request, 80):
            raise HTTPException(status_code=403, detail="bypass_token_limit requires tenant/admin level")
        fields["bypass_token_limit"] = req.bypass_token_limit
    if req.bypass_rate_limit is not None:
        if not check_min_level(request, 80):
            raise HTTPException(status_code=403, detail="bypass_rate_limit requires tenant/admin level")
        fields["bypass_rate_limit"] = req.bypass_rate_limit

    if fields:
        # Forensic audit log — capture BEFORE state for tamper-evident trail
        # (CLAUDE.md Application MINDSET rule 7: every mutation to
        # bots.system_prompt / oos_answer_template must leave an audit row).
        before_audit = {
            k: getattr(cfg, k, None)
            for k in fields.keys()
            if hasattr(cfg, k)
        }
        # JSON-serialise non-primitive values so jsonb stores canonical text.
        before_audit = {
            k: (v.model_dump() if hasattr(v, "model_dump") else v)
            for k, v in before_audit.items()
        }
        await repo.update_bot(bid, record_tenant_id=scope, **fields)
        # Audit row hash-chained per alembic 010g. Failure raises (no swallow).
        async with _sf(request)() as audit_session:
            await insert_audit_row(
                audit_session,
                record_tenant_id=scope,
                workspace_id=getattr(cfg, "workspace_id", "system"),
                actor_user_id=str(getattr(request.state, "user_id", "unknown")),
                action="update",
                resource_type="bot",
                resource_id=str(bid),
                before_json=before_audit,
                after_json=fields,
                reason="admin_ui_update_bot",
                trace_id=getattr(request.state, "trace_id", None),
            )
            await audit_session.commit()

    # Trim history ngay khi max_history giảm (không cần cron)
    if req.max_history is not None:
        system_max = await _sys_config(request).get_int("chat_max_history", DEFAULT_MAX_HISTORY)
        effective_max = max(req.max_history, system_max) if req.max_history > 0 else system_max
        async with _sf(request)() as session:
            # Tìm rooms của bot này có quá limit
            rooms = (await session.execute(
                text("""
                    SELECT channel_type, connect_id, count(*) as cnt
                    FROM chat_histories
                    WHERE record_bot_id = :bid
                    GROUP BY channel_type, connect_id
                    HAVING count(*) > :max
                """),
                {"bid": cfg.id, "max": effective_max},
            )).fetchall()
            for room in rooms:
                await session.execute(
                    text("""
                        DELETE FROM chat_histories WHERE id IN (
                            SELECT id FROM chat_histories
                            WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                            ORDER BY id DESC OFFSET :keep
                        )
                    """),
                    {"bid": cfg.id, "ch": room[0], "cid": room[1], "keep": effective_max},
                )
            if rooms:
                await session.commit()
                logger.info("bot_history_trimmed", bot_id=cfg.bot_id, rooms=len(rooms), max=effective_max)

    registry = container.bot_registry_service()
    await registry.invalidate(
        cfg.record_tenant_id, cfg.workspace_id, cfg.bot_id, cfg.channel_type,
    )
    return {"ok": True}


@router.get("/bots/{bot_id}/{channel_type}/callback-format")
async def get_callback_format(bot_id: str, channel_type: str, request: Request) -> dict:
    """Return expected callback response format for integration.
    @param bot_id: bot identifier
    @param channel_type: channel type
    @return: callback format spec
    """
    from ragbot.shared.callback_validator import CALLBACK_TEST_PAYLOAD
    return {
        "ok": True,
        "callback_format": CALLBACK_TEST_PAYLOAD["expected_response_format"],
        "description": "Your callback endpoint should accept POST with this format and return 200.",
    }


@router.delete("/bots/{bot_uuid}")
async def delete_bot(bot_uuid: str, request: Request) -> dict:
    """Xóa mềm bot (bot mặc định được bảo vệ).
    @param bot_uuid: UUID của bot cần xóa
    @return: {ok: true}
    """
    container = _container(request)
    repo = container.bot_repo()
    try:
        bid = uuid.UUID(bot_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bot UUID")

    scope = _tenant_scope(request)
    cfg = await repo.get_by_id(bid, record_tenant_id=scope)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    # DELETE flow per admin mandate 2026-05-13:
    #   - CLEAR transient data: chunks, documents, semantic_cache, chat_histories
    #     (chiếm dung lượng + invalidate stale retrieve)
    #   - PRESERVE forensic/billing: request_logs (cost + tokens), audit_log
    #     (compliance trail) — KHÔNG có FK tới bots nên hard-delete bot row
    #     không ảnh hưởng
    #   - HARD-DELETE bot row (was soft-delete) — vì uq_bots_4key NOT partial,
    #     soft-deleted row vẫn block recreate; cascade bot_model_bindings
    doc_svc = _doc_service(request)
    await doc_svc.delete_all_for_bot(
        bid,
        record_tenant_id=getattr(request.state, "record_tenant_id", None),
    )

    registry = container.bot_registry_service()
    await registry.invalidate(
        cfg.record_tenant_id, cfg.workspace_id, cfg.bot_id, cfg.channel_type,
    )

    sf = _sf(request)
    async with sf() as session:
        # Delete chat histories for THIS bot on THIS channel only.
        await session.execute(
            text(
                "DELETE FROM chat_histories "
                "WHERE record_bot_id = :bid AND channel_type = :ch",
            ),
            {"bid": bid, "ch": cfg.channel_type},
        )
        # Hard-delete bot row → frees uq_bots_4key slot for recreate.
        # FK cascade: bot_model_bindings ON DELETE CASCADE.
        # request_logs + audit_log have NO FK to bots — cost/billing
        # history fully preserved.
        await session.execute(
            text("DELETE FROM bots WHERE id = :bid"),
            {"bid": bid},
        )
        await session.commit()

    logger.info(
        "bot_deleted_hard",
        bot_uuid=str(bid),
        bot_id=cfg.bot_id,
        channel_type=cfg.channel_type,
        preserved_tables=["request_logs", "audit_log"],
    )
    return {"ok": True}


@router.put("/bots/{bot_id}/{channel_type}/max-history")
async def update_bot_max_history(
    bot_id: str, channel_type: str, body: UpdateMaxHistoryRequest, request: Request,
    workspace_id: str | None = None,  # 4-key: per-bot workspace
) -> dict:
    """Cập nhật max_history cho bot theo bot_id + channel_type.

    Key: bot_id + channel_type (không dùng UUID).
    Bắt buộc truyền int >= 1.
    Trim history ngay nếu max_history giảm (không cần cron).

    @param bot_id: Bot ID (e.g. "<demo-bot-slug>")
    @param channel_type: Channel type (e.g. "web", "zalo")
    @param body: {"max_history": 20}
    @return: {ok, bot_id, channel_type, max_history, trimmed_rooms}
    """
    _require_owner(request)
    bot_uuid = await _find_bot_uuid(request, bot_id, channel_type, workspace_id=workspace_id)
    repo = _container(request).bot_repo()
    scope = _tenant_scope(request)
    cfg = await repo.get_by_id(bot_uuid, record_tenant_id=scope)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id}:{channel_type} not found")

    # Update DB
    await repo.update_bot(bot_uuid, record_tenant_id=scope, max_history=body.max_history)

    # Trim history ngay (thêm đuôi cắt đầu — không cần cron)
    system_max = await _sys_config(request).get_int("chat_max_history", DEFAULT_MAX_HISTORY)
    effective_max = max(body.max_history, system_max)
    trimmed_rooms = 0
    async with _sf(request)() as session:
        rooms = (await session.execute(
            text("""
                SELECT channel_type, connect_id, count(*) as cnt
                FROM chat_histories
                WHERE record_bot_id = :bid
                GROUP BY channel_type, connect_id
                HAVING count(*) > :max
            """),
            {"bid": bot_uuid, "max": effective_max},
        )).fetchall()
        for room in rooms:
            await session.execute(
                text("""
                    DELETE FROM chat_histories WHERE id IN (
                        SELECT id FROM chat_histories
                        WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                        ORDER BY id DESC OFFSET :keep
                    )
                """),
                {"bid": bot_uuid, "ch": room[0], "cid": room[1], "keep": effective_max},
            )
        if rooms:
            await session.commit()
            trimmed_rooms = len(rooms)

    # Invalidate Redis cache (tenant-scoped — P24-L2)
    registry = _container(request).bot_registry_service()
    await registry.invalidate(
        cfg.record_tenant_id, cfg.workspace_id, bot_id, channel_type,
    )

    return {
        "ok": True,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "max_history": body.max_history,
        "effective_max": effective_max,
        "trimmed_rooms": trimmed_rooms,
    }


@router.get("/bots/{bot_id}/{channel_type}/chunking-info")
async def chunking_info(
    bot_id: str, channel_type: str, request: Request,
    sample_per_doc: int = 0,  # 0 = trả về TẤT CẢ chunks (accordion UI)
    max_chunks_per_doc: int = 1000,  # hard cap để tránh response > 5MB
    workspace_id: str | None = None,  # 4-key: per-bot workspace
) -> dict:
    """Trả về thông tin chunking strategy + sample chunks cho mỗi doc của bot.

    Inference strategy từ DB (parent_child mode, Haiku prefix pattern,
    avg/min/max chunk size) — KHÔNG cần re-ingest. Show ra UI tab Cấu hình.
    """
    bot_uuid = await _find_bot_uuid(request, bot_id, channel_type, workspace_id=workspace_id)
    cfg_svc = _sys_config(request)

    # 1. System-wide config snapshot (resolve chain default)
    sys_cfg = {
        "parent_child_enabled": await cfg_svc.get_bool("parent_child_enabled", True),
        "parent_chunk_size": await cfg_svc.get_int("parent_chunk_size", 1024),
        "child_chunk_size": await cfg_svc.get_int("child_chunk_size", 256),
        "child_chunk_overlap": await cfg_svc.get_int("child_chunk_overlap", 50),
        "enrichment_enabled": await cfg_svc.get_bool("enrichment_enabled", True),
        "enrichment_model": str(await cfg_svc.get("enrichment_model") or DEFAULT_ENRICHMENT_MODEL),
        "contextual_retrieval_enabled": await cfg_svc.get_bool(
            "contextual_retrieval_enabled", True,
        ),
        "contextual_retrieval_max_doc_chars": await cfg_svc.get_int(
            "contextual_retrieval_max_doc_chars", 50000,
        ),
        "late_chunking_enabled": await cfg_svc.get_bool("late_chunking_enabled", True),
        "late_chunking_context_chars": await cfg_svc.get_int(
            "late_chunking_context_chars", 200,
        ),
        "chunk_size": await cfg_svc.get_int("chunk_size", 512),
    }

    # 2. Per-doc chunk inspection
    sf = _sf(request)
    async with sf() as session:
        # List active docs of this bot
        doc_rows = (await session.execute(
            text("""
                SELECT id, document_name, content_chars, created_at
                FROM documents
                WHERE record_bot_id = :bid AND deleted_at IS NULL
                ORDER BY created_at DESC
            """),
            {"bid": bot_uuid},
        )).fetchall()

        docs_info = []
        for doc_row in doc_rows:
            doc_id = doc_row[0]
            # Aggregate stats for this doc
            stats_row = (await session.execute(
                text("""
                    SELECT
                      COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
                      COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM document_chunks ch
                        WHERE ch.parent_chunk_id = document_chunks.id
                      )) AS parents,
                      ROUND(AVG(LENGTH(content)))::int AS avg_chars,
                      MIN(LENGTH(content)) AS min_chars,
                      MAX(LENGTH(content)) AS max_chars,
                      COUNT(*) FILTER (
                        WHERE content LIKE 'Đoạn %nằm trong%'
                           OR content LIKE 'Đoạn %/%:%'
                           OR content LIKE 'Vị trí:%'
                      ) AS haiku_enriched
                    FROM document_chunks
                    WHERE record_document_id = :did
                """),
                {"did": doc_id},
            )).fetchone()

            total = int(stats_row[0]) if stats_row else 0
            embedded = int(stats_row[1]) if stats_row else 0
            parents = int(stats_row[2]) if stats_row else 0
            avg_chars = int(stats_row[3]) if stats_row and stats_row[3] else 0
            min_chars = int(stats_row[4]) if stats_row and stats_row[4] else 0
            max_chars = int(stats_row[5]) if stats_row and stats_row[5] else 0
            haiku_count = int(stats_row[6]) if stats_row else 0

            # Detect strategy heuristically — distinguish parent-child WITH HDT
            # path (chunks starting with "[Chapter > ...]") vs flat recursive
            # parent-child. Without this, every parent-child doc was mislabeled
            # "HDT mode" regardless of whether structural paths were preserved.
            detected = "unknown"
            if total == 0:
                detected = "no chunks (pending)"
            elif parents > 0 and parents > total * 0.1:
                # Count parents that actually carry HDT structural path
                # ([Chương > Mục > Điều] prefix).
                hdt_path_row = (await session.execute(
                    text(
                        "SELECT COUNT(*) FROM document_chunks "
                        "WHERE record_document_id=:did "
                        "AND parent_chunk_id IS NULL "
                        "AND content LIKE '[%]%'"
                    ),
                    {"did": doc_id},
                )).first()
                hdt_parents = int(hdt_path_row[0]) if hdt_path_row else 0
                if hdt_parents > parents * 0.5:
                    detected = "parent_child + HDT (structural path)"
                else:
                    detected = "parent_child (flat recursive)"
            elif max_chars > 1500 and avg_chars > 800:
                detected = "table_csv (row-as-chunk)"
            elif min_chars < 100 and avg_chars < 300:
                detected = "proposition / semantic"
            elif avg_chars > 400 and avg_chars < 800:
                detected = "recursive (default)"

            # Chunks: trả về toàn bộ (default) hoặc sample N chunks.
            # Default sample_per_doc=0 → return ALL chunks (accordion UI).
            if sample_per_doc and sample_per_doc > 0:
                # Sample mode: first + middle + has-prefix + last
                sample_indices = [0]
                if total > 4:
                    sample_indices.extend([total // 4, total // 2, 3 * total // 4])
                if total > 1:
                    sample_indices.append(total - 1)
                sample_indices = sorted(set(sample_indices))[:sample_per_doc]
                chunk_rows = (await session.execute(
                    text("""
                        SELECT
                          dc.chunk_index,
                          LENGTH(dc.content) AS chars,
                          (dc.embedding IS NULL) AS null_embed,
                          EXISTS (
                            SELECT 1 FROM document_chunks ch
                            WHERE ch.parent_chunk_id = dc.id
                          ) AS is_parent,
                          (dc.content LIKE 'Đoạn %nằm trong%'
                            OR dc.content LIKE 'Đoạn %/%:%'
                            OR dc.content LIKE 'Vị trí:%') AS has_haiku_prefix,
                          dc.metadata_json->>'page' AS page,
                          dc.metadata_json->'parent_headings' AS headings,
                          LEFT(dc.content, 1500) AS preview
                        FROM document_chunks dc
                        WHERE dc.record_document_id = :did
                          AND dc.chunk_index = ANY(:idx)
                        ORDER BY dc.chunk_index
                    """),
                    {"did": doc_id, "idx": sample_indices},
                )).fetchall()
            else:
                # Full mode: return ALL chunks (capped at max_chunks_per_doc)
                chunk_rows = (await session.execute(
                    text("""
                        SELECT
                          dc.chunk_index,
                          LENGTH(dc.content) AS chars,
                          (dc.embedding IS NULL) AS null_embed,
                          EXISTS (
                            SELECT 1 FROM document_chunks ch
                            WHERE ch.parent_chunk_id = dc.id
                          ) AS is_parent,
                          (dc.content LIKE 'Đoạn %nằm trong%'
                            OR dc.content LIKE 'Đoạn %/%:%'
                            OR dc.content LIKE 'Vị trí:%') AS has_haiku_prefix,
                          dc.metadata_json->>'page' AS page,
                          dc.metadata_json->'parent_headings' AS headings,
                          LEFT(dc.content, 1500) AS preview
                        FROM document_chunks dc
                        WHERE dc.record_document_id = :did
                        ORDER BY dc.chunk_index
                        LIMIT :cap
                    """),
                    {"did": doc_id, "cap": max_chunks_per_doc},
                )).fetchall()

            samples = []
            for r in chunk_rows:
                # Build heading path from metadata_json.parent_headings
                # (array of "H1 > H2") — fallback empty if missing.
                headings = r[6] if r[6] else []
                if isinstance(headings, list):
                    path_str = " > ".join(headings) if headings else ""
                else:
                    path_str = ""
                samples.append({
                    "chunk_index": int(r[0]),
                    "chars": int(r[1]),
                    "is_parent": bool(r[3]),
                    "has_haiku_prefix": bool(r[4]),
                    "page": r[5],
                    "path": path_str,
                    "preview": r[7],
                })

            docs_info.append({
                "document_id": str(doc_id),
                "document_name": doc_row[1],
                "content_chars": int(doc_row[2]) if doc_row[2] else 0,
                "total_chunks": total,
                "embedded_count": embedded,
                "parent_count": parents,
                "leaf_count": total - parents,
                "avg_chars": avg_chars,
                "min_chars": min_chars,
                "max_chars": max_chars,
                "haiku_enriched_count": haiku_count,
                "detected_strategy": detected,
                "sample_chunks": samples,
            })

    return {
        "ok": True,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "system_config": sys_cfg,
        "documents": docs_info,
    }


@router.get("/bots/{bot_uuid}/vocabulary")
async def get_bot_vocabulary(bot_uuid: str, request: Request) -> dict:
    """Lấy custom vocabulary hiện tại của bot.
    @param bot_uuid: UUID của bot
    @return: {ok, custom_vocabulary}
    """
    _require_owner(request)
    container = _container(request)
    repo = container.bot_repo()
    try:
        bid = uuid.UUID(bot_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bot UUID")

    cfg = await repo.get_by_id(bid, record_tenant_id=_tenant_scope(request))
    if cfg is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    return {"ok": True, "custom_vocabulary": cfg.custom_vocabulary or {}}


@router.patch("/bots/{bot_uuid}/vocabulary")
async def update_bot_vocabulary(
    bot_uuid: str, req: UpdateVocabularyRequest, request: Request,
) -> dict:
    """Cập nhật custom vocabulary (abbreviations + diacritics) cho bot.
    @param bot_uuid: UUID của bot
    @param req: abbreviations và diacritics map
    @return: {ok, custom_vocabulary}
    """
    _require_owner(request)
    container = _container(request)
    repo = container.bot_repo()
    try:
        bid = uuid.UUID(bot_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bot UUID")

    scope = _tenant_scope(request)
    cfg = await repo.get_by_id(bid, record_tenant_id=scope)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Merge: keep existing keys, override with new ones
    current = dict(cfg.custom_vocabulary or {})
    if req.abbreviations:
        existing_abbrevs = dict(current.get("abbreviations", {}))
        existing_abbrevs.update(req.abbreviations)
        current["abbreviations"] = existing_abbrevs
    if req.diacritics:
        existing_diacritics = dict(current.get("diacritics", {}))
        existing_diacritics.update(req.diacritics)
        current["diacritics"] = existing_diacritics

    updated = await repo.update_bot(bid, record_tenant_id=scope, custom_vocabulary=current)
    if updated is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Invalidate bot registry cache so workers pick up new vocabulary
    registry = container.bot_registry_service()
    await registry.invalidate(
        cfg.record_tenant_id, cfg.workspace_id, cfg.bot_id, cfg.channel_type,
    )

    return {"ok": True, "custom_vocabulary": updated.custom_vocabulary or {}}


__all__ = [
    "router",
    "list_bots",
    "create_bot",
    "update_bot",
    "get_callback_format",
    "delete_bot",
    "update_bot_max_history",
    "chunking_info",
    "get_bot_vocabulary",
    "update_bot_vocabulary",
]
