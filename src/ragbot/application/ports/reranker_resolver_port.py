"""RerankerResolverPort — Protocol for per-bot reranker resolution.

Dependency Injection contract for resolving which reranker provider
is configured for a specific bot, based on ``bot_model_bindings.purpose='rerank'``.

Pattern:
  record_bot_id → Redis cache (60s TTL) → DB (ai_providers + ai_models +
  bot_model_bindings) → build_reranker(provider=code, api_key=env) →
  RerankerPort instance.

Fail-soft: no binding OR config error → NullReranker (existing behaviour preserved).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from ragbot.application.ports.reranker_port import RerankerPort


class RerankerResolverPort(Protocol):
    """Resolve which reranker provider is configured for a specific bot.

    Implementations MUST be fail-soft: any missing binding, empty key, or
    infrastructure error must return ``NullReranker`` rather than raising.
    """

    async def resolve_for_bot(self, record_bot_id: UUID) -> "RerankerPort":
        """Return the RerankerPort instance configured for the bot.

        @param record_bot_id: UUID PK of the ``bots`` row (internal key).
        @return: RerankerPort implementation (Jina / Cohere / Null / etc.).
                 Guaranteed not to raise — any failure falls back to NullReranker.
        """
        ...


__all__ = ["RerankerResolverPort"]
