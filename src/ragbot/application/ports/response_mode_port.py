"""Response delivery strategy — how answer reaches the caller."""
from __future__ import annotations

from typing import Any, Protocol


class ResponseDeliveryPort(Protocol):
    """Strategy for delivering RAG answer to caller."""

    async def deliver(self, result: dict[str, Any]) -> bool:
        """Deliver answer. Returns True if successful."""
        ...

    @property
    def mode_name(self) -> str:
        """Human-readable mode name for logging."""
        ...
