"""Async chat endpoints — POST returns ``job_id`` + GET polls status.

Architecture::

    Client ──POST /test/chat-async──► HTTP route (this module)
                                        │
                                        │ XADD chat.requested
                                        ▼
                                      Redis Stream
                                        │
                                        │ XREADGROUP
                                        ▼
                              chat_async_worker.py
                                        │
                                        │ HSET chat:result:{job_id}
                                        ▼
                                       Redis Hash
                                        ▲
                                        │ HGETALL
    Client ──GET /test/chat-async/{id}──┘  (this module)

Decouples LLM latency from the HTTP request so a single uvicorn worker can
accept ~50–100 RPS instead of holding the connection open for the full
LangGraph pipeline (~3–8 s per turn). The POST returns immediately with a
``job_id`` the caller can poll until the worker writes the final result.

Identity + RBAC follows the same 4-key contract as the sync ``/test/chat``
path: ``record_tenant_id`` lifted from JWT bearer (middleware), body carries
``(bot_id, channel_type)`` REQUIRED + ``workspace_id`` OPTIONAL.

Token-quota gate mirrors the synchronous ``/test/chat`` path — exhausted
bots are refused at submission time so the worker never wakes for a job
that would be rejected anyway. Refusal text comes from
``bots.oos_answer_template`` (DB-driven, no app-injected literal per
CLAUDE.md MINDSET rule #2).

Scope intentionally narrow:
  * NEVER touches model / API key / provider config.
  * NEVER modifies the existing synchronous ``/test/chat`` endpoint.
  * NEVER manages worker lifecycle (owned by ``chat_async_worker.py``).

The Stream name + result-hash prefix + TTL come from
``shared/constants.py`` (single source of truth shared with the worker).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ragbot.shared.callback_validator import _is_url_safe
from ragbot.shared.constants import (
    CHAT_REQUEST_STREAM,
    CHAT_RESULT_HASH_PREFIX,
    DEFAULT_MAX_TOKENS_TOTAL,
    MAX_BOT_ID_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["test-async"])

_PLATFORM_TENANT_FALLBACK_UUID = uuid.UUID(int=1)


def _container(request: Request) -> Any:
    """DI container off ``app.state`` — mirrors ``test_chat._container``."""
    return request.app.state.container


def _tenant_uuid(request: Request) -> uuid.UUID:
    """Lift ``record_tenant_id`` UUID set by ``TenantContextMiddleware``.

    Falls back to ``_PLATFORM_TENANT_FALLBACK_UUID`` so demo callers
    without a tenant claim still resolve a row (parity with
    ``test_chat.test_chat`` so the async path matches the sync path's
    accepted-input surface; production ``/api/ragbot/chat`` enforces the
    claim strictly via 403).
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if isinstance(raw, uuid.UUID):
        return raw
    if raw is None:
        return _PLATFORM_TENANT_FALLBACK_UUID
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return _PLATFORM_TENANT_FALLBACK_UUID


class TestChatAsyncRequest(BaseModel):
    """Request body — same shape as ``TestChatRequest`` minus debug toggles.

    Kept as a thin schema rather than reusing ``TestChatRequest`` so the
    sync + async paths can evolve independently without coupling: e.g.
    the async path may later add ``priority`` / ``deadline_ms`` fields
    that have no meaning for the sync path.
    """

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Workspace slug; resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )
    question: str = Field(min_length=1, max_length=4000)
    callback_url: str | None = Field(
        default=None,
        description=(
            "Optional HTTPS/HTTP URL to POST the result when the pipeline "
            "completes. When set the worker POSTs "
            "{job_id, answer, citations, status} to this URL and marks the "
            "job as 'delivered'. Validated for SSRF safety (private IPs / "
            "internal ports rejected). Overrides the bot-level callback_url "
            "for this request only."
        ),
    )


@router.post("/chat-async")
async def submit_chat_async(
    req: TestChatAsyncRequest, request: Request,
) -> dict[str, Any]:
    """Enqueue a chat request → return ``job_id`` immediately.

    Pipeline:
      1. Resolve 4-key bot identity (404 on missing bot).
      2. Pre-call token quota gate — refuse early if exhausted (parity
         with sync ``/test/chat`` — see test_chat.py:1944-2000).
      3. ``XADD`` job to ``CHAT_REQUEST_STREAM`` for the worker to consume.
      4. Return ``{job_id, status: "pending", trace_id}``.

    The worker (sibling Coder-D1, G25) consumes the Stream and writes
    the final result to ``CHAT_RESULT_HASH_PREFIX{job_id}`` with TTL
    ``DEFAULT_CHAT_RESULT_TTL_S``. The GET endpoint below polls that hash.
    """
    container = _container(request)
    record_tenant_uuid = _tenant_uuid(request)
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )

    # ── Resolve 4-key bot identity ─────────────────────────────────────────
    bot_cfg = await container.bot_repo().find_by_4key(
        record_tenant_uuid, workspace_slug, req.bot_id, req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Bot {req.bot_id}:{req.channel_type} not found",
        )

    # ── PRE-CALL QUOTA GATE — mirror /test/chat behaviour ──────────────────
    # Refuse before XADD so the worker never wakes for a job we would have
    # rejected anyway. Refusal text comes from the bot's DB column, not an
    # app-injected literal (CLAUDE.md MINDSET rule #2).
    try:
        from ragbot.shared.token_budget import (  # noqa: PLC0415
            can_answer, compute_effective_max_tokens,
        )

        cfg_svc = container.system_config_service()
        system_max = int(
            await cfg_svc.get_int(
                "max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL,
            ),
        )
        effective_limit = compute_effective_max_tokens(
            system_max_tokens=system_max,
            bot_extra_max_tokens=int(bot_cfg.extra_max_tokens),
        )
        redis_quota = container.redis_client()
        l1_key = f"ragbot:bot:tokens_used:{bot_cfg.id}"
        l1_value = await redis_quota.get(l1_key)
        tokens_used = (
            int(l1_value) if l1_value is not None else int(bot_cfg.tokens_used)
        )
        if not can_answer(
            tokens_used=tokens_used,
            effective_limit=effective_limit,
            bypass=bool(bot_cfg.bypass_token_check),
        ):
            # Walk the 7-tier resolver so quota refusal text matches
            # the rest of the pipeline (owner override → language pack
            # → constants), not just tier 1 of the chain.
            try:
                _oos_resolver = container.oos_template_resolver()
                _quota_refusal = await _oos_resolver.resolve(
                    bot=bot_cfg,
                    language=getattr(bot_cfg, "language", None),
                    bot_name_substitution=getattr(bot_cfg, "bot_name", "") or "",
                )
            except Exception:  # noqa: BLE001 — fail-soft: empty refusal preferred over crash
                _quota_refusal = getattr(bot_cfg, "oos_answer_template", None) or ""
            return {
                "ok": False,
                "blocked": True,
                "blocked_reason": "QUOTA_EXHAUSTED",
                "answer": _quota_refusal,
                "refusal_reason": "QUOTA_EXHAUSTED",
                "tokens_used_this_period": tokens_used,
                "effective_limit": effective_limit,
            }
    except HTTPException:
        raise
    except (SQLAlchemyError, RedisError, ValueError, TypeError) as quota_exc:
        # Fail-soft quota gate: a transient DB/Redis probe failure must not
        # block chat (parity with the sync ``/test/chat`` path). But fail-open
        # leaks paid quota, so emit a distinct, deliberately-named event the
        # observability layer can alert on separately from generic warnings —
        # a sustained rate of these means quota enforcement is silently off.
        logger.warning(
            "quota_gate_bypassed",
            path="chat_async",
            error=str(quota_exc)[:200],
            error_type=type(quota_exc).__name__,
        )

    # ── Validate callback_url SSRF safety before enqueue ──────────────────
    # Reject private/internal IPs at submission time so the worker never
    # fires a request into the internal network. Domain-neutral check:
    # ``_is_url_safe`` resolves DNS and tests against blocked IP ranges.
    if req.callback_url is not None:
        url_safe, url_reason = await _is_url_safe(req.callback_url)
        if not url_safe:
            raise HTTPException(
                status_code=422,
                detail=f"callback_url rejected: {url_reason}",
            )

    # ── XADD job to Stream for the worker ──────────────────────────────────
    job_id = str(uuid.uuid4())
    trace_id = getattr(request.state, "trace_id", None) or f"async-{job_id}"
    redis = container.redis_client()
    payload = {
        "bot_id": req.bot_id,
        "channel_type": req.channel_type,
        "workspace_id": workspace_slug,
        "record_tenant_id": str(record_tenant_uuid),
        "record_bot_id": str(bot_cfg.id),
        "question": req.question,
        "trace_id": trace_id,
        "submitted_at_ms": int(time.time() * 1000),
    }
    if req.callback_url is not None:
        payload["callback_url"] = req.callback_url
    try:
        await redis.xadd(
            CHAT_REQUEST_STREAM,
            {"job_id": job_id, "req": json.dumps(payload, default=str)},
        )
    except Exception as enqueue_exc:  # noqa: BLE001 — request entrypoint; must catch all so client gets a 503 envelope rather than an internal traceback. exc_info=True preserves stack.
        logger.error(
            "chat_async_enqueue_failed",
            error=str(enqueue_exc)[:300],
            error_type=type(enqueue_exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="chat queue unavailable; retry shortly",
        ) from enqueue_exc

    return {
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "trace_id": trace_id,
    }


def _decode(value: Any) -> str:
    """Best-effort bytes → str decode; identity for already-decoded values."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


@router.get("/chat-async/{job_id}")
async def poll_chat_async(job_id: str, request: Request) -> dict[str, Any]:
    """Poll ``CHAT_RESULT_HASH_PREFIX{job_id}`` for worker output.

    States:
      * empty hash      → ``{"status": "pending"}`` (job in queue / running)
      * status=done     → full result envelope with answer + citations
      * status=error    → error envelope (worker raised; truncated message)

    The hash is written by ``chat_async_worker.py`` (sibling Coder-D1,
    G25) with TTL ``DEFAULT_CHAT_RESULT_TTL_S`` so abandoned-but-completed
    jobs eventually GC themselves out of Redis.
    """
    redis = _container(request).redis_client()
    state = await redis.hgetall(f"{CHAT_RESULT_HASH_PREFIX}{job_id}")
    if not state:
        return {"job_id": job_id, "status": "pending"}

    # Normalise keys → str (Redis returns ``bytes`` by default for hgetall
    # but a fake / decoded client may return ``str``).
    decoded: dict[str, str] = {}
    for raw_key, raw_val in state.items():
        decoded[_decode(raw_key)] = _decode(raw_val)

    status = decoded.get("status", "pending")
    if status == "error":
        return {
            "job_id": job_id,
            "status": "error",
            "error": decoded.get("error", ""),
        }

    citations_raw = decoded.get("citations", "[]")
    try:
        citations = json.loads(citations_raw) if citations_raw else []
    except (ValueError, TypeError):
        citations = []

    duration_raw = decoded.get("duration_ms", "0")
    try:
        duration_ms = int(duration_raw) if duration_raw else 0
    except (ValueError, TypeError):
        duration_ms = 0

    chunks_raw = decoded.get("chunks_used", "0")
    try:
        chunks_used = int(chunks_raw) if chunks_raw else 0
    except (ValueError, TypeError):
        chunks_used = 0

    return {
        "ok": True,
        "job_id": job_id,
        "status": status,
        "answer": decoded.get("answer", ""),
        "citations": citations,
        "chunks_used": chunks_used,
        "duration_ms": duration_ms,
    }


__all__ = [
    "TestChatAsyncRequest",
    "poll_chat_async",
    "router",
    "submit_chat_async",
]
