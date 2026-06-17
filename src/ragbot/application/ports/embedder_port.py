"""Embedder Strategy port — simplified interface for failover wrapping.

Distinct from ``EmbeddingPort`` (which carries the full DB-driven
``EmbeddingSpec`` + tenant context for the orchestrator's ``embed_one`` call
site). This Port models the *minimal* contract that a failover wrapper /
health probe needs:

* ``embed_query`` / ``embed_documents`` — the data path.
* ``health_check`` — boot-time + circuit-breaker probe.
* ``dimension`` / ``model_id`` — observability metadata used by failover
  policy (dimension-mismatch warning, structured logs).

Concrete strategies may also implement ``EmbeddingPort.embed_one`` for
drop-in use by the orchestrator without changes to ``query_graph``. The
registry treats both Ports as interchangeable at the type level.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedderPort(Protocol):
    """Strategy port for query/document embedding (minimal, sync-friendly)."""

    async def embed_query(self, text: str) -> list[float]:
        """Return a dense vector for ``text``.

        Raises ``EmbeddingError`` on adapter failure (after retry / CB exhaust).
        """
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batched ``embed_query`` for documents.

        Raises ``EmbeddingError`` on adapter failure.
        """
        ...

    async def health_check(self) -> bool:
        """Return ``True`` iff a probe call succeeded.

        MUST return ``False`` on any failure (including raised exceptions
        caught internally) so probe callers never see exceptions.
        """
        ...

    @property
    def dimension(self) -> int:
        """Vector dimension this embedder produces (0 for null embedders)."""
        ...

    @property
    def model_id(self) -> str:
        """Provider-aware identifier for observability.

        Domain-neutral provider-derived label.
        """
        ...


__all__ = ["EmbedderPort"]
