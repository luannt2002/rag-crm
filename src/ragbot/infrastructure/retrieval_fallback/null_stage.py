"""NullRetrievalStage — Null Object pattern for the retrieval fallback chain.

Selected by ``build_retrieval_fallback("null")`` and also returned by the
registry when the operator points a stage slot at a non-existent provider
key. The Null Object is **silent**: it returns the prior stage's result
unchanged (or ``[]`` if there is no prior). It never errors and never logs
a warning — orchestrator sees zero behaviour change.

Use case: operator wants to disable a single stage without rewriting the
chain. e.g. ``retrieval_stage_3 = "null"`` skips the keyword filter step.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class NullRetrievalStage:
    """No-op stage — passes through the prior stage's result."""

    def __init__(self, **kwargs: Any) -> None:
        # ``**kwargs`` so registry kwargs filtering doesn't crash on extras.
        self._kwargs = kwargs

    @property
    def stage_name(self) -> str:
        return "null"

    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float],
        record_bot_id: UUID,
        top_k: int,
        prior_stage_result: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        # Return prior result as-is to make this stage truly transparent in
        # the chain. Empty list when nothing upstream produced output.
        logger.debug(
            "retrieval_stage_null_bypass",
            prior=len(prior_stage_result or []),
            top_k=top_k,
        )
        return list(prior_stage_result or [])


__all__ = ["NullRetrievalStage"]
