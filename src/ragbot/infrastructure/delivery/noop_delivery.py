"""No-op delivery — caller polls job status."""
from __future__ import annotations

from typing import Any


class NoopDelivery:
    """Do nothing — caller uses GET /jobs/{id} to poll."""

    async def deliver(self, result: dict[str, Any]) -> bool:
        return True  # always "succeeds" (nothing to do)

    @property
    def mode_name(self) -> str:
        return "poll"
