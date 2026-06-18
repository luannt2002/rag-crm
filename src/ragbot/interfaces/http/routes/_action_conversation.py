"""Resolve a persistent ``conversation_id`` for conversational-action bots.

Shared by every chat entry-point (SSE stream, sync use-case path, test_chat).
Multi-turn slot / lead-capture state is keyed by the ``conversations.id`` UUID;
the JSONB conversation-state backend no-ops on ``None`` (``load_state`` returns
``{}``, ``save_state`` returns early), so a factoid-only bot intentionally
resolves to ``None`` — no conversation-row churn on single-turn traffic.

Carved out of ``test_chat/_shared.py`` so the production routes can reuse the
exact same get-or-create logic instead of hardcoding ``conversation_id=None``
(the historical SSE booking-slot loss). Behaviour-preserving relocation.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

logger = structlog.get_logger("ragbot.interfaces.http.routes.action_conversation")


async def resolve_action_conversation_id(
    conv_repo: Any,
    bot_cfg: Any,
    *,
    connect_id: str,
    tenant_id: Any,
    workspace_slug: str,
) -> Any:
    """Return the existing/new ``conversations.id`` for an action bot, else ``None``.

    Get-or-create keyed by ``connect_id`` when the bot opted into
    ``action_config.enabled`` and a repository is wired; otherwise ``None``
    (factoid bots skip persistence to avoid conversation-row churn).
    Graceful-degrades to ``None`` on repo error (transport degradation must
    not break the chat call).
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
            "action_conversation_resolve_failed",
            error=str(exc), error_type=type(exc).__name__,
        )
        return None


__all__ = ["resolve_action_conversation_id"]
