"""Proximity cache Protocol — semantic-similarity short-circuit for LLM calls.

Owner-opt-in cache that bypasses the LLM when an incoming query embedding is
sufficiently close to a previously cached query. Hit returns the prior answer;
miss returns ``None`` and the caller proceeds with the regular pipeline.

Implementations: :class:`NullProximityCache` (default OFF), and an in-memory
LSH-bucketed strategy. A Redis-backed strategy is the planned production
backend; until then the LSH adapter is the reference in-process bucket store.

Caller contract is intentionally narrow — only ``lookup`` / ``store`` — so
upgrading the backend later does not change orchestration code. Embeddings
are passed in already-computed by the embedder Port; this adapter never
performs embedding itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CacheHit:
    """Returned by :meth:`ProximityCachePort.lookup` on a similarity hit.

    @param answer: previously cached answer string.
    @param similarity: cosine similarity of the incoming embedding against the
        cached query embedding (range [-1.0, 1.0]; only entries above the
        configured threshold are surfaced).
    @param original_query: the query string that produced ``answer`` — kept
        for observability so logs can show *why* a hit was returned.
    """

    answer: str
    similarity: float
    original_query: str


@runtime_checkable
class ProximityCachePort(Protocol):
    """Owner-opt-in semantic-proximity cache contract.

    Only two operations: ``lookup`` to short-circuit LLM calls, ``store`` to
    persist a fresh answer. No tenant scoping in the Port signature — scoping
    is the caller's responsibility (build a per-tenant adapter at DI time).
    """

    def lookup(self, query_embedding: list[float]) -> CacheHit | None:
        """Return a :class:`CacheHit` if any cached query exceeds threshold.

        @param query_embedding: dense vector of the incoming query.
        @return: :class:`CacheHit` on similarity hit, ``None`` otherwise.
        """
        ...

    def store(self, query_embedding: list[float], answer: str, ttl_s: int) -> None:
        """Persist ``answer`` keyed by ``query_embedding``.

        @param query_embedding: dense vector of the query that produced
            ``answer``.
        @param answer: the LLM-generated answer to cache.
        @param ttl_s: time-to-live in seconds. Implementations may evict
            entries lazily; ``0`` means do-not-store.
        """
        ...


__all__ = ["CacheHit", "ProximityCachePort"]
