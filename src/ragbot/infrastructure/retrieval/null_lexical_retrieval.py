"""NullLexicalRetrieval — Null Object pattern for the lexical port.

Selected when ``system_config.lexical_retrieval_provider="null"`` (the
default, backward-compat baseline). Returns an empty list so the
orchestrator's RRF merge degrades cleanly to "vector only" without any
branching in the retrieve node.

Picking Null is a *deliberate* operator choice — keeping it observable
in audit logs via ``mode="lexical"`` + ``provider="null"`` mirrors the
NullReranker convention.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class NullLexicalRetrieval:
    """No-op lexical retrieval; always returns ``[]``."""

    def __init__(self, **_kwargs: Any) -> None:
        # Accept arbitrary kwargs so the registry can build NullLexicalRetrieval
        # with the same kwargs (e.g. ``session_factory=``) as a real adapter.
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    @property
    def mode(self) -> str:
        """Observability identifier."""
        return "null"

    async def search(
        self,
        query: str,  # noqa: ARG002 — Port contract requires the parameter
        record_bot_id: UUID,  # noqa: ARG002 — Port contract requires the parameter
        top_k: int,  # noqa: ARG002 — Port contract requires the parameter
        cr_enhanced: bool = False,  # noqa: ARG002 — Port contract requires the parameter
    ) -> list[dict[str, Any]]:
        return []

    async def health_check(self) -> bool:
        return True


__all__ = ["NullLexicalRetrieval"]
