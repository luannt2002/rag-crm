"""Port-boundary ledger decorators — universal embed/rerank emit coverage (G-2).

Before this, only the ``jina`` adapter of 11 embed/rerank providers emitted to
``token_ledger``; whichever provider DB config actually selected decided whether
ANY embed/rerank cost was recorded. The decorators wrap the resolved adapter at
the Port boundary so EVERY provider emits a row (Strategy + Decorator + DI).

Locks:
  * a non-jina embedder (no self-emit) wrapped by the decorator emits exactly
    one ``action='embedding'`` row with status='success' on embed_one/embed_batch.
  * a non-jina reranker wrapped by the decorator emits one ``action='rerank'``
    row.
  * an adapter that self-emits (``emits_own_ledger = True``, e.g. jina) is NOT
    double-counted by the decorator.
  * the decorator transparently proxies health_check / close / mode.
"""
from __future__ import annotations

import pytest

from ragbot.application.ports.token_ledger_port import TokenLedgerEntry
from ragbot.infrastructure.token_ledger.ledger_emitting_decorators import (
    LedgerEmittingEmbedderDecorator,
    LedgerEmittingRerankerDecorator,
)


class _SpyLedger:
    def __init__(self) -> None:
        self.entries: list[TokenLedgerEntry] = []

    def emit(self, entry: TokenLedgerEntry) -> None:
        self.entries.append(entry)


class _FakeEmbedder:
    emits_own_ledger = False

    def __init__(self) -> None:
        self.closed = False

    @property
    def model_id(self) -> str:
        return "fake-embed"

    async def embed_one(self, text, *, spec=None, record_tenant_id=None):
        return [0.1, 0.2, 0.3]

    async def embed_batch(self, texts, *, spec=None, record_tenant_id=None):
        return [[0.1] for _ in texts]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


class _SelfEmittingEmbedder(_FakeEmbedder):
    emits_own_ledger = True


class _FakeReranker:
    emits_own_ledger = False

    @property
    def mode(self) -> str:
        return "fake:reranker"

    async def rerank(self, query, chunks, *, top_n=5, model=None):
        return chunks[:top_n]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_embedder_decorator_emits_one_embedding_row():
    ledger = _SpyLedger()
    dec = LedgerEmittingEmbedderDecorator(_FakeEmbedder(), ledger=ledger, provider="zeroentropy")
    out = await dec.embed_one("hello")
    assert out == [0.1, 0.2, 0.3]
    rows = [e for e in ledger.entries if e.action == "embedding"]
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert rows[0].provider == "zeroentropy"


@pytest.mark.asyncio
async def test_embedder_decorator_batch_emits_one_row():
    ledger = _SpyLedger()
    dec = LedgerEmittingEmbedderDecorator(_FakeEmbedder(), ledger=ledger, provider="bkai_vn")
    await dec.embed_batch(["a", "b", "c"])
    rows = [e for e in ledger.entries if e.action == "embedding"]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_reranker_decorator_emits_one_rerank_row():
    ledger = _SpyLedger()
    dec = LedgerEmittingRerankerDecorator(_FakeReranker(), ledger=ledger, provider="voyage")
    out = await dec.rerank("q", [{"content": "a"}, {"content": "b"}], top_n=1)
    assert len(out) == 1
    rows = [e for e in ledger.entries if e.action == "rerank"]
    assert len(rows) == 1
    assert rows[0].provider == "voyage"


@pytest.mark.asyncio
async def test_self_emitting_adapter_not_double_counted():
    """jina-style adapters (emits_own_ledger=True) must NOT get a decorator row."""
    ledger = _SpyLedger()
    dec = LedgerEmittingEmbedderDecorator(
        _SelfEmittingEmbedder(), ledger=ledger, provider="jina",
    )
    await dec.embed_one("hello")
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_decorator_proxies_health_close_and_mode():
    ledger = _SpyLedger()
    emb = _FakeEmbedder()
    dec_e = LedgerEmittingEmbedderDecorator(emb, ledger=ledger, provider="zeroentropy")
    assert await dec_e.health_check() is True
    await dec_e.close()
    assert emb.closed is True

    dec_r = LedgerEmittingRerankerDecorator(_FakeReranker(), ledger=ledger, provider="voyage")
    assert dec_r.mode == "fake:reranker"
