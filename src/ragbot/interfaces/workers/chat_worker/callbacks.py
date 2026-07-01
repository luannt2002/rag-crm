"""Chat-worker persistence + callback delivery tail.

The trailing block of ``_handle_chat_received_body`` — assistant-message
persist, request_log finalize, chat-completed hooks, and webhook callback
delivery — relocated here verbatim during the god-file package split. No
logic change; locals are threaded in as explicit params.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

import structlog

from ragbot.application.events.chat_completed import ChatCompletedEvent
from ragbot.bootstrap import Container
from ragbot.domain.entities.message import Message
from ragbot.domain.events.chat_events import ChatAnswered
from ragbot.infrastructure.delivery import create_delivery
from ragbot.infrastructure.observability.metrics import (
    request_duration_seconds,
    request_total,
)
from ragbot.infrastructure.observability.p99_outlier import record_chat_latency
from ragbot.shared.hashing import content_hash_required
from ragbot.shared.types import Channel, MessageId

logger = structlog.get_logger(__name__)

__all__ = ["_persist_and_callback"]


async def _persist_and_callback(
    *,
    payload: dict[str, Any],
    container: Container,
    record_tenant_id: UUID | None,
    conv_for_history: Any,
    conv_repo: Any,
    conv_id: Any,
    job_repo: Any,
    job_id: Any,
    request_log_repo: Any,
    request_id: Any,
    clock: Any,
    bot_cfg: Any,
    bot_id_str: str,
    record_bot_id: Any,
    workspace_slug: Any,
    trace_id: Any,
    user_id: Any,
    answer_text: str,
    chosen_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    citations: list[dict],
    routing_reason: str | None,
    failure: str | None,
    final_state: dict[str, Any],
    _channel_type: str,
    _req_t0: float,
    _callback_max_retries: int,
    _callback_timeout_s: int,
    _callback_verify_ssl: Any,
    _callback_hmac_secret: str,
) -> None:
    # 2. Persist assistant message.
    # P25 Phase C-3: reuse the conversation already loaded for history (saves
    # one DB round-trip). Re-fetch only if the history-load path failed or
    # returned None.
    conversation = conv_for_history if conv_for_history is not None else (
        await conv_repo.get_by_id(conv_id, record_tenant_id=record_tenant_id)
    )
    if conversation is None:
        logger.error("chat.conversation_missing", conv_id=str(conv_id))
        await job_repo.update_status(
            job_id, record_tenant_id=record_tenant_id, status="failed", error="conversation_missing",
        )
        await request_log_repo.finalize_request_log(
            request_id, record_tenant_id=record_tenant_id,
            status="failed",
            error_code="CONVERSATION_MISSING",
            error_message="conversation not found",
        )
        try:
            _req_dur_s = time.perf_counter() - _req_t0
            request_total.labels(status="failed", channel_type=_channel_type).inc()
            request_duration_seconds.observe(_req_dur_s)
            record_chat_latency(
                duration_s=_req_dur_s,
                intent=final_state.get("intent") if isinstance(final_state, dict) else None,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    msg = Message.new_assistant_message(
        conversation_id=conv_id,
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
        content=answer_text or "(empty)",
        channel=cast(Channel, payload.get("channel", "api")),
        created_at=clock.now(),
        citations=(),
    )
    updated = conversation.add_message(msg)

    uow_factory = container.uow_factory()
    async with uow_factory() as uow:
        await conv_repo.save(
            updated,
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_slug,
        )
        await uow.add_outbox(
            ChatAnswered(
                occurred_at=clock.now(),
                record_tenant_id=record_tenant_id,
                trace_id=trace_id,
                workspace_id=workspace_slug,
                job_id=job_id,
                record_bot_id=record_bot_id,
                user_id=user_id,
                conversation_id=conv_id,
                message_id=MessageId(msg.id),
                answer=answer_text,
                citations=citations,
                tokens_in=prompt_tokens,
                tokens_out=completion_tokens,
                cost_usd=cost_usd,
                latency_ms=0,
                model_name=chosen_model,
                callback_url=payload.get("callback_url"),
            ),
        )
        await uow.commit()

    # 3. Finalize request_log
    # Resolve callback_url: request > bot > tenant > None (poll only)
    callback_url = (
        payload.get("callback_url")
        or getattr(bot_cfg, "callback_url", None)
        or None
    )

    if failure:
        await request_log_repo.finalize_request_log(
            request_id, record_tenant_id=record_tenant_id,
            status="failed",
            error_code="PIPELINE_ERROR",
            error_message=failure,
            answer_hash=None,
            model_name=chosen_model,
            routing_reason=routing_reason,
        )
        await job_repo.update_status(
            job_id, record_tenant_id=record_tenant_id, status="failed", error=failure,
        )
        # Deliver failure callback
        delivery = create_delivery(
            callback_url=callback_url,
            hmac_secret=_callback_hmac_secret,
            max_retries=_callback_max_retries,
            timeout_s=_callback_timeout_s,
            verify_ssl=bool(_callback_verify_ssl),
        )
        delivered = await delivery.deliver({
            "ok": False,
            "job_id": str(job_id),
            "bot_id": bot_id_str,
            "channel_type": _channel_type,
            "connect_id": str(user_id),
            "answer": None,
            "status": "error",
            "message": failure[:500],
        })
        logger.info("answer_delivered", mode=delivery.mode_name, success=delivered)
        if callback_url and not delivered:
            await job_repo.update_status(
                job_id,
                record_tenant_id=record_tenant_id,
                status="delivery_failed",
            )
        try:
            _req_dur_s = time.perf_counter() - _req_t0
            request_total.labels(status="failed", channel_type=_channel_type).inc()
            request_duration_seconds.observe(_req_dur_s)
            record_chat_latency(
                duration_s=_req_dur_s,
                intent=final_state.get("intent") if isinstance(final_state, dict) else None,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # G15: forward final graded chunks as (chunk_id, rank, score) refs --
    # the relational request_chunk_refs child table FK-validates each one.
    _graded_for_refs = (
        final_state.get("graded_chunks") or []
        if isinstance(final_state, dict) else []
    )
    _refs = [
        {
            "chunk_id": c.get("chunk_id") or c.get("id"),
            "rank": idx,
            "score": float(c.get("score", 0) or 0),
        }
        for idx, c in enumerate(_graded_for_refs)
    ]
    # STEP-5 attribution decouple: the stats route answers from a SYNTHETIC
    # chunk (sentinel id → FK-skipped → its retrieval is invisible to
    # CHUNK_RECALL). Attribute it to the REAL source chunks of the matched
    # entities WITHOUT feeding those raw chunks to the LLM — generate's context
    # is left untouched (re-adding the raw table rows is exactly what risks
    # variant-blob price fabrication / HALLU). Entities carry the backfilled
    # ``record_chunk_id``; append any not already referenced as low-rank refs.
    _stats_entities = (
        final_state.get("stats_entities") if isinstance(final_state, dict) else None
    ) or []
    _seen_ref_ids = {r["chunk_id"] for r in _refs if r.get("chunk_id")}
    for _e in _stats_entities:
        _cid = _e.get("record_chunk_id") if isinstance(_e, dict) else None
        if _cid and _cid not in _seen_ref_ids:
            _seen_ref_ids.add(_cid)
            _refs.append({"chunk_id": _cid, "rank": len(_refs), "score": None})
    await request_log_repo.finalize_request_log(
        request_id, record_tenant_id=record_tenant_id,
        answer_hash=content_hash_required(answer_text) if answer_text else None,
        answer_text=answer_text or None,
        model_name=chosen_model,
        routing_reason=routing_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        status="success",
        retrieved_chunks=_refs,
        citations=citations,
    )

    # Fire chat-completed hooks (Open-Closed extension point).
    # Stage 1: in-transaction DB hooks (tokens_used += delta)
    # Stage 2: post-commit hooks (Redis INCR, quota threshold notify)
    try:
        _hook_event = ChatCompletedEvent(
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_slug,
            bot_id=bot_id_str,
            channel_type=_channel_type,
            record_bot_id=bot_cfg.id,
            request_id=request_id,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            tokens_used_delta=int(prompt_tokens or 0) + int(completion_tokens or 0),
            refusal_reason=None,
            intent=(
                final_state.get("intent") if isinstance(final_state, dict) else None
            ),
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        _chat_hooks = container.chat_hook_registry()
        _session_factory = container.session_factory()
        async with _session_factory() as _hook_session:
            await _chat_hooks.fire_db_stage(_hook_event, session=_hook_session)
            await _hook_session.commit()
            await _chat_hooks.fire_post_stage(_hook_event, session=_hook_session)
    except Exception:  # noqa: BLE001 — hook side-effects must NEVER block the callback. Registry-level isolation already catches per-hook failures; this guards bootstrap / session-acquire errors.
        logger.warning("chat_hook_dispatch_failed", exc_info=True)

    await job_repo.update_status(
        job_id, record_tenant_id=record_tenant_id, status="success",
        result={
            "answer": answer_text,
            "message_id": str(msg.id),
            "request_id": str(request_id),
        },
    )

    # Deliver success callback
    delivery = create_delivery(
        callback_url=callback_url,
        hmac_secret=_callback_hmac_secret,
        max_retries=_callback_max_retries,
        timeout_s=_callback_timeout_s,
        verify_ssl=bool(_callback_verify_ssl),
    )
    delivered = await delivery.deliver({
        "ok": True,
        "job_id": str(job_id),
        "bot_id": bot_id_str,
        "channel_type": _channel_type,
        "connect_id": str(user_id),
        "answer": answer_text,
        "answer_type": final_state.get("answer_type", "answered"),
        "answer_reason": final_state.get("answer_reason"),
        "status": "success",
        "message": "Answer delivered",
        "sources": citations,
        "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
        "cost_usd": cost_usd,
        "duration_ms": int((time.perf_counter() - _req_t0) * 1000),
    })
    logger.info("answer_delivered", mode=delivery.mode_name, success=delivered)
    if callback_url:
        if delivered:
            await job_repo.update_status(job_id, record_tenant_id=record_tenant_id, status="delivered")
        else:
            await job_repo.update_status(job_id, record_tenant_id=record_tenant_id, status="delivery_failed")

    try:
        _req_dur_s = time.perf_counter() - _req_t0
        request_total.labels(status="success", channel_type=_channel_type).inc()
        request_duration_seconds.observe(_req_dur_s)
        record_chat_latency(
            duration_s=_req_dur_s,
            intent=final_state.get("intent") if isinstance(final_state, dict) else None,
        )
    except Exception:  # noqa: BLE001
        pass
