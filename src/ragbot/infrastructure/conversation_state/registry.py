"""Conversation state provider registry — Strategy + DI sacred-rule.

Provider resolved from ``system_config.conversation_state_provider`` (DB-driven,
Redis-cached). Default ``"null"`` means action-state tracking is OFF —
bots without ``action_config.enabled=true`` see no behavioural change.

Adding a new backend (e.g. Redis hot-cache) = new file under this
package + one registry entry. ``orchestration/query_graph`` + ``bootstrap``
stay untouched (Open-Closed).
"""

from __future__ import annotations

from typing import Any

from ragbot.application.ports.conversation_state_port import ConversationStatePort
from ragbot.infrastructure.conversation_state.jsonb_conversation_state import (
    JsonbConversationState,
)
from ragbot.infrastructure.conversation_state.null_conversation_state import (
    NullConversationState,
)


_REGISTRY: dict[str, type[ConversationStatePort]] = {
    "null": NullConversationState,
    "jsonb": JsonbConversationState,
}


def build_conversation_state(
    provider: str | None = None,
    **kwargs: Any,
) -> ConversationStatePort:
    """Construct conversation-state strategy named ``provider``.

    @param provider: Registry key. ``None`` / empty / unknown degrades
        to ``"null"`` (NullConversationState) — same graceful pattern as
        guardrail / reranker registries in the project.
    @param kwargs: Forwarded to strategy constructor. ``NullConversationState``
        ignores them; ``JsonbConversationState`` accepts ``session_factory=``.

    @return: ConversationStatePort instance ready for DI.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key, NullConversationState)
    return cls(**kwargs)


def available_providers() -> tuple[str, ...]:
    """Return registered provider names (used by /health/models + tests)."""
    return tuple(sorted(_REGISTRY.keys()))


__all__ = ["available_providers", "build_conversation_state"]
