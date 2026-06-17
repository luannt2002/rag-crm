"""Default-OFF AuditLoggerPort — no-op so callers never branch on None."""

from __future__ import annotations

from typing import Any


class NullAuditLogger:
    """No-op AuditLoggerPort implementation; safe DI default when audit is disabled."""

    async def log(
        self,
        bot_id: str,
        stage: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        return None


__all__ = ["NullAuditLogger"]
