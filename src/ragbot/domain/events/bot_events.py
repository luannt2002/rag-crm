"""Bot-related domain events.

Ref: RAGBOT_MASTER §14.2 — bot registry change notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ragbot.domain.events.base import DomainEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class BotRegistryChanged(DomainEvent):
    """Published when a bot row is created / updated / soft-deleted.

    Consumers (other replicas) invalidate their local registry cache on
    receipt so routing does not desync across the fleet.
    """

    event_type: ClassVar[str] = "bot.registry.changed.v1"

    workspace_id: str = ""
    bot_id: str = ""
    channel_type: str = ""
    action: str = "updated"  # created | updated | deleted
    bot_uuid: str = ""


__all__ = ["BotRegistryChanged"]
