"""Chat worker — consume `chat.received.v1` from Redis Streams and run RAG pipeline.

The pipeline delegates to the LangGraph StateGraph assembled by
``ragbot.orchestration.query_graph.build_graph``. Every LLM-bound node
is wrapped by ``InvocationLogger`` (INVARIANT #2) and every stage is
tracked by ``StepTracker``.
"""

from __future__ import annotations

import asyncio
import json  # noqa: F401 — retained for ``json.JSONDecodeError`` type reference below
import signal
import time
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError

from ragbot.application.dto.chat_payload import ChatReceivedPayload
from ragbot.bootstrap import Container
from ragbot.config.logging import (
    bind_request_context,
    channel_type_ctx,
    clear_request_context,
    mode_ctx,
    record_bot_id_ctx,
    setup_logging,
)
from ragbot.config.settings import get_settings
from ragbot.infrastructure.observability.metrics import (
    chat_worker_queue_depth,
    request_total,
)
from ragbot.shared.constants import (
    DEFAULT_BATCH_STEP_LOGGING_ENABLED,
    DEFAULT_CALLBACK_MAX_RETRIES,
    DEFAULT_CALLBACK_TIMEOUT_S,
    DEFAULT_CHAT_WORKER_CONCURRENCY,
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_TOKENS_TOTAL,
    SEMANTIC_CACHE_THRESHOLD,
    SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED,
    SUBJECT_CHAT_RECEIVED,
)
from ragbot.application.services.step_tracker import StepTracker
from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.orchestration.graph_assembly import (
    build_chat_initial_state,
    build_graph_di_kwargs,
    resolve_kg_service,
)
from ragbot.orchestration.query_graph import get_graph
from ragbot.orchestration.state import GraphState
from ragbot.shared.token_budget import (
    can_answer,
    compute_effective_max_tokens,
)
from ragbot.shared.hashing import content_hash_required
from ragbot.shared.errors import WorkspaceIdInvalid
from ragbot.shared.types import (
    BotId,
    ConversationId,
    JobId,
    TenantId,
    TraceId,
    UserId,
    WorkspaceId,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

from .callbacks import _persist_and_callback
from .config import (
    _CHAT_CONFIG_KEYS,
    _cfg_bool,
    _cfg_get,
    _cfg_int,
)
from .payload import _maybe_redact_chat_query, _resolve_record_tenant_id
from .pipeline_config import _build_pipeline_config

logger = structlog.get_logger(__name__)

__all__ = [
    "handle_chat_received",
    "main",
]


async def handle_chat_received(payload: dict[str, Any], container: Container) -> None:
    """Xử lý sự kiện chat.received — validate, chạy RAG pipeline, lưu kết quả.
    @param payload: dữ liệu sự kiện (bot_id, content, conversation_id, ...)
    @param container: DI container
    """
    record_tenant_id = await _resolve_record_tenant_id(payload, container)
    bind_request_context(
        trace_id=payload.get("trace_id", ""),
        record_tenant_id=record_tenant_id,
        bot_id=payload.get("bot_id"),
        conversation_id=payload.get("conversation_id"),
    )
    try:
        await _handle_chat_received_body(payload, container, record_tenant_id)
    finally:
        clear_request_context()


async def _handle_chat_received_body(
    payload: dict[str, Any],
    container: Container,
    record_tenant_id: UUID | None,
) -> None:
    logger.info("chat.received.consumed", payload_keys=list(payload.keys()))

    _req_t0 = time.perf_counter()
    _channel_type = str(payload.get("channel_type") or "unknown")
    # Kept defined for the post-response p99 outlier hook so the
    # latency-emit path stays valid even when the pipeline body never
    # populated ``final_state`` (timeout, validation reject, etc.).
    final_state: dict[str, Any] = {}

    # H3 — Parse job_id TRUC moi validation khac. Neu missing -> khong co
    # job reference de update; log va return ngay.
    job_id_raw = payload.get("job_id")
    if not job_id_raw:
        logger.error("chat_worker_missing_job_id", payload_keys=list(payload.keys()))
        return
    try:
        job_id = JobId(UUID(str(job_id_raw)))
    except (TypeError, ValueError):
        logger.error("chat_worker_invalid_job_id", job_id_raw=job_id_raw)
        return

    job_repo = container.job_repo()

    # H5 — Pydantic validation toan bo payload.
    try:
        valid = ChatReceivedPayload.model_validate(payload)
    except ValidationError as e:
        logger.error(
            "chat_received_invalid_payload",
            errors=e.errors(),
            payload_keys=list(payload.keys()),
        )
        try:
            await job_repo.update_status(
                job_id, record_tenant_id=None, status="failed",
                error=f"INVALID_PAYLOAD: {e.errors()[:3]}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed_updating_invalid_payload_job")
        return

    # External identifiers from upstream — already validated & trimmed.
    bot_id_str = valid.bot_id
    channel_type_str = valid.channel_type

    # record_tenant_id UUID resolved upstream by `_resolve_record_tenant_id`
    # (UUID payload claim wins; legacy INT translates via tenants.config).
    # If still None we cannot scope the bot lookup — fail the job loudly.
    if record_tenant_id is None:
        logger.error(
            "chat_worker_missing_record_tenant_id",
            payload_keys=list(payload.keys()),
        )
        try:
            await job_repo.update_status(
                job_id, record_tenant_id=None, status="failed",
                error="RECORD_TENANT_ID_REQUIRED",
            )
        except Exception:  # noqa: BLE001
            logger.exception("missing_tenant_update_failed")
        return
    record_tenant_id_typed: TenantId = TenantId(record_tenant_id)

    # Resolve the workspace slug from the queued payload BEFORE the
    # registry lookup so the cache key + DB query carry the full 4-key
    # identity. Missing / empty falls back to the tenant UUID; a malformed
    # slug fails the job loudly rather than silently coercing.
    raw_ws = payload.get("workspace_id")
    workspace_slug: WorkspaceId
    try:
        workspace_slug = resolve_workspace_id(
            raw_ws, record_tenant_id=record_tenant_id,
        )
    except WorkspaceIdInvalid as exc:
        logger.warning(
            "chat_worker_invalid_workspace_id",
            raw=str(raw_ws)[:64],
            error=str(exc),
        )
        try:
            await job_repo.update_status(
                job_id, record_tenant_id=record_tenant_id_typed, status="failed",
                error="WORKSPACE_ID_INVALID",
            )
        except Exception:  # noqa: BLE001
            logger.exception("invalid_workspace_id_update_failed")
        return

    # Feed the resolved slug to the RLS workspace GUC binder (ADR-W1-D3).
    bind_request_context(workspace_id=str(workspace_slug))

    # Lookup bot from cache (4-key external identity).
    bot_registry = container.bot_registry_service()
    bot_cfg = await bot_registry.lookup(
        record_tenant_id, workspace_slug, bot_id_str, channel_type_str,
    )
    if bot_cfg is None:
        logger.warning(
            "bot_not_found",
            workspace_id=workspace_slug,
            bot_id=bot_id_str,
            channel_type=channel_type_str,
        )
        try:
            await job_repo.update_status(
                job_id, record_tenant_id=record_tenant_id_typed, status="failed",
                error="BOT_NOT_FOUND",
            )
        except Exception:  # noqa: BLE001
            logger.exception("bot_not_found_update_failed")
        return

    # Internal UUID — downstream FKs on messages/conversations all refer to
    # `bots.id` UUID (not external bot_id VARCHAR).
    record_bot_id = BotId(bot_cfg.id)
    # Token-ledger attribution (production query path): record_bot_id (report
    # key) + mode='query' so every LLM call this turn makes is attributed.
    record_bot_id_ctx.set(str(bot_cfg.id))
    channel_type_ctx.set(str(channel_type_str or ""))  # 4th identity key for ledger
    mode_ctx.set("query")
    if not valid.conversation_id:
        logger.error("chat_worker_missing_conversation_id", job_id=str(job_id))
        try:
            await job_repo.update_status(
                job_id, record_tenant_id=record_tenant_id, status="failed",
                error="CONVERSATION_ID_REQUIRED",
            )
        except Exception:  # noqa: BLE001
            logger.exception("missing_conv_update_failed")
        return
    conv_id = ConversationId(UUID(valid.conversation_id))
    user_id = UserId(valid.user_id)
    trace_id = TraceId(valid.trace_id)

    conv_repo = container.conv_repo()
    request_log_repo = container.request_log_repo()
    message_repo = container.message_repo()
    tenant_policy_repo = container.tenant_policy_repo()
    clock = container.clock()
    request_id = job_id  # 1 chat = 1 request

    # `payload["message_id"]` la ID INT cua upstream service (khach).
    # Luu thang vao request_logs.message_id de group metric theo cau hoi khach.
    external_message_id = valid.message_id
    question_text = valid.content

    # PII redaction at the WORKER BOUNDARY (Master Finding #4 fix).
    # Per-bot opt-in via plan_limits.pii_redaction_enabled — default False
    # so existing tenants see no behaviour change. When enabled, the raw
    # user query is masked (e.g. [EMAIL], [PHONE], [CCCD]) BEFORE message
    # persist, request_log hash, and LLM call so no raw PII reaches the DB
    # or the model.
    question_text = _maybe_redact_chat_query(
        question_text,
        bot_cfg=bot_cfg,
        pii_redactor=container.pii(),
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
    )

    question_hash = content_hash_required(question_text)

    # 1a/1b/1c — three independent persist operations on three DISTINCT
    # repos (each owning its own session_factory; verified Q7). CLAUDE.md
    # Async Rule 1 + Rule 7: independent + no shared session = gather-safe.
    #
    # Failure semantics: required outputs (audit chain start). Use
    # ``return_exceptions=True`` to allow ALL ops to attempt before we
    # see results; then raise the first failure so the worker NACKs
    # the message and the stream redelivers (exactly-once retry).
    internal_message_uuid = uuid4()
    persist_results = await asyncio.gather(
        message_repo.create(
            message_id=internal_message_uuid,
            conversation_id=conv_id,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            role="user",
            content=question_text,
            channel=payload.get("channel", "api"),
        ),
        request_log_repo.create_request_log(
            request_id=request_id,
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_slug,
            connect_id=str(user_id),
            record_bot_id=record_bot_id,
            record_conversation_id=conv_id,
            message_id=external_message_id,
            trace_id=str(trace_id),
            question_hash=question_hash,
            context_namespace=f"tenant:{record_tenant_id}:bot:{record_bot_id}:conv:{conv_id}",
            channel_type=payload.get("channel_type"),
        ),
        job_repo.update_status(job_id, record_tenant_id=record_tenant_id, status="running"),
        return_exceptions=True,
    )
    for op_name, result in zip(
        ("message_create", "request_log_create", "job_status_running"),
        persist_results,
    ):
        if isinstance(result, BaseException):
            logger.error(
                "chat_worker_persist_failed",
                op=op_name,
                request_id=str(request_id),
                error_type=type(result).__name__,
                error=str(result),
            )
            raise result

    # Phase-B B4 — load system_config snapshot eagerly so the StepTracker
    # construction below can honour the ``batch_step_logging_enabled``
    # flag. ``_cfg`` was previously read deeper in the body; promoting it
    # here is the minimum change that lets the tracker observe the flag
    # before the first ``step()`` call (history_load) executes.
    _cfg_svc = SystemConfigService(
        session_factory=container.session_factory(),
        redis_client=container.redis_client(),
    )
    _cfg = await _cfg_svc.get_many(list(_CHAT_CONFIG_KEYS))
    _batch_step_logging = _cfg_bool(
        _cfg, "batch_step_logging_enabled", DEFAULT_BATCH_STEP_LOGGING_ENABLED,
    )
    tracker = StepTracker(
        request_id=request_id, record_tenant_id=record_tenant_id_typed, repo=request_log_repo,
        metrics=container.metrics_port(),
        batch_enabled=_batch_step_logging,
    )

    routing_reason: str | None = None
    chosen_model = ""
    failure: str | None = None
    answer_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    citations: list[dict] = []
    _callback_max_retries: int = DEFAULT_CALLBACK_MAX_RETRIES
    _callback_timeout_s: int = DEFAULT_CALLBACK_TIMEOUT_S
    _callback_verify_ssl: bool = True
    _callback_hmac_secret: str = ""

    try:
        policy = await tenant_policy_repo.get_policy(
            record_tenant_id=record_tenant_id, record_bot_id=record_bot_id,
        )
        if policy is None:
            routing_reason = "no_policy -> fallback default"
        else:
            routing_reason = (
                f"policy: private={policy['private_doc_ratio']}/"
                f"web={policy['web_search_ratio']}/"
                f"general={policy['general_knowledge_ratio']}"
            )

        # Check token budget before running pipeline (skip if bot has bypass)
        if not bot_cfg.bypass_token_limit:
            try:
                budget = container.token_budget()
                if record_tenant_id is not None:
                    await budget.ensure_affordable(record_tenant_id=record_tenant_id)
            except Exception as exc:  # noqa: BLE001 — QuotaExceeded + repository errors flow through here; treat any failure as quota_exceeded outcome so the worker doesn't block a queued message indefinitely.
                logger.warning(
                    "token_budget_exceeded",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                await job_repo.update_status(job_id, record_tenant_id=record_tenant_id, status="failed", error="QUOTA_EXCEEDED")
                try:
                    request_total.labels(status="quota_exceeded", channel_type=_channel_type).inc()
                except Exception:  # noqa: BLE001
                    pass
                return
        else:
            logger.info("token_budget_bypassed", bot_id=bot_id_str)

        # Per-bot quota gate (tokens_used vs effective_limit). Layered on
        # top of legacy tenant-level token_budget above. bypass_token_check
        # is an ops-only flag distinct from the tenant bypass_token_limit.
        try:
            _cfg_svc_quota = container.system_config_service()
            _system_max_tokens = int(await _cfg_svc_quota.get_int(
                "max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL,
            ))
            _effective_limit = compute_effective_max_tokens(
                system_max_tokens=_system_max_tokens,
                bot_extra_max_tokens=int(getattr(bot_cfg, "extra_max_tokens", 0) or 0),
            )
            # Read tokens_used from Redis L1 fast path, fallback to bot_cfg snapshot.
            _redis = container.redis_client()
            _quota_key = f"ragbot:bot:tokens_used:{bot_cfg.id}"
            _redis_val = await _redis.get(_quota_key)
            _tokens_used = (
                int(_redis_val)
                if _redis_val is not None
                else int(getattr(bot_cfg, "tokens_used", 0) or 0)
            )
            if not can_answer(
                tokens_used=_tokens_used,
                effective_limit=_effective_limit,
                bypass=bool(getattr(bot_cfg, "bypass_token_check", False)),
            ):
                # Walk the 7-tier resolver so quota refusal text matches
                # the rest of the pipeline (owner column → language pack
                # → constants), not just tier 1 of the chain.
                try:
                    _quota_resolver = container.oos_template_resolver()
                    _refusal_msg = await _quota_resolver.resolve(
                        bot=bot_cfg,
                        language=getattr(bot_cfg, "language", None),
                        bot_name_substitution=getattr(bot_cfg, "bot_name", "") or "",
                    )
                except Exception:  # noqa: BLE001 — fail-soft refusal text
                    _refusal_msg = getattr(bot_cfg, "oos_answer_template", None) or ""
                logger.warning(
                    "quota_exhausted_pre_call",
                    record_bot_id=str(bot_cfg.id),
                    tokens_used=_tokens_used,
                    effective_limit=_effective_limit,
                )
                try:
                    _notifier = container.webhook_notifier()
                    await _notifier.send_quota_exhausted(
                        record_tenant_id=record_tenant_id,
                        record_bot_id=record_bot_id,
                        bot_name=getattr(bot_cfg, "bot_name", "") or "",
                        tokens_used=_tokens_used,
                        effective_limit=_effective_limit,
                    )
                except Exception:  # noqa: BLE001 — notify is best-effort
                    logger.warning("quota_exhausted_notify_failed", exc_info=True)
                await job_repo.update_status(
                    job_id, record_tenant_id=record_tenant_id,
                    status="failed", error="QUOTA_EXHAUSTED",
                    result={"answer": _refusal_msg, "refusal_reason": "QUOTA_EXHAUSTED"},
                )
                await request_log_repo.finalize_request_log(
                    request_id, record_tenant_id=record_tenant_id,
                    status="failed",
                    error_code="QUOTA_EXHAUSTED",
                    error_message="bot tokens_used >= effective_limit",
                )
                try:
                    request_total.labels(
                        status="quota_exceeded", channel_type=_channel_type,
                    ).inc()
                except Exception:  # noqa: BLE001
                    pass
                return
        except Exception:  # noqa: BLE001 — quota-gate resolve must not crash the pipeline; on failure we fall through and rely on post-call hooks to record usage.
            logger.warning("quota_gate_resolve_failed", exc_info=True)

        # Load recent conversation history (last 3 turns) for multi-turn.
        # P25 Phase C-3: cache the Conversation object here so the assistant-
        # persist block below reuses it instead of issuing a second
        # ``conv_repo.get_by_id`` (saves one DB round-trip per turn). On
        # failure we leave ``conv_for_history = None`` and the assistant block
        # re-fetches as before.
        conversation_history: list[dict] = []
        conv_for_history = None
        # Phase C instrumentation: wrap history fetch + extraction so analyzers
        # can attribute multi-turn cost (DB round-trip + message-list build).
        # Exception is swallowed inside the async-with so the request still
        # proceeds with empty history; the step row is recorded as ``failed``
        # with the error string for triage.
        try:
            async with tracker.step("history_load") as _hist_ctx:
                conv_for_history = await conv_repo.get_by_id(conv_id, record_tenant_id=record_tenant_id)
                if conv_for_history and hasattr(conv_for_history, "messages"):
                    # ``chat_max_history`` is part of the batched ``_cfg`` snapshot
                    # loaded above (see ``_CHAT_CONFIG_KEYS``) — no extra Redis
                    # round-trip. ``DEFAULT_MAX_HISTORY`` is the SSoT default in
                    # ``shared/constants.py`` (operator-tunable per system_config).
                    _hist_limit = _cfg_int(_cfg, "chat_max_history", DEFAULT_MAX_HISTORY) or DEFAULT_MAX_HISTORY
                    recent = conv_for_history.history_for_llm(limit=_hist_limit) if hasattr(conv_for_history, "history_for_llm") else (
                        conv_for_history.messages[-_hist_limit:] if hasattr(conv_for_history.messages, '__getitem__') else []
                    )
                    conversation_history = [
                        {"role": m.role, "content": m.content}
                        for m in recent
                        if m.content
                    ]
                _hist_ctx.set_metadata(
                    n_messages=len(conversation_history),
                    found=conv_for_history is not None,
                )
        except Exception:  # noqa: BLE001 — multi-turn history is best-effort; treat any DB / serialization failure as empty history so the turn still answers, but log + record the step as failed.
            logger.warning("load_history_failed", conv_id=str(conv_id))
            conv_for_history = None
            conversation_history = []

        # Load pipeline config from system_config (Redis-cached DB).
        # Finding #2 perf fix: replace 65 sequential ``await get*()`` calls
        # (≈ 65 round-trips on cold cache, ≈ 65 Redis hits on warm) with a
        # single ``get_many`` round-trip. The defaults inlined below preserve
        # the previous per-key semantics exactly — coercion helpers above
        # match ``SystemConfigService.get_int / get_float / get_bool``.
        # ``_cfg`` was eagerly loaded before tracker construction (Phase-B
        # B4) so the StepTracker can honour ``batch_step_logging_enabled``;
        # reuse the cached snapshot here.
        # All three of these keys are already in ``_CHAT_CONFIG_KEYS`` and
        # therefore present in the batched ``_cfg`` snapshot — pulling them
        # via the helpers avoids 3 extra Redis round-trips per chat turn
        # (mega-sprint-G21 dropped the previous redundant per-key
        # SystemConfigService awaits which overwrote these exact values
        # with no semantic difference).
        # Wave M3.3-A — fallback aligned to canonical DEFAULT_RERANK_TOP_N
        # (was literal 5, which silently regressed Z2 migration 0057's seed
        # value of 7 when system_config row missing).
        pipeline_config = _build_pipeline_config(_cfg, bot_cfg)

        # Warn loudly if the live cache threshold drifted below the recommended
        # floor; a value < 0.95 risks serving borderline matches as exact hits.
        _live_cache_thr = float(pipeline_config.get(
            "cache_similarity_threshold", SEMANTIC_CACHE_THRESHOLD,
        ))
        if _live_cache_thr < SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED:
            logger.warning(
                "semantic_cache_threshold_below_recommended",
                live=_live_cache_thr,
                min_recommended=SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED,
                canonical=SEMANTIC_CACHE_THRESHOLD,
                hint="cache may serve borderline matches; set system_config.pipeline_cache_similarity_threshold ≥ 0.95",
            )

        # GraphRAG — knowledge graph service (optional, only if enabled);
        # shared resolver so every transport honours the same gate.
        _kg_service: Any = resolve_kg_service(pipeline_config)

        # Canonical DI assembly (ADR-W1-DI) — one shared builder for every
        # transport; required deps fail loudly as GraphAssemblyError.
        graph = await get_graph(**build_graph_di_kwargs(container))

        _session_factory_for_request = (
            container.session_factory()
            if (_kg_service or pipeline_config.get("parent_child_enabled"))
            else None
        )

        # Resolve OOS / refuse template via the 7-tier chain
        # (bot.col -> plan_limits -> workspace_config -> tenants ->
        # system_config -> language_packs -> constants). Resolver is
        # optional in legacy bootstrap configurations; fall back to the
        # owner column when missing so the worker keeps shipping.
        try:
            _oos_resolver = container.oos_template_resolver()
        except Exception:  # noqa: BLE001 — optional DI; tolerate older bootstrap
            _oos_resolver = None
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
        try:
            _sysprompt_assembler = container.sysprompt_assembler()
        except Exception:  # noqa: BLE001 — optional DI; tolerate older bootstrap
            _sysprompt_assembler = None
        _assembled_sysprompt = (
            await _sysprompt_assembler.assemble(bot=bot_cfg, language=_bot_language)
            if _sysprompt_assembler is not None
            else (bot_cfg.system_prompt or "")
        )

        initial_state: GraphState = build_chat_initial_state(
            record_tenant_id=record_tenant_id,
            request_id=request_id,
            message_id=external_message_id,
            conversation_id=conv_id,
            record_bot_id=record_bot_id,
            bot_cfg=bot_cfg,
            channel_type=channel_type_str,
            workspace_id=workspace_slug,
            user_groups=valid.user_groups,
            query=question_text,
            conversation_history=conversation_history,
            pipeline_config=pipeline_config,
            tracker=tracker,
            assembled_sysprompt=_assembled_sysprompt,
            oos_template_resolved=_oos_template_resolved,
            bot_language=_bot_language,
            kg_service=_kg_service,
            session_factory=_session_factory_for_request,
        )

        _pipeline_timeout_s = _cfg_int(_cfg, "pipeline_timeout_s", 60)
        _callback_max_retries = _cfg_int(_cfg, "callback_max_retries", DEFAULT_CALLBACK_MAX_RETRIES)
        _callback_timeout_s = _cfg_int(_cfg, "callback_timeout_s", DEFAULT_CALLBACK_TIMEOUT_S)
        _callback_verify_ssl = _cfg_get(_cfg, "callback_verify_ssl", True)
        _callback_hmac_secret = _cfg_get(_cfg, "callback_hmac_secret", "") or ""
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(initial_state, config={"recursion_limit": pipeline_config["graph_recursion_limit"]}),
                timeout=_pipeline_timeout_s,
            )
        except asyncio.TimeoutError:
            failure = "PIPELINE_TIMEOUT"
            logger.error("pipeline_timeout", job_id=str(job_id), timeout_s=_pipeline_timeout_s)

        if not failure:
            answer_text = final_state.get("answer", "") or ""
            chosen_model = final_state.get("model_used") or chosen_model
            tokens = final_state.get("tokens") or {}
            prompt_tokens = int(tokens.get("prompt", 0))
            completion_tokens = int(tokens.get("completion", 0))
            cost_usd = float(final_state.get("cost_usd", 0.0))
            citations = list(final_state.get("citations") or [])

    except Exception as exc:  # noqa: BLE001
        failure = str(exc)
        logger.exception("chat_pipeline_failed")
        # Surface the unrecoverable failure to the configured webhook
        # channel. Fire-and-forget — the alert path must never block
        # the persist + callback flow. The hook itself catches all
        # scheduling errors so a webhook outage cannot cascade.
        try:
            _hook = container.error_notify_hook()
            await _hook.on_ai_error(
                error=exc,
                component="chat.pipeline",
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                request_id=request_id,
            )
        except Exception:  # noqa: BLE001 — alert path must not break pipeline persistence
            logger.warning("chat_error_notify_hook_failed", exc_info=True)

    # Phase-B B4 — flush buffered step rows once the pipeline is done
    # (success, timeout, or exception). Idempotent + best-effort:
    # ``StepTracker.flush()`` swallows + logs repo-side failures
    # internally, so no outer try/except is required here. No-op when
    # batch mode is OFF (returns 0 immediately).
    await tracker.flush()

    await _persist_and_callback(
        payload=payload,
        container=container,
        record_tenant_id=record_tenant_id,
        conv_for_history=conv_for_history,
        conv_repo=conv_repo,
        conv_id=conv_id,
        job_repo=job_repo,
        job_id=job_id,
        request_log_repo=request_log_repo,
        request_id=request_id,
        clock=clock,
        bot_cfg=bot_cfg,
        bot_id_str=bot_id_str,
        record_bot_id=record_bot_id,
        workspace_slug=workspace_slug,
        trace_id=trace_id,
        user_id=user_id,
        answer_text=answer_text,
        chosen_model=chosen_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        citations=citations,
        routing_reason=routing_reason,
        failure=failure,
        final_state=final_state,
        _channel_type=_channel_type,
        _req_t0=_req_t0,
        _callback_max_retries=_callback_max_retries,
        _callback_timeout_s=_callback_timeout_s,
        _callback_verify_ssl=_callback_verify_ssl,
        _callback_hmac_secret=_callback_hmac_secret,
    )


async def main() -> None:
    """Khởi chạy chat worker — subscribe chat.received.v1.

    P25 Phase B: per-process concurrency cap via ``asyncio.Semaphore``.
    Each chat_worker process accepts at most
    ``DEFAULT_CHAT_WORKER_CONCURRENCY`` (overridable via system_config key
    ``chat_worker_concurrency``) overlapping pipeline runs. Horizontal scale
    (docker-compose ``scale=N``) multiplies this — N processes × concurrency.
    Updates the ``chat_worker_queue_depth`` Gauge on each enter/exit so an
    operator sees backpressure live.
    """
    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()

    bus = container.bus()
    await bus.ensure_streams()

    # Resolve concurrency from system_config (DB) with constants.py fallback.
    concurrency = DEFAULT_CHAT_WORKER_CONCURRENCY
    try:
        cfg_svc = SystemConfigService(
            session_factory=container.session_factory(),
            redis_client=container.redis_client(),
        )
        concurrency = await cfg_svc.get_int(
            "chat_worker_concurrency", DEFAULT_CHAT_WORKER_CONCURRENCY,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "chat_worker_concurrency_config_load_failed",
            fallback=DEFAULT_CHAT_WORKER_CONCURRENCY,
        )
    if concurrency < 1:
        concurrency = DEFAULT_CHAT_WORKER_CONCURRENCY
    semaphore = asyncio.Semaphore(concurrency)
    logger.info("chat_worker_concurrency_resolved", concurrency=concurrency)

    stop = asyncio.Event()

    async def _handler(event: Any) -> None:  # noqa: ANN401
        # Z2-P0-2 fix: do NOT swallow exceptions here. If handler raises after
        # job status="running" but before status="failed"/"success" is written,
        # swallowing would let the bus XACK and the job stays "running" forever.
        # Re-raise so the bus skips XACK; recover_pending_messages will XCLAIM
        # and retry (up to 5 deliveries before dead-letter).
        async with semaphore:
            try:
                chat_worker_queue_depth.inc()
            except Exception:  # noqa: BLE001
                pass
            try:
                await handle_chat_received(event.payload, container)
            finally:
                try:
                    chat_worker_queue_depth.dec()
                except Exception:  # noqa: BLE001
                    pass

    sub = await bus.subscribe(
        SUBJECT_CHAT_RECEIVED,
        _handler,
        durable_name="chat-worker",
        queue_group="chat",
    )

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    logger.info("chat_worker_started")
    await stop.wait()

    await sub.unsubscribe()
    await bus.close()
    logger.info("chat_worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
