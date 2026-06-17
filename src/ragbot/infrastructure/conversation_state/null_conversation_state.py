"""NullConversationState — opt-out provider, all methods are no-ops.

Returned by the registry when ``system_config.conversation_state_provider``
resolves to ``"null"`` (default) or when a bot has not opted in via
``bots.action_config.enabled=true``.

Sacred-rule alignment:
- Strategy + DI: registered as ``"null"`` strategy in registry.py.
- Graceful degradation: load returns ``{}``, save no-ops, drift detect
  returns empty list. No exceptions; existing pipeline runs unchanged.
- Multi-tenant: stateless, no per-tenant config required.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ragbot.application.ports.conversation_state_port import ConversationStatePort
from ragbot.application.ports.guardrail_port import GuardrailHit


class NullConversationState(ConversationStatePort):
    """No-op state backend. Default for bots that have not opted into action tracking."""

    async def load_state(  # noqa: D401
        self,
        *,
        conversation_id: UUID | None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    async def save_state(  # noqa: D401
        self,
        *,
        conversation_id: UUID | None,  # noqa: ARG002
        state: dict[str, Any],  # noqa: ARG002
    ) -> None:
        return

    async def detect_drift(  # noqa: D401
        self,
        *,
        prior_state: dict[str, Any],  # noqa: ARG002
        proposed_answer: str,  # noqa: ARG002
        chunks: list[dict[str, Any]],  # noqa: ARG002
    ) -> list[GuardrailHit]:
        return []


__all__ = ["NullConversationState"]
