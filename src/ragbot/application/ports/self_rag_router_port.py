"""Self-RAG / Adaptive routing Protocol — contract for routing strategies.

A Self-RAG router decides whether the retrieve stage can be skipped for a
given (intent, query) pair. Skipping retrieval for conversational intents
(greeting / chitchat / vu_vo) avoids embed + pgvector + RRF + rerank work
where the LLM does not need grounded chunks to answer, cutting tier-1
latency and token usage.

Default implementation is a Null Object (always returns ``False`` -- never
skip) so wiring this Port into the orchestrator is operator-OFF until an
adaptive strategy is selected via ``system_config.self_rag_router_provider``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SelfRagRouterPort(Protocol):
    """Adaptive routing abstraction — decides skip-retrieve per query.

    Implementations must be pure (no I/O) and side-effect free so the
    orchestrator can call them in the hot path without latency cost.
    """

    def should_skip_retrieve(self, intent: str, query: str) -> bool:
        """Return True when the retrieve stage can be safely bypassed.

        @param intent: classifier label (e.g. ``factoid``, ``greeting``).
        @param query: raw user query string (already PII-redacted upstream).
        @return: True to skip retrieve, False to run the normal pipeline.
        """
        ...


__all__ = ["SelfRagRouterPort"]
