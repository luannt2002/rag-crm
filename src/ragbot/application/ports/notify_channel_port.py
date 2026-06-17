"""NotifyChannelPort — Protocol for sending operational notifications.

Strategy + DI pattern (CLAUDE.md compliant). Implementations:

* ``WebhookNotifier`` — HTTP webhook to an operator-configured upstream URL.
* ``NullNotifier`` — no-op, default for dev/local when env vars empty.
* Future: SlackNotifier, EmailNotifier, etc.

The port is intentionally narrow: callers pass already-resolved identity
keys + the event payload. The adapter owns throttling, transport, and
failure semantics (soft-fail — chat path never depends on notify ok).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class NotifyChannelPort(Protocol):
    """Send operational notifications about bot events."""

    async def send_quota_exhausted(
        self,
        *,
        record_tenant_id: UUID,
        record_bot_id: UUID,
        bot_name: str,
        tokens_used: int,
        effective_limit: int,
    ) -> bool:
        """Send ``bot_quota_exhausted`` event.

        Returns ``True`` if the event was dispatched, ``False`` if the
        adapter silently dropped it (disabled, throttled, or transport
        failure). Never raises — quota-notify must not break the chat
        path that triggered it.
        """
        ...


__all__ = ["NotifyChannelPort"]
