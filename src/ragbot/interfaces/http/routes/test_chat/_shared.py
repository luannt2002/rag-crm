"""Shared helpers + module-level singletons for the test_chat route package.

Carved verbatim from the original ``test_chat.py`` god-module (behavior-
preserving relocation, no logic change). Every route sub-module imports the
helpers it needs from here; ``__init__`` re-exports them so external importers
(``chat_stream.py`` late-import, integration tests) keep working unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.application.services import google_link_service  # noqa: F401 — re-export parity
from ragbot.application.services.document_service import DocumentService
from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.application.services.retry_policy import CircuitBreaker, CircuitBreakerPolicy
from ragbot.shared.bot_limits import resolve_bot_limit  # noqa: F401 — re-export parity
from ragbot.shared.rbac import check_min_level
from ragbot.shared.constants import (
    ROLLING_SUMMARY_KEEP_LAST,  # noqa: F401 — re-export parity
    ROLLING_SUMMARY_THRESHOLD,  # noqa: F401 — re-export parity
)
from ragbot.shared.pagination import page_limit
from ragbot.shared.workspace_id_validator import resolve_workspace_id  # noqa: F401 — re-export parity

# Pipeline-config SSoT lives in a sibling module to keep this file focused;
# re-exported below so external importers see them on the package namespace.
from ragbot.interfaces.http.routes.test_chat._pipeline_config import (
    _PIPELINE_CFG_KEYS,
    _build_pipeline_config,
    _coerce_bool,
    _coerce_float,
    _coerce_int,
    _parse_intent_list,
)

logger = structlog.get_logger("ragbot.interfaces.http.routes.test_chat")

_PLATFORM_TENANT_FALLBACK_UUID = uuid.UUID(int=1)

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent / "static"

# Circuit breaker cho LLM calls — sau 5 lỗi liên tiếp sẽ chặn request 30s
# để tránh gửi thêm request tới LLM provider đang lỗi.
_llm_circuit = CircuitBreaker(name="litellm", policy=CircuitBreakerPolicy())


def _container(request: Request):
    """Lấy DI container từ app state.
    @param request: FastAPI Request
    @return: Container instance
    """
    return request.app.state.container


def _sf(request: Request):
    """Lấy session factory từ container.
    @param request: FastAPI Request
    @return: AsyncSessionFactory
    """
    return _container(request).session_factory()


async def _find_bot_uuid(
    request: Request,
    bot_id: str,
    channel_type: str,
    *,
    record_tenant_id: uuid.UUID | None = None,
    workspace_id: str | None = None,
) -> uuid.UUID:
    """Tìm bot UUID theo 4-key identity ``(record_tenant_id, workspace_id, bot_id, channel_type)``.

    The bot repo expects the UUID FK; if the caller didn't pass one
    explicitly we lift ``request.state.record_tenant_id`` (set by
    ``TenantContextMiddleware`` from the JWT bearer claim). Missing
    tenant context → 422.

    When an explicit ``workspace_id`` is supplied it resolves via the canonical
    4-key lookup. When it is omitted (e.g. the demo UI lists a bot by its
    globally-unique slug), it resolves by ``(record_tenant_id, bot_id,
    channel_type)`` and accepts the match ONLY if it is unambiguous — if two
    workspaces share the slug the caller is told to pass ``workspace_id``. This
    keeps the 4-key contract intact at the chat/write boundary while letting
    read-path callers address a bot without knowing its workspace slug.
    """
    if record_tenant_id is None:
        record_tenant_id = getattr(request.state, "record_tenant_id", None)
    if record_tenant_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"identity violation: record_tenant_id missing for "
                f"{bot_id}:{channel_type}. JWT must carry record_tenant_id."
            ),
        )
    repo = _container(request).bot_repo()
    if workspace_id is not None and str(workspace_id).strip() != "":
        workspace_slug = resolve_workspace_id(
            workspace_id, record_tenant_id=record_tenant_id,
        )
        cfg = await repo.find_by_4key(
            record_tenant_id, workspace_slug, bot_id, channel_type,
        )
    else:
        # No workspace slug given — resolve by the unique (tenant, bot_id,
        # channel) match. Returns None when ambiguous (same slug in 2
        # workspaces) so we 404 asking the caller to disambiguate rather
        # than silently picking one.
        cfg = await repo.find_by_3key_unique(
            record_tenant_id, bot_id, channel_type,
        )
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id}:{channel_type} not found")
    return cfg.id


_page_limit = page_limit  # shared alias


def _tenant_scope(request: Request) -> uuid.UUID | None:
    """``record_tenant_id`` UUID for repo row-scoping. ``None`` = platform admin bypass.

    Lifts ``request.state.record_tenant_id`` set by ``TenantContextMiddleware``
    from the JWT bearer claim. Platform admin tokens (level 100) without an
    explicit tenant claim get None so they can list across tenants.
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if isinstance(raw, uuid.UUID):
        # Sentinel system UUID = unscoped admin token; treat as bypass.
        if raw.int == 0:
            return None
        return raw
    if raw is None:
        if check_min_level(request, 100):
            return None
        return None
    try:
        parsed = uuid.UUID(str(raw))
        if parsed.int == 0:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _sys_config(request: Request) -> SystemConfigService:
    """Lấy SystemConfigService từ container.
    @param request: FastAPI Request
    @return: SystemConfigService instance
    """
    c = _container(request)
    return SystemConfigService(
        session_factory=c.session_factory(),
        redis_client=c.redis_client(),
    )


def _caller_tenant_uuid(request: Request) -> uuid.UUID | None:
    """Lift ``record_tenant_id`` UUID from request.state for audit rows.

    Used by every ``write_audit(AuditEntry(...))`` call in this module.
    Returns ``None`` when the caller is a platform-level service token
    with no tenant binding — ``audit_log.record_tenant_id`` is nullable
    for that case.
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _resolve_body_tenant_int(
    request: Request, body_tenant_int: int,
) -> uuid.UUID:
    """Translate legacy body INT ``tenant_id`` → ``record_tenant_id`` UUID FK.

    Demo client still passes upstream INT; resolver maps via
    ``tenants.config->>'upstream_tenant_id'`` to the UUID stored in the
    ``bots.record_tenant_id`` FK column. 404 when upstream INT is
    unregistered so misconfigured demo clients fail fast.
    """
    sf = _container(request).session_factory()
    async with sf() as session:
        row = await session.execute(
            text(
                "SELECT id FROM tenants "
                "WHERE (config->>'upstream_tenant_id')::int = :tid LIMIT 1"
            ),
            {"tid": int(body_tenant_int)},
        )
        scalar = row.scalar()
    if scalar is None:
        raise HTTPException(
            status_code=404,
            detail=f"upstream tenant_id={body_tenant_int} not registered",
        )
    return uuid.UUID(str(scalar))


def _audit_entry(
    request: Request,
    *,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID,
    before: dict | None,
    after: dict | None,
    record_bot_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> AuditEntry:
    """Build a forensic ``AuditEntry`` from the FastAPI request context.

    Centralises the actor / trace / tenant extraction so every admin
    route in this module emits a structurally identical row.
    ``resource_id`` accepts both ``str`` (system_config keys, service
    names) and ``uuid.UUID`` (bots, tokens) — the dataclass field is
    typed UUID but the repository ``str()``-coerces before insert.
    """
    return AuditEntry(
        record_tenant_id=_caller_tenant_uuid(request),
        record_bot_id=record_bot_id,
        actor_user_id=getattr(request.state, "user_id", None) or "unknown",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,  # type: ignore[arg-type]
        before=before,
        after=after,
        reason=reason,
        trace_id=getattr(request.state, "trace_id", "n/a"),
    )


def _apply_rolling_summary(
    history: list[dict[str, str]],
    *,
    threshold: int,
    keep_last: int,
) -> list[dict[str, str]]:
    """Compress `history` when it exceeds `threshold` turns.

    Pure function — no LLM call (cheap summary is enough to keep LLM
    anchored without per-request cost). Keeps the last `keep_last`
    messages verbatim and folds the older ones into a single synthetic
    system message: "Hội thoại trước (N tin): <first 80 chars>; ...".

    This is a cheap heuristic compression. The high-quality variant
    (actual LLM summarisation) is feasible later — this at least stops
    the token count from growing unboundedly and gives the LLM a
    coarse anchor on earlier context.
    """
    if not history or threshold <= 0 or len(history) <= threshold:
        return history
    # Take tail verbatim; fold head into summary
    head = history[:-keep_last] if keep_last > 0 else history
    tail = history[-keep_last:] if keep_last > 0 else []
    fragments = []
    for m in head:
        role = m.get("role", "?")
        preview = (m.get("content") or "").strip().replace("\n", " ")[:80]
        fragments.append(f"[{role}] {preview}")
    summary_text = (
        f"Tóm tắt hội thoại trước ({len(head)} tin nhắn): "
        + " ⬌ ".join(fragments)
    )[:1200]  # safety cap
    return [{"role": "system", "content": summary_text}, *tail]


async def _resolve_action_conversation_id(
    conv_repo: Any,
    bot_cfg: Any,
    *,
    connect_id: str,
    tenant_id: Any,
    workspace_slug: str,
) -> Any:
    """Resolve a persistent conversation_id for conversational-action bots.

    Returns the existing/new ``conversations.id`` (get-or-create keyed by
    ``connect_id``) when the bot opted into ``action_config.enabled`` and a
    repository is wired; otherwise ``None``. Multi-turn slot/lead-capture
    state is keyed by this id, and the JSONB backend no-ops on ``None`` — so
    factoid-only bots intentionally return ``None`` (no conversation-row churn
    on single-turn load tests). Mirrors production, which gets the id from the
    upstream chat payload. Graceful-degrades to ``None`` on repo error.
    """
    action_on = bool((getattr(bot_cfg, "action_config", {}) or {}).get("enabled"))
    if not action_on or conv_repo is None:
        return None
    try:
        from ragbot.shared.types import BotId, TenantId, UserId, WorkspaceId
        conv = await conv_repo.get_or_create(
            BotId(bot_cfg.id), UserId(connect_id),
            record_tenant_id=TenantId(tenant_id),
            workspace_id=WorkspaceId(workspace_slug),
        )
        return conv.id
    except (SQLAlchemyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning(
            "test_chat_conversation_resolve_failed",
            error=str(exc), error_type=type(exc).__name__,
        )
        return None


def _doc_service(request: Request):
    """Lấy DocumentService instance.
    @param request: FastAPI Request
    @return: DocumentService instance
    """
    c = _container(request)
    # pipeline_audit_logger is optional; the container provides
    # a singleton but if absent (older containers in tests) fall back to None.
    _audit = None
    if hasattr(c, "pipeline_audit_logger"):
        try:
            _audit = c.pipeline_audit_logger()
        except Exception:  # noqa: BLE001
            _audit = None
    # Best-effort wire PII redactor + bot_repo for the boundary fix (Master
    # Finding #4). Older test containers may lack one; fall back to None
    # (passthrough) so existing tests keep running.
    _pii = None
    if hasattr(c, "pii"):
        try:
            _pii = c.pii()
        except Exception:  # noqa: BLE001 — best-effort hook; missing container provider must not break legacy tests
            _pii = None
    _bot_repo = None
    if hasattr(c, "bot_repo"):
        try:
            _bot_repo = c.bot_repo()
        except Exception:  # noqa: BLE001 — best-effort hook; missing container provider must not break legacy tests
            _bot_repo = None
    _stats_repo = None
    if hasattr(c, "stats_index_repo"):
        try:
            _stats_repo = c.stats_index_repo()
        except Exception:  # noqa: BLE001 — best-effort hook; missing container provider must not break legacy tests
            _stats_repo = None
    return DocumentService(
        session_factory=c.session_factory(),
        embedder=c.embedder(),
        settings=request.app.state.settings,
        config_service=_sys_config(request),
        audit_logger=_audit,
        model_resolver=c.model_resolver(),
        pii_redactor=_pii,
        bot_repo=_bot_repo,
        stats_index_repo=_stats_repo,
    )


async def _token_service(request: Request):
    """Lấy JwtTokenService instance với defaults từ system_config.
    @param request: FastAPI Request
    @return: JwtTokenService
    """
    from ragbot.application.services.jwt_token_service import JwtTokenService
    c = _container(request)
    settings = request.app.state.settings
    cfg_svc = _sys_config(request)
    rl_value = await cfg_svc.get_int("rate_limit_default_value", 120)
    rl_window = await cfg_svc.get_int("rate_limit_default_window", 60)
    return JwtTokenService(
        session_factory=c.session_factory(),
        jwt_secret=settings.app.api_token,
        default_rate_limit_value=rl_value,
        default_rate_limit_window=rl_window,
    )


def _require_owner(request: Request) -> None:
    """Kiểm tra role=owner — chỉ cho phép BE chính truy cập admin routes.
    @raises: HTTPException 403 nếu không phải owner
    """
    if not check_min_level(request, 100):
        raise HTTPException(status_code=403, detail="Platform admin (level 100) required")


__all__ = [
    "logger",
    "_PLATFORM_TENANT_FALLBACK_UUID",
    "_STATIC_DIR",
    "_llm_circuit",
    "_container",
    "_sf",
    "_find_bot_uuid",
    "_page_limit",
    "_tenant_scope",
    "_sys_config",
    "_caller_tenant_uuid",
    "_resolve_body_tenant_int",
    "_audit_entry",
    "_apply_rolling_summary",
    "_PIPELINE_CFG_KEYS",
    "_coerce_int",
    "_coerce_float",
    "_coerce_bool",
    "_parse_intent_list",
    "_resolve_action_conversation_id",
    "_build_pipeline_config",
    "_doc_service",
    "_token_service",
    "_require_owner",
]
