"""ViRankerLocalReranker — STUB for the on-host Vietnamese reranker.

This file ships only as a registry placeholder so operators see the option in
``list_providers()`` and the registry can fail-loud with an actionable message
when someone flips ``reranker_provider="viranker_local"`` without provisioning
the model. NOT installed by default — pulling in ``sentence-transformers`` +
the ViRanker weights is heavy and out of scope for the reranker-OFF baseline.

To enable in a real deployment:

1. Add to ``pyproject.toml``::

       sentence-transformers = "^3.0"

2. Pre-download the ViRanker weights (or BGE-reranker-v2-m3 as a substitute)
   into a local cache directory.

3. Replace the body of ``rerank`` with a real ``CrossEncoder.predict`` call.

4. Set ``reranker_provider="viranker_local"`` in ``system_config``.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import DEFAULT_RERANK_TOP_N


class ViRankerLocalReranker:
    """Local cross-encoder reranker — DISABLED stub."""

    def __init__(self, *, model: str | None = None) -> None:
        raise NotImplementedError(
            "ViRanker local reranker is not installed. "
            "Install sentence-transformers, download viranker model weights, "
            "implement CrossEncoder.predict in this file, and register the "
            "class in ragbot.infrastructure.reranker.registry. "
            "See viranker_local_reranker.py docstring for the full guide.",
        )

    @staticmethod
    def get_provider_name() -> str:
        return "viranker_local"

    @property
    def mode(self) -> str:  # pragma: no cover
        """Observability identifier matching RerankerPort.mode."""
        return "viranker_local"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:  # pragma: no cover — unreachable until installed
        raise NotImplementedError("Stub — see __init__")

    async def health_check(self) -> bool:  # pragma: no cover
        return False

    async def close(self) -> None:  # pragma: no cover
        return None


__all__ = ["ViRankerLocalReranker"]
