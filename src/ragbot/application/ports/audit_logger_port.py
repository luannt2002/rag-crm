"""Port for the pipeline audit emitter — concrete impls send to JSONL/OTel/Loki."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AuditLoggerPort(Protocol):
    """Append a single audit line for a pipeline event."""

    async def log(
        self,
        bot_id: str,
        stage: str,
        event: str,
        data: dict[str, Any],
    ) -> None: ...


__all__ = ["AuditLoggerPort"]
