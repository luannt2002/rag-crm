"""LexicalRetrievalPort — Strategy + DI contract for sparse / keyword retrieval.

The orchestrator runs vector retrieval (dense, semantic) in parallel with
the lexical branch (BM25 / tsvector / etc.), then fuses both via RRF. The
two paths are deliberately decoupled so an operator can flip the lexical
strategy off (``provider="null"``), swap providers, or run a non-Postgres
backend (Elasticsearch, OpenSearch, in-process BM25) without touching the
orchestrator.

Return shape mirrors the vector path (``chunk_id``, ``document_id``,
``content``/``text``, ``score``, ``metadata``) so the downstream RRF merge
can dedupe by ``chunk_id`` and the rerank node sees a uniform payload.

Tenant isolation: the adapter MUST scope by ``record_bot_id`` (the
internal UUID, 1:1 with the external 4-key bot identity). The orchestrator
never passes raw tenant_id / bot_id slugs to this port.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class LexicalRetrievalPort(Protocol):
    """Sparse / lexical retrieval contract.

    @param query: rewritten query string (post-abbrev-expand, post-vocab).
    @param record_bot_id: internal UUID PK of the bot — tenant isolation key.
    @param top_k: max chunks to return.
    @param cr_enhanced: opt-in flag — when True the adapter is free to
        index a richer text surface for BM25 (e.g. ``content`` PLUS the
        per-chunk situated-context string written by the Anthropic CR
        enricher). Default ``False`` keeps the legacy ``content``-only
        path bit-exact for bots that have not flipped
        ``plan_limits.cr_enhanced_enabled``. Adapter MAY ignore the flag
        (Null Object, ES backend) — contract is best-effort hint, not a
        hard requirement.
    @return: list of chunk dicts; empty list when nothing matches or the
        adapter is disabled (Null Object). MUST NOT raise on empty corpus
        or no-match — those are normal states.
    """

    async def search(
        self,
        query: str,
        record_bot_id: UUID,
        top_k: int,
        cr_enhanced: bool = False,
    ) -> list[dict[str, Any]]: ...


__all__ = ["LexicalRetrievalPort"]
