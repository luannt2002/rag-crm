"""Production SSE streaming chat — B.6.

Differs from ``POST /chat`` (202 + worker outbox) in that ``/chat/stream``
runs the pipeline inside the request and pushes token deltas to SSE the
moment the LLM yields them. Reuses the same building blocks:

- 4-key resolve (tenant + workspace + bot_id + channel_type) via
  ``BotRegistryService`` (Redis cached + DB fallback).
- RBAC permission ``chat:stream`` (level mirrors ``chat:submit``).
- ``TenantContextMiddleware`` already enforced rate-limit + token-cap
  upstream; no extra check here.
- Pipeline graph built fresh per request (chat_worker pattern) so step
  trackers / guardrails bind to the request context.
- Output framing delegated to ``_sse_helper.stream_real_llm`` so the demo
  endpoint and this production endpoint share the SSE contract.

Out of scope: streaming idempotency replay, streaming + Outbox resume,
OpenTelemetry span export, HTTP/2 push.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.config.logging import bind_request_context
from ragbot.infrastructure.repositories.history_reconcile import HistoryReconciler
from ragbot.interfaces.http._sse_helper import _STREAM_SENTINEL, stream_real_llm
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.interfaces.http.schemas.chat_schema import ChatRequest
from ragbot.shared.constants import (
    DEFAULT_CHAT_STREAM_TIMEOUT_S,
    DEFAULT_CONNECT_ID,
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_SOURCE_PREVIEW_CHARS,
    DEFAULT_SSE_SINK_MAXSIZE,
)
from ragbot.shared.hashing import content_hash_required
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)


router = APIRouter(tags=["chat"])


def _container(request: Request):
    return request.app.state.container


async def _build_pipeline_config_for_stream(
    cfg_svc: SystemConfigService, bot_cfg: Any,
) -> dict[str, Any]:
    """Reuse the demo-route helper as the single source of truth.

    ``test_chat._build_pipeline_config`` already centralises every system_config
    key the pipeline reads at runtime (with per-bot overrides via
    ``resolve_bot_limit``). Reusing it avoids dict drift between the demo
    endpoint and the production stream endpoint — adding a new pipeline knob
    in one place updates both routes.
    """
    # Late import — test_chat imports a heavy chain (StaticFiles, repos);
    # bringing it in at module level on every chat_stream call is wasteful
    # and breaks the stream module's import-graph isolation in tests.
    from ragbot.interfaces.http.routes.test_chat import (  # noqa: PLC0415
        _build_pipeline_config,
    )

    return await _build_pipeline_config(cfg_svc, bot_cfg)


@router.post(
    "/chat/stream",
    summary="SSE streaming variant of POST /chat",
    dependencies=[Depends(require_permission_dep("chat", "stream"))],
)
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Production real-token SSE streaming endpoint.

    Returns ``text/event-stream`` with the contract documented in
    :func:`ragbot.interfaces.http._sse_helper.stream_real_llm`. The pipeline
    runs synchronously in the request task; the streaming sink lives in
    process memory (no Redis/Outbox).
    """
    # JWT bearer is the tenant authority — body no longer carries tenant_id.
    record_tenant: uuid.UUID = request.state.record_tenant_id
    if record_tenant is None:
        raise HTTPException(status_code=403, detail="missing tenant context")

    container = _container(request)
    sf = container.session_factory()
    cfg_svc = SystemConfigService(
        session_factory=sf,
        redis_client=container.redis_client(),
    )
    t0 = time.perf_counter()

    # Streaming master flag — two-key resolution for backward compat:
    #   1. ``streaming_response_enabled`` (FILE-OWNERSHIP-MATRIX 2D spec) is
    #      the canonical kill-switch; default OFF per the spec but only takes
    #      effect when an operator has explicitly set the row.
    #   2. ``streaming_enabled`` (legacy) remains the live runtime gate so
    #      existing deployments keep behaviour. Disabling either kills SSE.
    # Resolution order: explicit ``streaming_response_enabled=false`` always
    # wins (operator opt-out). Otherwise the legacy ``streaming_enabled``
    # default ON path runs unchanged.
    feature_flag_key = "streaming_response_enabled"
    legacy_flag_key = "streaming_enabled"
    response_flag_value = await cfg_svc.get_bool(feature_flag_key, True)
    legacy_flag_value = await cfg_svc.get_bool(legacy_flag_key, True)
    streaming_on = response_flag_value and legacy_flag_value
    if not streaming_on:
        raise HTTPException(
            status_code=403,
            detail="Streaming is disabled via system_config",
        )

    # Body fallback: caller without a slug receives the tenant UUID.
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant,
    )
    # Feed the resolved slug to the RLS workspace GUC binder (ADR-W1-D3).
    bind_request_context(workspace_id=str(workspace_id))

    # 4-key external identity resolve → record_bot_id (UUID).
    registry = container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_id=record_tenant,
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    request_id = uuid.uuid4()
    message_id = int(time.time() * 1000)
    tenant_uuid = record_tenant
    connect_id = req.user_id or DEFAULT_CONNECT_ID

    system_max = await cfg_svc.get_int("chat_max_history", DEFAULT_MAX_HISTORY)
    max_history = req.history_limit or system_max

    # ── 1. LOAD HISTORY (best-effort; missing history = empty list) ──
    # MT-1 reconcile: merge BOTH stores (chat_histories written here +
    # messages/conversations written by the worker transport) so a user
    # who alternates transports under the same connect_id keeps full
    # multi-turn context. Read-path only — no second write store.
    conversation_history: list[dict[str, str]] = []
    try:
        conversation_history = await HistoryReconciler(sf).load(
            record_bot_id=bot_cfg.id,
            connect_id=connect_id,
            channel_type=req.channel_type,
            limit=max_history,
        )
    except SQLAlchemyError as exc:
        # DB unavailable / query timeout — degrade to empty history rather
        # than 500ing the chat call. ``logger.exception`` preserves the
        # traceback in structlog output so the failure mode stays visible.
        logger.exception("chat_stream_history_load_failed", error=str(exc))

    # ── 2. BUILD PIPELINE GRAPH (fresh per request) ──
    from ragbot.application.services.step_tracker import StepTracker  # noqa: PLC0415
    from ragbot.orchestration.graph_assembly import (  # noqa: PLC0415
        build_chat_initial_state,
        build_graph_di_kwargs,
        resolve_kg_service,
    )
    from ragbot.orchestration.query_graph import get_graph  # noqa: PLC0415
    from ragbot.orchestration.state import GraphState  # noqa: PLC0415
    from ragbot.shared.errors import GraphAssemblyError  # noqa: PLC0415

    request_log_repo = container.request_log_repo()
    question_hash = content_hash_required(req.content)

    try:
        await request_log_repo.create_request_log(
            request_id=request_id,
            record_tenant_id=tenant_uuid,
            connect_id=connect_id,
            question_hash=question_hash,
            message_id=message_id,
            record_bot_id=bot_cfg.id,
            channel_type=req.channel_type,
            trace_id=getattr(request.state, "trace_id", str(request_id)),
        )
    except SQLAlchemyError as exc:
        # Audit-log create is best-effort: if the request_logs INSERT fails
        # we still want the user's chat to work. Traceback preserved via
        # ``logger.exception`` instead of silent swallow.
        logger.exception("chat_stream_log_create_failed", error=str(exc))

    tracker = StepTracker(
        request_id=request_id,
        record_tenant_id=tenant_uuid,
        repo=request_log_repo,
        metrics=container.metrics_port(),
    )

    def _opt(attr: str):
        # Optional DI dependency resolve. The container provider may
        # legitimately raise on missing config (KeyError on system_config),
        # bad attribute wiring (AttributeError), or wrong-arg shape
        # (TypeError). Anything else is a real bug and should propagate so
        # we don't silently strip out a hard dependency.
        if not hasattr(container, attr):
            return None
        try:
            return getattr(container, attr)()
        except (KeyError, AttributeError, TypeError) as exc:
            logger.warning(
                "chat_stream_optional_dep_unavailable",
                attr=attr,
                error=str(exc),
            )
            return None

    # Canonical DI assembly (ADR-W1-DI): one shared builder for every
    # transport so an SSE-first warm-up can no longer drop worker-only
    # deps. Required deps (vector_store/embedder/llm/...) fail loudly as
    # GraphAssemblyError → 503, preserving the Y3-P1 contract.
    try:
        graph = await get_graph(**build_graph_di_kwargs(container))
    except GraphAssemblyError as exc:
        logger.error(
            "chat_stream_required_dep_missing",
            dep=exc.details.get("dep"),
        )
        raise HTTPException(
            status_code=503, detail=f"RAG pipeline misconfigured: {exc.message}",
        )
    except (KeyError, AttributeError, TypeError, ValueError, ImportError) as exc:
        # Graph wiring exceptions: missing system_config (KeyError),
        # mis-wired DI attribute (AttributeError), bad node shape
        # (TypeError), bad config value (ValueError), missing optional
        # dep (ImportError). Anything beyond this is unexpected and
        # *should* bubble up as a 500 — narrow catch + ``raise`` from a
        # bare ``HTTPException`` would mask real bugs.
        logger.exception("chat_stream_graph_build_failed")
        raise HTTPException(
            status_code=503, detail=f"RAG pipeline not configured: {exc}",
        )

    pipeline_config = await _build_pipeline_config_for_stream(cfg_svc, bot_cfg)

    _oos_resolver = _opt("oos_template_resolver")
    _bot_language = getattr(bot_cfg, "language", None) or DEFAULT_LANGUAGE
    _oos_template_resolved = (
        await _oos_resolver.resolve(
            bot=bot_cfg,
            language=_bot_language,
            bot_name_substitution=getattr(bot_cfg, "bot_name", "") or "",
        )
        if _oos_resolver is not None
        else (getattr(bot_cfg, "oos_answer_template", None) or "")
    )

    # Assemble final system_prompt: owner content + platform-default rules
    # − per-bot opt-outs. See SysPromptAssembler module docstring.
    _assembler = _opt("sysprompt_assembler")
    _assembled_sysprompt = (
        await _assembler.assemble(bot=bot_cfg, language=_bot_language)
        if _assembler is not None
        else (getattr(bot_cfg, "system_prompt", "") or "")
    )

    # Bounded queue → backpressure when SSE consumer is slow. Producer
    # (LLM stream) blocks on full queue instead of buffering tokens
    # unbounded, preventing OOM on stuck/disconnected clients.
    sink: asyncio.Queue = asyncio.Queue(maxsize=DEFAULT_SSE_SINK_MAXSIZE)
    # Resolve a persistent conversation_id so action bots keep multi-turn slot
    # state across SSE turns. The inline graph here never went through the
    # use-case get-or-create (unlike the queued /chat path), so this was the
    # SSE booking-slot loss. Factoid bots resolve to None (no row churn).
    from ragbot.interfaces.http.routes._action_conversation import (  # noqa: PLC0415
        resolve_action_conversation_id,
    )
    _conversation_id = await resolve_action_conversation_id(
        container.conv_repo(),
        bot_cfg,
        connect_id=connect_id,
        tenant_id=tenant_uuid,
        workspace_slug=workspace_id,
    )
    initial_state: GraphState = build_chat_initial_state(
        record_tenant_id=tenant_uuid,
        request_id=request_id,
        message_id=message_id,
        conversation_id=_conversation_id,
        record_bot_id=bot_cfg.id,
        bot_cfg=bot_cfg,
        channel_type=req.channel_type,
        workspace_id=workspace_id,
        # HTTP chat requests carry no group claims — explicit empty list so
        # the permission pre-filter sees "no groups" rather than a missing key.
        user_groups=[],
        query=req.content,
        conversation_history=conversation_history,
        pipeline_config=pipeline_config,
        tracker=tracker,
        assembled_sysprompt=_assembled_sysprompt,
        oos_template_resolved=_oos_template_resolved,
        bot_language=_bot_language,
        kg_service=resolve_kg_service(pipeline_config),
        session_factory=_opt("session_factory"),
    )
    initial_state["_stream_sink"] = sink  # type: ignore[typeddict-unknown-key]

    final_state_holder: dict = {"state": None, "error": None, "sources": []}

    def _build_sources(graded: list) -> list:
        return [
            {
                "document_name": (c.get("document_name")
                                  or c.get("metadata", {}).get("document_title")
                                  or "(không tên)"),
                "source_url": c.get("source_url") or None,
                "chunk_index": c.get("chunk_index", 0),
                "score": round(float(c.get("score", 0)), 4),
                "preview": (c.get("content") or c.get("text") or "")[:DEFAULT_SOURCE_PREVIEW_CHARS],
            }
            for c in graded
        ]

    timeout_s = await cfg_svc.get_int(
        "chat_stream_timeout_s", DEFAULT_CHAT_STREAM_TIMEOUT_S,
    )

    async def _run_graph() -> None:
        try:
            async with asyncio.timeout(timeout_s):
                final_state = await graph.ainvoke(
                    initial_state,
                    config={"recursion_limit": pipeline_config["graph_recursion_limit"]},
                )
            final_state_holder["state"] = final_state
            final_state_holder["sources"] = _build_sources(
                final_state.get("graded_chunks") or [],
            )
        except asyncio.CancelledError:
            # Client disconnected mid-stream; ``finally`` still runs and
            # pushes the sentinel. Re-raise so the cancel propagates to
            # the request task without being labelled a pipeline error.
            raise
        except Exception as exc:  # noqa: BLE001
            # Pipeline (LLM / vector store / graph node) raised — stream
            # the error to the client via SSE. We retain a broad catch
            # here because failures can come from any of ~20 plugin nodes,
            # but ``logger.exception`` preserves the full traceback so
            # diagnosis is never silent (P20 lesson: no broad-except
            # swallows without traceback).
            final_state_holder["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "chat_stream_pipeline_failed",
                error=final_state_holder["error"],
            )
            raise
        finally:
            await sink.put(_STREAM_SENTINEL)

    graph_task = asyncio.create_task(_run_graph())

    async def _finalize_log(final_state: dict, answer: str, llm_error: str | None) -> None:
        tokens = final_state.get("tokens") or {"prompt": 0, "completion": 0, "cached": 0}
        graded_chunks = final_state.get("graded_chunks") or []
        try:
            await request_log_repo.finalize_request_log(
                request_id,
                record_tenant_id=tenant_uuid,
                answer_hash=content_hash_required(answer) if answer else None,
                model_name=final_state.get("model_used", ""),
                prompt_tokens=int(tokens.get("prompt", 0)),
                completion_tokens=int(tokens.get("completion", 0)),
                cost_usd=float(final_state.get("cost_usd", 0.0)),
                status="success" if not llm_error else "failed",
                error_code="PIPELINE_ERROR" if llm_error else None,
                error_message=llm_error,
                # G15: only refs (chunk_id + rank + score) -- previews
                # used to live in inline JSONB; relational table stores
                # FK-validated refs only, no PII.
                retrieved_chunks=[{
                    "chunk_id": c.get("chunk_id") or c.get("id"),
                    "rank": idx,
                    "score": float(c.get("score", 0) or 0),
                } for idx, c in enumerate(graded_chunks)],
            )
        except SQLAlchemyError as exc:
            # Best-effort audit finalize: never fail the user's chat just
            # because the request_logs UPDATE failed. Traceback preserved.
            logger.exception("chat_stream_log_finalize_failed", error=str(exc))

    async def _save_history(answer: str) -> None:
        if not answer:
            return
        try:
            async with sf() as session:
                await session.execute(
                    sa_text(
                        """
                        WITH ins AS (
                            INSERT INTO chat_histories (record_bot_id, channel_type, connect_id, role, content)
                            VALUES (:bid, :ch, :cid, 'user', :q), (:bid, :ch, :cid, 'assistant', :a)
                        )
                        DELETE FROM chat_histories
                        WHERE id IN (
                            SELECT id FROM chat_histories
                            WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                            ORDER BY id DESC
                            OFFSET :keep
                        )
                        """,
                    ),
                    {"bid": bot_cfg.id, "ch": req.channel_type, "cid": connect_id,
                     "q": req.content, "a": answer, "keep": max_history},
                )
                await session.commit()
        except (SQLAlchemyError, RedisError) as exc:
            # History persist is best-effort; user already received the
            # answer via SSE. Traceback preserved via ``logger.exception``.
            logger.exception("chat_stream_history_save_failed", error=str(exc))

    async def _on_complete(final_state: dict, answer: str, _duration_ms: int) -> None:
        err = final_state_holder.get("error")
        await asyncio.gather(
            _finalize_log(final_state, answer, err),
            _save_history(answer),
        )

    telemetry_extra = {
        "request_id": str(request_id),
        "record_tenant_id": str(tenant_uuid),
        "record_bot_id": str(bot_cfg.id),
        "workspace_id": workspace_id,
        "bot_id": req.bot_id,
        "channel_type": req.channel_type,
        "feature_flag": feature_flag_key,
    }
    return StreamingResponse(
        stream_real_llm(
            sink,
            graph_task,
            final_state_holder,
            t0,
            _on_complete,
            telemetry_extra=telemetry_extra,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
