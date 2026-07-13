"""Chat routes (POST /chat, /chat/stream, GET /chat/history, DELETE /chat).

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). Same
production pipeline (query_graph) + demo extras, same SSE framing.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError  # noqa: F401 — used by carved handlers

from ragbot.infrastructure.repositories.history_reconcile import HistoryReconciler
from ragbot.shared.bot_limits import resolve_bot_limit
from ragbot.shared.hashing import content_hash_required
from ragbot.shared.served_chunks import build_served_chunks
from ragbot.shared.verdict_meta import build_verdict_meta
from ragbot.shared.constants import (
    DEFAULT_CONNECT_ID,
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_TOKENS_TOTAL,
    DEFAULT_SSE_SINK_MAXSIZE,
    DEFAULT_STREAMING_USE_REAL_LLM,
    DEFAULT_STREAMING_WORD_DELAY_MS,
    ROLLING_SUMMARY_KEEP_LAST,
    ROLLING_SUMMARY_THRESHOLD,
)

# Wave H Phase 1 — share SSE framing + sentinel with ``/chat/stream`` so a
# single source of truth governs token framing, TTFT capture, and the
# ``replace`` event semantics.
from ragbot.interfaces.http._sse_helper import (
    _STREAM_SENTINEL,
    stream_real_llm as _shared_stream_real_llm,
)

from .schemas import TestChatRequest, TestChatClearRequest
from ._shared import (
    _PLATFORM_TENANT_FALLBACK_UUID,
    _apply_rolling_summary,
    _build_pipeline_config,
    _caller_tenant_uuid,
    _container,
    _find_bot_uuid,
    _llm_circuit,
    _require_owner,
    _resolve_action_conversation_id,
    _sf,
    _sys_config,
    _tenant_scope,
    logger,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id
from ragbot.config.logging import (
    bot_id_ctx,
    channel_type_ctx,
    mode_ctx,
    record_bot_id_ctx,
    workspace_id_ctx,
)

router = APIRouter(tags=["test"])


def _build_stats_attributed_refs(
    graded_chunks: list[dict] | None, final_state: dict | None,
) -> list[dict]:
    """request_chunk_refs from graded chunks + STEP-5 stats attribution.

    The stats route answers from a synthetic chunk (sentinel id → FK-skipped),
    so its retrieval is invisible to CHUNK_RECALL. Attribute it to the matched
    entities' REAL source chunks (``record_chunk_id``) WITHOUT feeding the raw
    chunks to the LLM — the generate context stays synthetic-only (HALLU-safe).
    """
    refs: list[dict] = [
        {
            "chunk_id": c.get("chunk_id") or c.get("id"),
            "rank": idx,
            "score": float(c.get("score", 0) or 0),
        }
        for idx, c in enumerate(graded_chunks or [])
    ]
    stats_entities = (
        (final_state or {}).get("stats_entities") or []
        if isinstance(final_state, dict) else []
    )
    seen = {r["chunk_id"] for r in refs if r.get("chunk_id")}
    for e in stats_entities:
        cid = e.get("record_chunk_id") if isinstance(e, dict) else None
        if cid and cid not in seen:
            seen.add(cid)
            refs.append({"chunk_id": cid, "rank": len(refs), "score": None})
    return refs


@router.post("/chat")
async def test_chat(req: TestChatRequest, request: Request) -> dict:
    """Chat test: dùng cùng pipeline production (query_graph) + extras cho demo.
    @param req: {bot_id, channel_type, question}
    @return: {ok, answer, sources, chunks_used, tokens, cost_usd, duration_ms, debug}
    """
    # asyncio imported at module level

    container = _container(request)
    sf = _sf(request)
    cfg_svc = _sys_config(request)
    # Honour caller-supplied connect_id so harness rooms stay isolated;
    # default to shared DEFAULT_CONNECT_ID for the demo UI's single room.
    connect_id = req.connect_id or DEFAULT_CONNECT_ID
    t0 = time.perf_counter()

    # Demo path: tenant lifted from JWT bearer (set by middleware).
    record_tenant_uuid = (
        _tenant_scope(request) or _PLATFORM_TENANT_FALLBACK_UUID
    )
    # Workspace is an OPTIONAL disambiguator on the chat boundary. When the
    # caller (e.g. demo UI) does not pass workspace_id, resolve by the unique
    # (tenant, bot_id, channel) match instead of forcing the str(tenant) system
    # fallback — otherwise a bot living in a non-default workspace (spa/xe/legal)
    # 404s for any caller that does not know its slug.
    _ws_explicit = req.workspace_id is not None and str(req.workspace_id).strip() != ""
    if _ws_explicit:
        workspace_slug = resolve_workspace_id(
            req.workspace_id, record_tenant_id=record_tenant_uuid,
        )
        bot_cfg = await container.bot_repo().find_by_4key(
            record_tenant_uuid, workspace_slug, req.bot_id, req.channel_type,
        )
    else:
        bot_cfg = await container.bot_repo().find_by_3key_unique(
            record_tenant_uuid, req.bot_id, req.channel_type,
        )
        # Downstream pipeline scopes by the bot's ACTUAL workspace.
        workspace_slug = (
            getattr(bot_cfg, "workspace_id", None)
            or resolve_workspace_id(req.workspace_id, record_tenant_id=record_tenant_uuid)
        )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail=f"Bot {req.bot_id}:{req.channel_type} not found")

    # Token-ledger attribution: bind the bot's internal UUID (record_bot_id) +
    # workspace + mode='query' so every LLM call this request makes is logged
    # against THIS bot (otherwise query rows land with bot_id NULL). Mirrors the
    # document_worker's ingest binding.
    record_bot_id_ctx.set(str(bot_cfg.id))                       # internal UUID (report key)
    bot_id_ctx.set(str(getattr(bot_cfg, "bot_id", "") or req.bot_id or ""))  # external slug
    workspace_id_ctx.set(str(workspace_slug or ""))
    channel_type_ctx.set(str(req.channel_type or ""))           # 4th identity key for ledger
    mode_ctx.set("query")

    # ── Step B: Parallel gather — all 6 calls independent after bot_cfg ──
    # Dependency DAG: bot_cfg.id → all 6 calls; no cross-dep among them.
    # Saves ~90ms vs sequential by overlapping Redis L1 + DB ready-check +
    # 4 system_config reads that each independently hit Redis-cached values.
    _cfg_svc_inner = container.system_config_service()
    _redis_inner = container.redis_client()
    _l1_key = f"ragbot:bot:tokens_used:{bot_cfg.id}"

    async def _ready_check_query() -> tuple[int, int, int]:
        """Run doc-ready check in its own session; returns (total, active, ready)."""
        from sqlalchemy import text as _t  # noqa: PLC0415
        async with sf() as _sess:
            _result = await _sess.execute(_t("""
                SELECT
                    count(*) as total_docs,
                    count(CASE WHEN state='active' THEN 1 END) as active_docs,
                    count(CASE WHEN state='active' AND (
                        SELECT count(*) FROM document_chunks WHERE record_document_id = d.id
                    ) > 0 THEN 1 END) as ready_docs
                FROM documents d
                WHERE record_bot_id = :bot_id AND deleted_at IS NULL
            """), {"bot_id": bot_cfg.id})
            _stat = _result.fetchone()
            return (_stat[0] or 0, _stat[1] or 0, _stat[2] or 0)

    (
        _system_max_raw,
        _l1_value,
        _ready_stat,
        _chat_max_history_raw,
        _rolling_threshold_raw,
        _rolling_keep_last_raw,
    ) = await asyncio.gather(
        _cfg_svc_inner.get_int("max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL),
        _redis_inner.get(_l1_key),
        _ready_check_query(),
        cfg_svc.get_int("chat_max_history", DEFAULT_MAX_HISTORY),
        cfg_svc.get_int("rolling_summary_threshold", ROLLING_SUMMARY_THRESHOLD),
        cfg_svc.get_int("rolling_summary_keep_last", ROLLING_SUMMARY_KEEP_LAST),
        return_exceptions=True,
    )

    # Unpack with fallback defaults for any failed gather leg.
    # Non-critical config reads degrade gracefully; quota gate stays strict.
    _system_max: int = (
        int(_system_max_raw)
        if not isinstance(_system_max_raw, BaseException)
        else DEFAULT_MAX_TOKENS_TOTAL
    )
    if isinstance(_l1_value, BaseException):
        _l1_value = None  # Redis failure → treat as cache miss → fallback to DB
    _doc_ready_stat: tuple[int, int, int] = (
        _ready_stat
        if not isinstance(_ready_stat, BaseException)
        else (0, 0, 0)  # no stat → allow chat (not blocking)
    )
    _chat_max_history_cfg: int = (
        int(_chat_max_history_raw)
        if not isinstance(_chat_max_history_raw, BaseException)
        else DEFAULT_MAX_HISTORY
    )
    _rolling_threshold: int = (
        int(_rolling_threshold_raw)
        if not isinstance(_rolling_threshold_raw, BaseException)
        else ROLLING_SUMMARY_THRESHOLD
    )
    _rolling_keep_last: int = (
        int(_rolling_keep_last_raw)
        if not isinstance(_rolling_keep_last_raw, BaseException)
        else ROLLING_SUMMARY_KEEP_LAST
    )

    # ── PRE-CALL QUOTA GATE — Reject if bot exceeded monthly token budget ──
    # Read tokens_used from Redis L1 (fast ~0.5ms) with DB-snapshot fallback.
    # effective_limit = system_config.max_tokens_total + bot.extra_max_tokens.
    # Refusal text from bot.oos_answer_template (DB-driven, no hardcode).
    try:
        from ragbot.shared.token_budget import (  # noqa: PLC0415
            can_answer, compute_effective_max_tokens,
        )

        _effective_limit = compute_effective_max_tokens(
            system_max_tokens=_system_max,
            bot_extra_max_tokens=int(bot_cfg.extra_max_tokens),
        )
        _tokens_used = (
            int(_l1_value) if _l1_value is not None else int(bot_cfg.tokens_used)
        )
        if not can_answer(
            tokens_used=_tokens_used,
            effective_limit=_effective_limit,
            bypass=bool(bot_cfg.bypass_token_check),
        ):
            # Walk the 7-tier resolver so quota refusal text matches
            # the rest of the pipeline (owner override → language pack
            # → constants), not just tier 1 of the chain.
            try:
                _quota_resolver = container.oos_template_resolver()
                _refuse_msg = await _quota_resolver.resolve(
                    bot=bot_cfg,
                    language=getattr(bot_cfg, "language", None),
                    bot_name_substitution=getattr(bot_cfg, "bot_name", "") or "",
                )
            except Exception:  # noqa: BLE001 — fail-soft refusal text
                _refuse_msg = getattr(bot_cfg, "oos_answer_template", None) or ""
            # Fire notify directly (refused chats don't go through hook pipeline).
            try:
                _notifier = container.webhook_notifier()
                await _notifier.send_quota_exhausted(
                    record_tenant_id=record_tenant_uuid,
                    record_bot_id=bot_cfg.id,
                    bot_name=bot_cfg.bot_name or "",
                    tokens_used=_tokens_used,
                    effective_limit=_effective_limit,
                )
            except Exception as _ne:  # noqa: BLE001 — notify is best-effort
                logger.warning("test_chat_quota_notify_failed", error=str(_ne)[:200])
            return {
                "ok": False,
                "blocked": True,
                "blocked_reason": "QUOTA_EXHAUSTED",
                "answer": _refuse_msg,
                "refusal_reason": "QUOTA_EXHAUSTED",
                "chunks_used": 0,
                "tokens": {"prompt": 0, "completion": 0},
                "tokens_used_this_period": _tokens_used,
                "effective_limit": _effective_limit,
            }
    except HTTPException:
        raise
    except Exception as _qe:  # noqa: BLE001 — quota gate fail-soft (don't block chat if check errors)
        logger.warning("test_chat_quota_gate_failed", error=str(_qe)[:200])

    # GUARD (admin mandate 2026-05-13): nếu bot chỉ có 1 doc và doc đó
    # chưa state="active" (vẫn DRAFT/chunking/embedding/failed) → cấm chat,
    # trả message rõ ràng cho user "tài liệu đang chuẩn bị, vui lòng đợi".
    # Tránh case user hỏi → bot bịa vì không có context.
    # _doc_ready_stat fetched in Step B parallel gather above.
    _total, _active, _ready = _doc_ready_stat
    if _total > 0 and _ready == 0:
        # Bot có doc nhưng chưa có doc nào sẵn sàng → cấm chat
        return {
            "ok": False,
            "blocked": True,
            "blocked_reason": "documents_not_ready",
            "answer": (
                f"⏳ Tài liệu đang được chuẩn bị ({_total} tài liệu, {_active} đã chunk, 0 sẵn sàng). "
                f"Vui lòng đợi worker hoàn tất rồi hỏi lại. F5 list documents để xem progress."
            ),
            "documents": {
                "total": _total,
                "active": _active,
                "ready": _ready,
            },
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }

    request_id = uuid.uuid4()
    message_id = int(time.time() * 1000)
    tenant_id = _caller_tenant_uuid(request) or _PLATFORM_TENANT_FALLBACK_UUID
    # Per-bot max_history — _chat_max_history_cfg fetched in Step B gather above.
    max_history = resolve_bot_limit(bot_cfg, "max_history", system_default=_chat_max_history_cfg)
    if max_history is None:
        max_history = _chat_max_history_cfg

    # ── Step C: history query — depends on max_history from Step B ──
    # MT-1 reconcile: merge chat_histories (this transport) + messages
    # (worker transport) for the same (record_bot_id, connect_id) so
    # cross-transport multi-turn context survives. Read-path only.
    conversation_history = await HistoryReconciler(sf).load(
        record_bot_id=bot_cfg.id,
        connect_id=connect_id,
        channel_type=req.channel_type,
        limit=max_history,
    )

    # Rolling summary — thresholds fetched in Step B gather above.
    conversation_history = _apply_rolling_summary(
        conversation_history,
        threshold=_rolling_threshold,
        keep_last=_rolling_keep_last,
    )

    # ── 2. BUILD + RUN PRODUCTION PIPELINE ─────────────────────────────────
    from ragbot.orchestration.graph_assembly import (
        build_chat_initial_state,
        build_graph_di_kwargs,
        resolve_kg_service,
    )
    from ragbot.orchestration.query_graph import get_graph
    from ragbot.orchestration.state import GraphState
    from ragbot.application.services.step_tracker import StepTracker

    request_log_repo = container.request_log_repo()
    question_hash = content_hash_required(req.question)

    # Create request log (test marker)
    try:
        await request_log_repo.create_request_log(
            request_id=request_id, record_tenant_id=tenant_id, connect_id=connect_id,
            question_hash=question_hash, question_text=req.question,
            message_id=message_id,
            record_bot_id=bot_cfg.id, channel_type=req.channel_type,
            workspace_id=workspace_slug,
            trace_id=f"test-{request_id}",
        )
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        logger.warning(
            "test_chat_log_create_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    tracker = StepTracker(
        request_id=request_id, record_tenant_id=tenant_id, repo=request_log_repo,
    )

    # Circuit breaker check BEFORE building graph
    if not _llm_circuit.can_execute():
        raise HTTPException(status_code=503, detail="LLM circuit breaker open — thử lại sau 30s")

    # Resolve dependencies — reranker not in Container, semantic_cache optional
    def _opt(attr: str):
        """Lấy optional dependency từ container, None nếu không có."""
        if not hasattr(container, attr):
            return None
        try:
            return getattr(container, attr)()
        except Exception:  # noqa: BLE001 — optional dep, return None if unavailable
            return None

    try:
        graph = await get_graph(**build_graph_di_kwargs(container))
    except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
        # model_resolver or llm not configured → fallback error
        logger.exception(
            "test_chat_graph_build_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=f"RAG pipeline not configured: {exc}") from exc

    # Load ALL pipeline tunable params from system_config (Redis-cached DB)
    pipeline_config = await _build_pipeline_config(cfg_svc, bot_cfg)

    # Test-mode A/B: merge per-request overrides on top of the resolved config
    # so a load-test can flip cost/intelligence flags without mutating
    # system_config / plan_limits (CLAUDE.md forbids psql hot-fixes there).
    # Ephemeral — scoped to this single request only.
    if req.pipeline_config_overrides:
        pipeline_config.update(req.pipeline_config_overrides)

    # Walk the canonical 7-tier OOS template chain ONCE per request and
    # stash the result for orchestration nodes to read sync. See
    # ``OosTemplateResolver`` module docstring for the full ladder.
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
    # − per-bot opt-outs. See ``SysPromptAssembler`` module docstring for
    # the multi-tenant rationale. Falls back to raw bot.system_prompt when
    # assembler not wired (legacy bootstrap / unit tests).
    _assembler = _opt("sysprompt_assembler")
    _assembled_sysprompt = (
        await _assembler.assemble(bot=bot_cfg, language=_bot_language)
        if _assembler is not None
        else (bot_cfg.system_prompt or "")
    )

    conversation_id = await _resolve_action_conversation_id(
        _opt("conv_repo"), bot_cfg,
        connect_id=connect_id, tenant_id=tenant_id, workspace_slug=workspace_slug,
    )

    initial_state: GraphState = build_chat_initial_state(
        record_tenant_id=tenant_id,
        request_id=request_id,
        message_id=message_id,
        conversation_id=conversation_id,
        record_bot_id=bot_cfg.id,
        bot_cfg=bot_cfg,
        channel_type=req.channel_type,
        workspace_id=workspace_slug,
        # Demo endpoints carry no group claims — explicit empty list.
        user_groups=[],
        query=req.question,
        conversation_history=conversation_history,
        pipeline_config=pipeline_config,
        tracker=tracker,
        assembled_sysprompt=_assembled_sysprompt,
        oos_template_resolved=_oos_template_resolved,
        bot_language=_bot_language,
        kg_service=resolve_kg_service(pipeline_config),
        session_factory=_opt("session_factory"),
    )
    # test-mode: skip semantic cache when True (transport-specific key)
    initial_state["bypass_cache"] = req.bypass_cache

    answer = ""
    from ragbot.shared.errors import ExternalServiceError, LLMError  # noqa: PLC0415
    from ragbot.application.ports.guardrail_port import GuardrailBlocked  # noqa: PLC0415

    llm_error = None
    _svc_unavailable = False
    final_state = {}

    _pipeline_timeout_s = int(pipeline_config.get("pipeline_timeout_s") or 0)
    try:
        _graph_coro = graph.ainvoke(
            initial_state,
            config={"recursion_limit": pipeline_config["graph_recursion_limit"]},
        )
        # Server-side wall-clock kill: a runaway pipeline (a hung upstream LLM)
        # must not hold the request slot indefinitely. Mirrors the async worker
        # (chat_worker/pipeline.py) so this harness reflects the SAME timeout a
        # production consumer gets; 0 disables. Value is config-driven per bot.
        if _pipeline_timeout_s > 0:
            final_state = await asyncio.wait_for(_graph_coro, timeout=_pipeline_timeout_s)
        else:
            final_state = await _graph_coro
        answer = final_state.get("answer", "") or ""
        _llm_circuit.record_success()
    except GuardrailBlocked as exc:
        # A guardrail raised mid-graph (e.g. conversation_state service-drift on a
        # price/aggregation answer, or output block) propagated out of the node.
        # Convert to a graceful refuse using the bot's OWN oos template — never
        # crash (this was a 500), never inject platform text (sacred-rule 10).
        _llm_circuit.record_success()  # not an LLM/infra failure
        logger.info(
            "test_chat_guardrail_blocked",
            rules=[h.rule_id for h in exc.hits],
        )
        final_state = {
            "answer": _oos_template_resolved or "",
            "answer_type": "blocked",
            "answer_reason": "guardrail_blocked",
            "guardrail_flags": [
                {"stage": "output", "rule_id": h.rule_id, "severity": h.severity,
                 "action": h.action, "blocked": True}
                for h in exc.hits
            ],
        }
        answer = final_state["answer"]
    except ExternalServiceError as exc:
        # Embedder/reranker circuit open or upstream 5xx — transient infra,
        # NOT a pipeline bug. Return 503 so clients/harness retry. Do NOT trip
        # the LLM circuit (this is an embedder failure, not the LLM).
        _svc_unavailable = True
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "test_chat_external_service_unavailable",
            error=llm_error,
            error_type=type(exc).__name__,
            exc_info=True,
        )
    except LLMError as exc:
        # B4 — the LLM provider failed after retries (e.g. an upstream gateway 5xx on a
        # heavy "liệt kê" query). This is TRANSIENT infra, not a pipeline bug:
        # map it to a retryable 503 (like ExternalServiceError) instead of a
        # 500. It IS an LLM failure, so the LLM circuit still counts it (so the
        # breaker can open if the provider keeps failing).
        _llm_circuit.record_failure()
        _svc_unavailable = True
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "test_chat_llm_provider_unavailable",
            error=llm_error,
            error_type=type(exc).__name__,
            exc_info=True,
        )
    except asyncio.TimeoutError:
        # Pipeline exceeded the server wall-clock budget — a hung upstream, not a
        # pipeline bug. Free the slot and return a retryable 503 (same envelope as
        # LLMError). NOT counted against the LLM breaker: the timeout is a
        # whole-pipeline budget, not a confirmed single-provider failure.
        _svc_unavailable = True
        llm_error = f"PipelineTimeout: exceeded {_pipeline_timeout_s}s"
        logger.warning(
            "test_chat_pipeline_timeout",
            timeout_s=_pipeline_timeout_s,
            error_type="TimeoutError",
        )
    except Exception as exc:  # noqa: BLE001 — request entrypoint; must catch all to record CB + return 5xx envelope. exc_info=True preserves stack.
        _llm_circuit.record_failure()
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "test_chat_pipeline_failed",
            error=llm_error,
            error_type=type(exc).__name__,
            exc_info=True,
        )

    duration_ms = int((time.perf_counter() - t0) * 1000)

    # ── 3. EXTRACT RESULTS FROM PIPELINE STATE ─────────────────────────────
    tokens = final_state.get("tokens") or {"prompt": 0, "completion": 0, "cached": 0}
    cost_usd = float(final_state.get("cost_usd", 0.0))
    citations = list(final_state.get("citations") or [])
    graded_chunks = final_state.get("graded_chunks") or []
    model_used = final_state.get("model_used", "")
    intent = final_state.get("intent", "")

    # Build sources from graded_chunks (pipeline already has source info)
    # Preview 1500 chars so external evaluator (RAGAS judge) can verify
    # claims against real chunk body, not header. See pin test
    # tests/unit/test_chat_sources_preview_length.py.
    sources = [
        {
            "document_name": c.get("document_name") or c.get("metadata", {}).get("document_title") or "(không tên)",
            "source_url": c.get("source_url") or None,
            "chunk_index": c.get("chunk_index", 0),
            "score": round(float(c.get("score", 0)), 4),
            "preview": (c.get("content") or c.get("text") or "")[:1500],
        }
        for c in graded_chunks
    ]

    # Debug info: surface decompose / parent-child / grounding / rewrite
    # telemetry on the response JSON for per-turn audits.
    scores = [float(c.get("score", 0)) for c in graded_chunks]
    _retrieved_chunks = final_state.get("retrieved_chunks") or []
    retrieval_debug = {
        "top_k": len(_retrieved_chunks),
        "chunks_graded": len(graded_chunks),
        "score_max": round(max(scores), 4) if scores else 0,
        "score_min": round(min(scores), 4) if scores else 0,
        "score_avg": round(sum(scores) / len(scores), 4) if scores else 0,
        "history_messages": len(conversation_history),
        "condensed": final_state.get("original_query") is not None,
        "intent": intent,
        "model": model_used,
        "source": "query_graph",
        # P15 telemetry — if the feature didn't fire this turn, the field
        # is empty / 0 / False. Absence means "not triggered".
        "rewritten_query": (final_state.get("rewritten_query") or "")[:300],
        "cached_tokens": int((final_state.get("tokens") or {}).get("cached", 0)),
        # Decompose node writes `sub_queries` (a list[str] of 2-4 atomic
        # questions) when the multi-hop router fires. Each sub-query then
        # drives its own hybrid_search branch in retrieve. Absence/empty =
        # not triggered.
        "query_decomposed": bool(final_state.get("sub_queries") or []),
        "sub_queries": list(final_state.get("sub_queries") or []),
        "parents_expanded_count": sum(
            1 for c in _retrieved_chunks if c.get("is_parent_expanded")
        ),
        "guardrail_flags": final_state.get("guardrail_flags") or [],
        # Numeric-fidelity observe verdict (truth-audit Phase 4) — counts +
        # capped unsupported tokens; consumed by the trace harness.
        "numeric_fidelity": final_state.get("numeric_fidelity"),
        # Cache status — "bypassed" when bypass_cache=True, else "hit" or "miss"
        # (inferred from answer_type since check_cache node doesn't expose a
        # separate state key for the non-bypass path).
        "cache_status": (
            final_state.get("cache_status")
            or ("hit" if final_state.get("answer_type") == "cache_hit" else "miss")
        ),
        # Phase-3 "llm" when GenerateOutput emitted citations directly,
        # "auto_fallback" when the legacy free-form path synthesised them from
        # top-K graded chunks. Empty / missing -> structured succeeded with
        # zero citations (truthful empty list).
        "citations_source": final_state.get("citations_source", ""),
        "intent_corrected": bool(final_state.get("intent_corrected", False)),
    }

    # ── 4. FINALIZE LOG + SAVE HISTORY (test-specific, parallel) ───────────
    async def _finalize_log():
        try:
            await request_log_repo.finalize_request_log(
                request_id, record_tenant_id=tenant_id,
                answer_hash=content_hash_required(answer) if answer else None,
                answer_text=answer or None,
                model_name=model_used,
                prompt_tokens=int(tokens.get("prompt", 0)),
                completion_tokens=int(tokens.get("completion", 0)),
                cost_usd=cost_usd,
                status="success" if not llm_error else "failed",
                error_code="PIPELINE_ERROR" if llm_error else None,
                error_message=llm_error,
                # G15: only refs (chunk_id + rank + score) -- previews
                # used to live in inline JSONB; relational table stores
                # FK-validated refs only, no PII. Stats-route turns are
                # attributed to their real source chunks (STEP-5) here.
                retrieved_chunks=_build_stats_attributed_refs(
                    graded_chunks, final_state
                ),
                # Deterministic guard self-verdict → metadata_json (observe-only,
                # sacred #10 safe): unlocks DB-queryable grounding-fail /
                # numeric-flag rates without re-running. NOT a correctness grade.
                metadata={"guard_verdict": build_verdict_meta(final_state)},
            )
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            logger.warning(
                "test_chat_log_finalize_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _save_history():
        if not answer:
            return
        # Truth-audit verification: persist the chunks the LLM actually saw
        # with the assistant turn (auditable without debug mode).
        _sc = json.dumps(
            build_served_chunks(final_state.get("graded_chunks") or []),
            ensure_ascii=False,
        )
        async with sf() as session:
            # INSERT mới + TRIM cũ trong 1 transaction
            # Thêm đuôi (2 rows: user + assistant), cắt đầu (giữ max_history mới nhất)
            await session.execute(
                text("""
                    WITH ins AS (
                        INSERT INTO chat_histories (record_bot_id, channel_type, connect_id, role, content, served_chunks)
                        VALUES (:bid, :ch, :cid, 'user', :q, NULL),
                               (:bid, :ch, :cid, 'assistant', :a, CAST(:sc AS jsonb))
                    )
                    DELETE FROM chat_histories
                    WHERE id IN (
                        SELECT id FROM chat_histories
                        WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                        ORDER BY id DESC
                        OFFSET :keep
                    )
                """),
                {"bid": bot_cfg.id, "ch": req.channel_type, "cid": connect_id,
                 "q": req.question, "a": answer, "sc": _sc, "keep": max_history},
            )
            await session.commit()

    await asyncio.gather(_finalize_log(), _save_history())

    # ── 5. FIRE CHAT COMPLETION HOOKS (Open-Closed extension point) ────────
    # Token quota deduct + Redis L1 sync + threshold notify. Adding new
    # side-effects = register hook in bootstrap.py, NEVER edit this file.
    # 2-stage commit guarantees Redis+DB sync (DB → commit → Redis post).
    try:
        from datetime import datetime, timezone as _tz
        from ragbot.application.events.chat_completed import ChatCompletedEvent

        _hook_event = ChatCompletedEvent(
            record_tenant_id=tenant_id,
            workspace_id=bot_cfg.workspace_id,
            bot_id=bot_cfg.bot_id,
            channel_type=bot_cfg.channel_type,
            record_bot_id=bot_cfg.id,
            request_id=request_id,
            prompt_tokens=int(tokens.get("prompt", 0)),
            completion_tokens=int(tokens.get("completion", 0)),
            tokens_used_delta=int(tokens.get("prompt", 0)) + int(tokens.get("completion", 0)),
            refusal_reason=None,
            intent=final_state.get("intent"),
            timestamp_iso=datetime.now(_tz.utc).isoformat(),
        )
        _hook_registry = container.chat_hook_registry()
        async with sf() as _hook_session:
            await _hook_registry.fire_db_stage(_hook_event, session=_hook_session)
            await _hook_session.commit()
            await _hook_registry.fire_post_stage(_hook_event, session=_hook_session)
    except Exception as _exc:  # noqa: BLE001 — hooks are side-effects, never block response
        logger.warning(
            "test_chat_hook_dispatch_failed",
            error=str(_exc)[:200],
            error_type=type(_exc).__name__,
        )

    if llm_error:
        if _svc_unavailable:
            raise HTTPException(status_code=503, detail="Upstream model service temporarily unavailable. Retry shortly.")
        raise HTTPException(status_code=500, detail="RAG pipeline failed. Check server logs.")

    response: dict[str, Any] = {
        "ok": True, "bot_id": req.bot_id, "question": req.question, "answer": answer,
        "answer_type": final_state.get("answer_type", "answered" if answer else "no_context"),
        "answer_reason": final_state.get("answer_reason"),
        "chunks_used": len(graded_chunks), "top_score": scores[0] if scores else 0,
        "sources": sources,
        "citations": citations,
        "tokens": {"prompt": int(tokens.get("prompt", 0)), "completion": int(tokens.get("completion", 0)), "cached": int(tokens.get("cached", 0))},
        "cost_usd": cost_usd, "duration_ms": duration_ms, "request_id": str(request_id),
        "debug": retrieval_debug,
    }

    # HARN-3: opt-in chunk-content payload for offline harness + LLM judge.
    # Without this, the judge only sees source NAMES and conservatively marks
    # any specific number as hallucinated. With `debug=full`, it sees chunk
    # CONTENT and can verify numbers against what was actually retrieved.
    # Prefer graded_chunks (what the LLM actually saw); fall back to raw
    # retrieved_chunks if grading stage produced nothing.
    if (req.debug or "").lower() == "full":
        _src_chunks = graded_chunks or _retrieved_chunks
        response["retrieved_chunks_content"] = [
            {
                "chunk_id": (
                    (c.get("chunk_id") if isinstance(c, dict) else None)
                    or (c.get("id") if isinstance(c, dict) else None)
                ),
                "content": ((c.get("content") if isinstance(c, dict) else None)
                            or (c.get("text") if isinstance(c, dict) else None)
                            or "")[:3000],
                "source": (
                    (c.get("document_name") if isinstance(c, dict) else None)
                    or (c.get("source") if isinstance(c, dict) else None)
                    or ((c.get("metadata") or {}).get("document_title") if isinstance(c, dict) else None)
                ),
                "score": float(c.get("score", 0)) if isinstance(c, dict) else None,
            }
            for c in _src_chunks
        ]

    return response


async def _stream_simulated(
    answer: str,
    sources: list,
    duration_ms: int,
    word_delay_ms: int,
    answer_type: str = "answered",
    answer_reason: str | None = None,
):
    """Legacy simulated SSE stream — split full answer on whitespace + delay.

    Used as a backward-compat fallback when ``streaming_use_real_llm=False``
    or for the cache-hit path (no LLM call → no real stream).
    """
    import json as _json  # noqa: PLC0415

    yield f"data: {_json.dumps({'type': 'status', 'stage': 'generating'}, ensure_ascii=False)}\n\n"

    words = answer.split(" ") if answer else []
    delay_s = word_delay_ms / 1000.0
    for i, word in enumerate(words):
        token = word if i == 0 else f" {word}"
        yield f"data: {_json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"
        if delay_s > 0:
            await asyncio.sleep(delay_s)

    yield f"data: {_json.dumps({'type': 'done', 'answer': answer, 'answer_type': answer_type, 'answer_reason': answer_reason, 'sources': sources, 'duration_ms': duration_ms}, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def test_chat_stream(req: TestChatRequest, request: Request) -> StreamingResponse:
    """Chat test với SSE streaming — real per-token LLM streaming.

    Cùng pipeline production như ``/test/chat`` nhưng trả
    ``StreamingResponse`` (``text/event-stream``). Khi ``streaming_use_real_llm``
    bật (mặc định) generate node đẩy từng delta lên ``_stream_sink`` và
    endpoint yield SSE ngay. Khi tắt → fallback simulated word-by-word.

    Trade-off math-lockdown:
        Real streaming push token đi trước khi pipeline kiểm tra
        ``find_ungrounded_numbers`` ở cuối generate. Nếu lockdown hard-block,
        endpoint emit thêm event ``replace`` để client thay phần đã streamed
        bằng câu trả lời chuẩn (giữ tính an toàn cao nhất, đánh đổi 1 lần
        UX flicker khi lockdown trigger).

    SSE format:
        data: {"type": "status", "stage": "generating"}\\n\\n
        data: {"type": "token", "content": "từ"}\\n\\n
        data: {"type": "replace", "answer": "...", "reason": "..."}\\n\\n  # optional
        data: {"type": "done", "answer": "...", "sources": [...], "duration_ms": 1234}\\n\\n

    @param req: {bot_id, channel_type, question}
    @return: StreamingResponse (text/event-stream)
    """
    _require_owner(request)
    container = _container(request)
    sf = _sf(request)
    cfg_svc = _sys_config(request)
    # Honour caller-supplied connect_id so harness rooms stay isolated;
    # default to shared DEFAULT_CONNECT_ID for the demo UI's single room.
    connect_id = req.connect_id or DEFAULT_CONNECT_ID
    t0 = time.perf_counter()

    # Check streaming_enabled flag
    streaming_on = await cfg_svc.get_bool("streaming_enabled", True)
    if not streaming_on:
        raise HTTPException(status_code=403, detail="Streaming is disabled via system_config")

    # Demo path: tenant lifted from JWT bearer; registry is Redis-cached.
    record_tenant_uuid = (
        _tenant_scope(request) or _PLATFORM_TENANT_FALLBACK_UUID
    )
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )
    registry = container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_uuid,
        workspace_slug,
        req.bot_id,
        req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail=f"Bot {req.bot_id}:{req.channel_type} not found")

    request_id = uuid.uuid4()
    message_id = int(time.time() * 1000)
    tenant_id = _caller_tenant_uuid(request) or _PLATFORM_TENANT_FALLBACK_UUID
    # Per-bot max_history via single source of truth (bot_limits.py)
    system_max = await cfg_svc.get_int("chat_max_history", DEFAULT_MAX_HISTORY)
    max_history = resolve_bot_limit(bot_cfg, "max_history", system_default=system_max)
    if max_history is None:
        max_history = system_max

    # ── 1. LOAD HISTORY ──
    # MT-1 reconcile: merge chat_histories (this transport) + messages
    # (worker transport) for the same (record_bot_id, connect_id).
    conversation_history = await HistoryReconciler(sf).load(
        record_bot_id=bot_cfg.id,
        connect_id=connect_id,
        channel_type=req.channel_type,
        limit=max_history,
    )

    # ── 2. BUILD + RUN PRODUCTION PIPELINE ──
    from ragbot.orchestration.graph_assembly import (  # noqa: PLC0415
        build_chat_initial_state,
        build_graph_di_kwargs,
        resolve_kg_service,
    )
    from ragbot.orchestration.query_graph import get_graph  # noqa: PLC0415
    from ragbot.orchestration.state import GraphState  # noqa: PLC0415
    from ragbot.application.services.step_tracker import StepTracker  # noqa: PLC0415

    request_log_repo = container.request_log_repo()
    question_hash = content_hash_required(req.question)

    try:
        await request_log_repo.create_request_log(
            request_id=request_id, record_tenant_id=tenant_id,
            workspace_id=workspace_slug,
            connect_id=connect_id,
            question_hash=question_hash, question_text=req.question,
            message_id=message_id,
            record_bot_id=bot_cfg.id, channel_type=req.channel_type,
            trace_id=f"test-stream-{request_id}",
        )
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        logger.warning(
            "test_chat_stream_log_create_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    tracker = StepTracker(
        request_id=request_id, record_tenant_id=tenant_id, repo=request_log_repo,
    )

    if not _llm_circuit.can_execute():
        raise HTTPException(status_code=503, detail="LLM circuit breaker open — thử lại sau 30s")

    def _opt(attr: str):
        """Lấy optional dependency từ container, None nếu không có."""
        if not hasattr(container, attr):
            return None
        try:
            return getattr(container, attr)()
        except Exception:  # noqa: BLE001 — optional dep, return None if unavailable
            return None

    try:
        graph = await get_graph(**build_graph_di_kwargs(container))
    except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
        logger.exception(
            "test_chat_stream_graph_build_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=f"RAG pipeline not configured: {exc}") from exc

    pipeline_config = await _build_pipeline_config(cfg_svc, bot_cfg)

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
        else (bot_cfg.system_prompt or "")
    )

    conversation_id = await _resolve_action_conversation_id(
        _opt("conv_repo"), bot_cfg,
        connect_id=connect_id, tenant_id=tenant_id, workspace_slug=workspace_slug,
    )

    initial_state: GraphState = build_chat_initial_state(
        record_tenant_id=tenant_id,
        request_id=request_id,
        message_id=message_id,
        conversation_id=conversation_id,
        record_bot_id=bot_cfg.id,
        bot_cfg=bot_cfg,
        channel_type=req.channel_type,
        workspace_id=workspace_slug,
        # Demo endpoints carry no group claims — explicit empty list.
        user_groups=[],
        query=req.question,
        conversation_history=conversation_history,
        pipeline_config=pipeline_config,
        tracker=tracker,
        assembled_sysprompt=_assembled_sysprompt,
        oos_template_resolved=_oos_template_resolved,
        bot_language=_bot_language,
        kg_service=resolve_kg_service(pipeline_config),
        session_factory=_opt("session_factory"),
    )
    # test-mode: skip semantic cache when True (transport-specific key)
    initial_state["bypass_cache"] = req.bypass_cache

    # ── 3. STREAMING DECISION ──
    use_real_stream = await cfg_svc.get_bool(
        "streaming_use_real_llm", DEFAULT_STREAMING_USE_REAL_LLM,
    )
    word_delay_ms = await cfg_svc.get_int(
        "streaming_word_delay_ms", DEFAULT_STREAMING_WORD_DELAY_MS,
    )

    def _build_sources(graded: list) -> list:
        # Preview length 1500 chars: enough for legal Điều / FAQ block to
        # appear in /chat API response so external evaluators (RAGAS LLM
        # judge, audit tools) see real chunk content instead of header
        # snippet. Pre-2026-05-27 was 200 chars → judge fail-verified
        # legitimate claims because chunk body invisible.
        return [
            {
                "document_name": c.get("document_name") or c.get("metadata", {}).get("document_title") or "(không tên)",
                "source_url": c.get("source_url") or None,
                "chunk_index": c.get("chunk_index", 0),
                "score": round(float(c.get("score", 0)), 4),
                "preview": (c.get("content") or c.get("text") or "")[:1500],
            }
            for c in graded
        ]

    async def _finalize_log(final_state: dict, answer: str, llm_error: str | None):
        tokens = final_state.get("tokens") or {"prompt": 0, "completion": 0, "cached": 0}
        cost_usd = float(final_state.get("cost_usd", 0.0))
        graded_chunks = final_state.get("graded_chunks") or []
        model_used = final_state.get("model_used", "")
        try:
            await request_log_repo.finalize_request_log(
                request_id, record_tenant_id=tenant_id,
                answer_hash=content_hash_required(answer) if answer else None,
                answer_text=answer or None,
                model_name=model_used,
                prompt_tokens=int(tokens.get("prompt", 0)),
                completion_tokens=int(tokens.get("completion", 0)),
                cost_usd=cost_usd,
                status="success" if not llm_error else "failed",
                error_code="PIPELINE_ERROR" if llm_error else None,
                error_message=llm_error,
                # G15: only refs (chunk_id + rank + score) -- previews
                # used to live in inline JSONB; relational table stores
                # FK-validated refs only, no PII. Stats-route turns are
                # attributed to their real source chunks (STEP-5) here.
                retrieved_chunks=_build_stats_attributed_refs(
                    graded_chunks, final_state
                ),
                # Deterministic guard self-verdict → metadata_json (observe-only,
                # sacred #10 safe): unlocks DB-queryable grounding-fail /
                # numeric-flag rates without re-running. NOT a correctness grade.
                metadata={"guard_verdict": build_verdict_meta(final_state)},
            )
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            logger.warning(
                "test_chat_stream_log_finalize_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _save_history(answer: str, final_state: dict | None = None):
        if not answer:
            return
        # Truth-audit verification: chunks the LLM saw ride with the turn.
        _sc = json.dumps(
            build_served_chunks((final_state or {}).get("graded_chunks") or []),
            ensure_ascii=False,
        )
        async with sf() as session:
            # INSERT mới + TRIM cũ trong 1 transaction
            await session.execute(
                text("""
                    WITH ins AS (
                        INSERT INTO chat_histories (record_bot_id, channel_type, connect_id, role, content, served_chunks)
                        VALUES (:bid, :ch, :cid, 'user', :q, NULL),
                               (:bid, :ch, :cid, 'assistant', :a, CAST(:sc AS jsonb))
                    )
                    DELETE FROM chat_histories
                    WHERE id IN (
                        SELECT id FROM chat_histories
                        WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                        ORDER BY id DESC
                        OFFSET :keep
                    )
                """),
                {"bid": bot_cfg.id, "ch": req.channel_type, "cid": connect_id,
                 "q": req.question, "a": answer, "sc": _sc, "keep": max_history},
            )
            await session.commit()

    # ── 4a. REAL STREAMING PATH ──
    # Run pipeline in a background task with a sink the generate node pushes
    # token deltas onto. SSE generator drains the sink concurrently.
    if use_real_stream:
        # Bounded queue → backpressure when SSE consumer lags. Producer
        # (LLM stream) blocks on full queue instead of buffering tokens
        # unbounded, preventing OOM on stuck/disconnected clients.
        sink: asyncio.Queue = asyncio.Queue(maxsize=DEFAULT_SSE_SINK_MAXSIZE)
        initial_state["_stream_sink"] = sink  # type: ignore[typeddict-unknown-key]

        final_state_holder: dict = {"state": None, "error": None, "sources": []}

        async def _run_graph():
            try:
                final_state = await graph.ainvoke(
                    initial_state,
                    config={"recursion_limit": pipeline_config["graph_recursion_limit"]},
                )
                final_state_holder["state"] = final_state
                final_state_holder["sources"] = _build_sources(final_state.get("graded_chunks") or [])
                _llm_circuit.record_success()
            except Exception as exc:  # noqa: BLE001 — background pipeline driver; record CB + log + reraise to abort SSE.
                _llm_circuit.record_failure()
                final_state_holder["error"] = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "test_chat_stream_pipeline_failed",
                    error=final_state_holder["error"],
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                raise
            finally:
                # Always release the consumer — even on cache_hit (generate
                # node skipped) or pipeline error.
                await sink.put(_STREAM_SENTINEL)

        graph_task = asyncio.create_task(_run_graph())

        async def _on_complete(final_state: dict, answer: str, _duration_ms: int):
            err = final_state_holder.get("error")
            await asyncio.gather(
                _finalize_log(final_state, answer, err),
                _save_history(answer, final_state),
            )

        _telemetry_extra = {
            "request_id": str(request_id),
            "record_tenant_id": str(tenant_id),
            "record_bot_id": str(bot_cfg.id),
            "workspace_id": workspace_slug,
            "bot_id": req.bot_id,
            "channel_type": req.channel_type,
            "feature_flag": "streaming_use_real_llm",
        }
        return StreamingResponse(
            # Wave H Phase 1 — named SSE events (first_token / chunk /
            # citations / done) replace the bare ``data:`` framing for the
            # demo endpoint so the test page can wire dedicated
            # ``EventSource.addEventListener('first_token', …)`` handlers
            # against the TTFT SLA. Production ``/chat/stream`` stays on
            # legacy framing until its UI migrates.
            _shared_stream_real_llm(
                sink,
                graph_task,
                final_state_holder,
                t0,
                _on_complete,
                telemetry_extra=_telemetry_extra,
                named_events=True,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── 4b. LEGACY SIMULATED PATH (backward-compat fallback) ──
    answer = ""
    llm_error = None
    final_state: dict = {}

    try:
        final_state = await graph.ainvoke(initial_state, config={"recursion_limit": pipeline_config["graph_recursion_limit"]})
        answer = final_state.get("answer", "") or ""
        _llm_circuit.record_success()
    except Exception as exc:  # noqa: BLE001 — request entrypoint; must catch all to record CB + return 5xx envelope. exc_info=True preserves stack.
        _llm_circuit.record_failure()
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "test_chat_stream_pipeline_failed",
            error=llm_error,
            error_type=type(exc).__name__,
            exc_info=True,
        )

    duration_ms = int((time.perf_counter() - t0) * 1000)
    sources = _build_sources(final_state.get("graded_chunks") or [])

    await asyncio.gather(
        _finalize_log(final_state, answer, llm_error),
        _save_history(answer, final_state),
    )

    if llm_error:
        raise HTTPException(status_code=500, detail="RAG pipeline failed. Check server logs.")

    _at = final_state.get("answer_type", "answered" if answer else "no_context")
    _ar = final_state.get("answer_reason")
    return StreamingResponse(
        _stream_simulated(answer, sources, duration_ms, word_delay_ms, answer_type=_at, answer_reason=_ar),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/history")
async def test_chat_history(
    bot_id: str,
    channel_type: str,
    request: Request,
    connect_id: str | None = None,
) -> dict:
    """Lấy lịch sử chat từ DB theo bot + channel + connect_id (room).

    F5 reload bug fix (2026-05-28): previously the endpoint ignored caller
    ``connect_id`` and hardcoded ``DEFAULT_CONNECT_ID`` for the lookup,
    while the POST /chat path persists rows under the caller-supplied
    ``connect_id``. Result: UI saw empty history after F5 even though
    chat_histories rows were intact in DB. Now we honour the same
    ``connect_id or DEFAULT_CONNECT_ID`` resolution as the POST path so
    save-and-load target the same room consistently.

    @param bot_id, channel_type: external bot identity (HTTP body 2-key).
    @param connect_id: room id matching the POST /chat caller. Falls back
        to ``DEFAULT_CONNECT_ID`` when caller does not pass it (demo UI
        single-room case stays backward compatible).
    @return: {ok, messages, total}
    """
    bot_uuid = await _find_bot_uuid(request, bot_id, channel_type)
    resolved_cid = connect_id or DEFAULT_CONNECT_ID
    sf = _sf(request)
    async with sf() as session:
        rows = (await session.execute(
            text("""
                SELECT role, content, created_at, served_chunks FROM chat_histories
                WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                ORDER BY id ASC
            """),
            {"bid": bot_uuid, "ch": channel_type, "cid": resolved_cid},
        )).fetchall()
    return {
        "ok": True,
        "messages": [{
            "role": r[0], "content": r[1],
            "created_at": r[2].isoformat() if r[2] else None,
            # chunks the LLM saw for this assistant turn (truth-audit verify)
            "served_chunks": (json.loads(r[3]) if isinstance(r[3], str) else r[3]) if r[3] else None,
        } for r in rows],
        "total": len(rows),
        "connect_id": resolved_cid,
    }


@router.delete("/chat")
async def test_chat_clear(req: TestChatClearRequest, request: Request) -> dict:
    """Xóa sạch chat_histories, request_logs, model_invocations cho bot.
    @param req: {bot_id, channel_type}
    @return: {ok, deleted_messages, deleted_logs}
    """
    bot_uuid = await _find_bot_uuid(
        request, req.bot_id, req.channel_type, workspace_id=req.workspace_id,
    )
    sf = _sf(request)
    async with sf() as session:
        # Xóa chat_histories theo room (bot_id + channel_type + connect_id)
        chat_del = await session.execute(
            text("DELETE FROM chat_histories WHERE record_bot_id = :bid AND channel_type = :ch AND connect_id = :cid"),
            {"bid": bot_uuid, "ch": req.channel_type, "cid": DEFAULT_CONNECT_ID},
        )
        # Xóa model_invocations liên quan request_logs của bot.
        # NOTE: request_logs.request_id is the PK (no prefix — external contract),
        # but model_invocations.record_request_id is the internal FK (prefixed).
        # The earlier version of this query used "WHERE request_id IN (...)" on
        # model_invocations, which 500s because model_invocations has no column
        # named request_id. Fix: use the actual FK column name.
        await session.execute(
            text("""
                DELETE FROM model_invocations
                WHERE record_request_id IN (
                    SELECT request_id FROM request_logs WHERE record_bot_id = :bid
                )
            """),
            {"bid": bot_uuid},
        )
        # Xóa request_logs
        log_del = await session.execute(
            text("DELETE FROM request_logs WHERE record_bot_id = :bid"),
            {"bid": bot_uuid},
        )
        await session.commit()

    return {
        "ok": True,
        "deleted_messages": chat_del.rowcount or 0,
        "deleted_logs": log_del.rowcount or 0,
    }


__all__ = [
    "router",
    "test_chat",
    "test_chat_stream",
    "test_chat_history",
    "test_chat_clear",
]
