"""Port-boundary ledger decorators for embed/rerank adapters.

CLAUDE.md Strategy + DI + Decorator: instead of duplicating the
``emit_aux_usage`` call inside each of the 11 embed/rerank adapters (only
``jina`` ever did), wrap the resolved adapter ONCE at the Port boundary in
``build_embedder`` / ``build_reranker``. Every provider then produces a
``token_ledger`` row — closing the coverage gap where the active provider
decided whether ANY embed/rerank cost was recorded.

Token-accurate cost: an adapter that already knows its per-call token usage
exposes ``last_usage`` (a dict ``{total_tokens, input_tokens, cost_usd,
input_unit_price}``); the decorator reads it after the call and snapshots
those numbers. Adapters that already self-emit a token-rich row set
``emits_own_ledger = True`` so the decorator does NOT double-count them — it
becomes a transparent pass-through for those. Adapters that expose nothing
still get a coverage row (the call happened, with real timing) so the ledger
never silently misses a provider.

The decorators proxy the full Port surface so they are drop-in interchangeable
with the wrapped adapter (Liskov): unknown attributes delegate to the inner
adapter via ``__getattr__``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ragbot.application.ports.token_ledger_port import TokenLedgerPort
from ragbot.infrastructure.token_ledger.aux_usage import emit_aux_usage


def _emits_own(adapter: object) -> bool:
    """True if the adapter already emits its own token-rich ledger row."""
    return bool(getattr(adapter, "emits_own_ledger", False))


def _read_usage(adapter: object) -> dict[str, Any]:
    """Read the adapter's optional per-call ``last_usage`` (empty if absent)."""
    usage = getattr(adapter, "last_usage", None)
    return usage if isinstance(usage, dict) else {}


class LedgerEmittingEmbedderDecorator:
    """Wrap an embedder and emit one ``action='embedding'`` ledger row per call."""

    def __init__(
        self,
        inner: Any,
        *,
        ledger: TokenLedgerPort | None,
        provider: str | None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._provider = provider
        # Pass-through when the inner adapter self-emits — avoids double rows.
        self._pass_through = _emits_own(inner)

    def _emit(self, started_at: datetime) -> None:
        if self._ledger is None or self._pass_through:
            return
        usage = _read_usage(self._inner)
        emit_aux_usage(
            self._ledger,
            action="embedding",
            provider=self._provider,
            model=str(getattr(self._inner, "model_id", "") or "") or None,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            input_unit_price=usage.get("input_unit_price"),
            cost_usd=usage.get("cost_usd"),
        )

    async def embed_one(self, text: str, **kwargs: Any) -> list[float]:
        started = datetime.now(UTC)
        out = await self._inner.embed_one(text, **kwargs)
        self._emit(started)
        return out

    async def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        started = datetime.now(UTC)
        out = await self._inner.embed_batch(texts, **kwargs)
        self._emit(started)
        return out

    async def embed_query(self, text: str) -> list[float]:
        started = datetime.now(UTC)
        out = await self._inner.embed_query(text)
        self._emit(started)
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        started = datetime.now(UTC)
        out = await self._inner.embed_documents(texts)
        self._emit(started)
        return out

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def close(self) -> None:
        await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        # Delegate properties (dimension/model_id) + any unproxied method.
        return getattr(self._inner, name)


class LedgerEmittingRerankerDecorator:
    """Wrap a reranker and emit one ``action='rerank'`` ledger row per call."""

    def __init__(
        self,
        inner: Any,
        *,
        ledger: TokenLedgerPort | None,
        provider: str | None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._provider = provider
        self._pass_through = _emits_own(inner)

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        started = datetime.now(UTC)
        out = await self._inner.rerank(query, chunks, **kwargs)
        if self._ledger is not None and not self._pass_through:
            usage = _read_usage(self._inner)
            emit_aux_usage(
                self._ledger,
                action="rerank",
                provider=self._provider,
                model=str(getattr(self._inner, "mode", "") or "") or None,
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                started_at=started,
                finished_at=datetime.now(UTC),
                input_unit_price=usage.get("input_unit_price"),
                cost_usd=usage.get("cost_usd"),
            )
        return out

    @property
    def mode(self) -> str:
        return self._inner.mode

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def close(self) -> None:
        await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


__all__ = [
    "LedgerEmittingEmbedderDecorator",
    "LedgerEmittingRerankerDecorator",
]
