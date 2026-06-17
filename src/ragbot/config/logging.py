"""Structured logging setup (structlog + contextvars).

Ref: docs/application/PLAN_01_WORKSPACE_BOOTSTRAP.md §logging.py
     PLAN_14 §Observability.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any
from uuid import UUID  # noqa: F401 — used in type hints (string-quoted to avoid cycle)

import structlog
from structlog.types import EventDict, Processor

# Context variables propagated via structlog.contextvars
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
# SECURITY: ``UNSET`` is the loud sentinel — see ``engine.session_with_tenant``
# and ``uow.SqlAlchemyUnitOfWork.__aenter__``. Any DB session opened while
# this var still equals ``UNSET`` will raise ``RuntimeError`` so a forgotten
# ``bind_request_context()`` cannot quietly bypass row-level-security
# (worker writes used to drop the SET LOCAL silently when ``""``).
tenant_id_ctx: ContextVar[str] = ContextVar("tenant_id", default="UNSET")
# P33 — INT tenant id (upstream NestJS claim). Distinct from tenant_id_ctx
# which carries the internal UUID PK. The token meter + rate limiter both
# key on the int id (matches the JWT sub-claim and bots.tenant_id column).
tenant_id_int_ctx: ContextVar[int | None] = ContextVar("tenant_id_int", default=None)
# Workspace slug for the RLS workspace GUC (``SET LOCAL app.workspace_id``).
# Empty default = unbound: the 0141 policy clause COALESCE('')='' then keeps
# tenant-wide visibility, so existing flows are byte-unchanged until a
# request actually binds a workspace.
workspace_id_ctx: ContextVar[str] = ContextVar("workspace_id", default="")
bot_id_ctx: ContextVar[str] = ContextVar("bot_id", default="")
# Internal bot UUID (record_bot_id) — distinct from bot_id_ctx (external slug).
# The token ledger's primary report key. Kept separate so we never parse a slug
# as a UUID (CLAUDE.md naming rule: bot_id = slug, record_bot_id = UUID PK).
record_bot_id_ctx: ContextVar[str] = ContextVar("record_bot_id", default="")
# Token-ledger flow classifier — 'ingest' (document worker) | 'query' (chat).
# Set by the worker/route entrypoint so the LLM router tags each token-spending
# call WITHOUT guessing flow from the model name.
mode_ctx: ContextVar[str] = ContextVar("mode", default="")
conversation_id_ctx: ContextVar[str] = ContextVar("conversation_id", default="")
user_id_ctx: ContextVar[str] = ContextVar("user_id", default="")


def _drop_color_message_key(_: Any, __: str, event_dict: EventDict) -> EventDict:
    event_dict.pop("color_message", None)
    return event_dict


def _inject_trace_id(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Inject trace_id from contextvars into log record if available."""
    tid = trace_id_ctx.get()
    if tid:
        event_dict.setdefault("trace_id", tid)
    return event_dict


def setup_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog + stdlib logging.

    JSON output by default for production. Colorized console in dev.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            },
        ),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        _drop_color_message_key,
        _inject_trace_id,
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level,
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer: Processor
    renderer = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Tame noisy loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_request_context(
    *,
    trace_id: str | None = None,
    record_tenant_id: "UUID | str | None" = None,
    tenant_id: "UUID | str | None" = None,
    tenant_id_int: int | None = None,
    workspace_id: str | None = None,
    bot_id: "UUID | str | None" = None,
    conversation_id: "UUID | str | None" = None,
    user_id: str | None = None,
) -> None:
    """Bind per-request context to structlog + contextvars.

    ``record_tenant_id`` is the internal UUID FK to ``tenants.id`` and
    is the canonical name. ``tenant_id`` kwarg accepted as legacy alias
    and forwarded to the same contextvar. ``workspace_id`` is the resolved
    slug — it feeds the ``app.workspace_id`` RLS GUC binder.
    """
    fields: dict[str, str] = {}
    if trace_id:
        trace_id_ctx.set(trace_id)
        fields["trace_id"] = trace_id
    rt = record_tenant_id if record_tenant_id is not None else tenant_id
    if rt:
        tenant_id_s = str(rt)
        tenant_id_ctx.set(tenant_id_s)
        fields["record_tenant_id"] = tenant_id_s
    if workspace_id:
        workspace_id_ctx.set(str(workspace_id))
        fields["workspace_id"] = str(workspace_id)
    if tenant_id_int is not None:
        tenant_id_int_ctx.set(int(tenant_id_int))
        fields["tenant_id_int"] = str(int(tenant_id_int))
    if bot_id:
        bot_id_s = str(bot_id)
        bot_id_ctx.set(bot_id_s)
        fields["bot_id"] = bot_id_s
    if conversation_id:
        conv_s = str(conversation_id)
        conversation_id_ctx.set(conv_s)
        fields["conversation_id"] = conv_s
    if user_id:
        user_id_ctx.set(user_id)
        fields["user_id"] = user_id

    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """Reset contextvars (call at end of request / task)."""
    tenant_id_int_ctx.set(None)
    # Workers reuse the same coroutine across consumed messages — reset the
    # workspace slug so one message's workspace cannot leak into the next
    # transaction's RLS GUC binding.
    workspace_id_ctx.set("")
    structlog.contextvars.clear_contextvars()


def get_tenant_id() -> str | None:
    """Return internal tenant UUID string (or None if unset / sentinel)."""
    val = tenant_id_ctx.get()
    if not val or val == "UNSET":
        return None
    return val


def get_tenant_id_int() -> int | None:
    """Return external INT tenant id from upstream (or None if unset)."""
    return tenant_id_int_ctx.get()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return bound logger."""
    return structlog.stdlib.get_logger(name)


__all__ = [
    "bind_request_context",
    "bot_id_ctx",
    "record_bot_id_ctx",
    "mode_ctx",
    "clear_request_context",
    "conversation_id_ctx",
    "get_logger",
    "get_tenant_id",
    "get_tenant_id_int",
    "setup_logging",
    "tenant_id_ctx",
    "tenant_id_int_ctx",
    "trace_id_ctx",
    "user_id_ctx",
]
