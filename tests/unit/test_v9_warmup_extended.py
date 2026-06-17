"""Extended warmup probes (reranker + tokenizer).

Covers the new ``_warmup_reranker`` / ``_warmup_tokenizer`` paths plus
the assertion that ``run_warmup`` keeps every probe independent (one
failing does not short-circuit the others) and respects the per-probe
timeout budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.infrastructure.observability import warmup as warmup_mod


class _OkEmbedder:
    async def health_check(self) -> bool:
        return True


class _OkLLM:
    async def health_check(self) -> bool:
        return True


class _OkReranker:
    def __init__(self) -> None:
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        return True


class _RaisingReranker:
    async def health_check(self) -> bool:
        raise OSError("dns fail")


class _SlowReranker:
    async def health_check(self) -> bool:
        await asyncio.sleep(5.0)
        return True


class _OkTokenizer:
    def __init__(self) -> None:
        self.calls = 0

    def tokenize(self, text: str) -> list[str]:
        self.calls += 1
        return text.split() or ["<empty>"]


class _RaisingTokenizer:
    def tokenize(self, text: str) -> list[str]:
        raise ValueError("model load fail")


class _StubContainer:
    def __init__(
        self,
        *,
        embedder: Any,
        llm: Any,
        reranker: Any | None = None,
        tokenizer: Any | None = None,
    ) -> None:
        self._embedder = embedder
        self._llm = llm
        self._reranker = reranker
        self._tokenizer = tokenizer

    def embedder(self) -> Any:
        return self._embedder

    def llm(self) -> Any:
        return self._llm

    def reranker(self) -> Any:
        if self._reranker is None:
            raise AttributeError("reranker provider not bound")
        return self._reranker

    def tokenizer(self) -> Any:
        if self._tokenizer is None:
            raise AttributeError("tokenizer provider not bound")
        return self._tokenizer


@pytest.mark.asyncio
async def test_warmup_runs_reranker_probe_when_healthy() -> None:
    reranker = _OkReranker()
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=reranker,
        tokenizer=_OkTokenizer(),
    )
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["reranker_ok"] is True
    assert reranker.calls == 1


@pytest.mark.asyncio
async def test_warmup_runs_tokenizer_probe_when_healthy() -> None:
    tokenizer = _OkTokenizer()
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=_OkReranker(),
        tokenizer=tokenizer,
    )
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["tokenizer_ok"] is True
    assert tokenizer.calls == 1


@pytest.mark.asyncio
async def test_warmup_independent_probes_one_fails_others_succeed() -> None:
    """Reranker raising must not cancel the tokenizer probe."""
    tokenizer = _OkTokenizer()
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=_RaisingReranker(),
        tokenizer=tokenizer,
    )
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["embed_ok"] is True
    assert summary["llm_ok"] is True
    assert summary["reranker_ok"] is False
    assert summary["tokenizer_ok"] is True
    assert tokenizer.calls == 1  # tokenizer ran despite reranker failure


@pytest.mark.asyncio
async def test_warmup_reranker_timeout_does_not_block_app_readiness() -> None:
    """Slow reranker must respect timeout_s and surface failure."""
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=_SlowReranker(),
        tokenizer=_OkTokenizer(),
    )
    t0 = asyncio.get_event_loop().time()
    summary = await warmup_mod.run_warmup(container, timeout_s=0.05)
    elapsed = asyncio.get_event_loop().time() - t0
    assert summary["reranker_ok"] is False
    # Total wall-time must be bounded — 4 probes * 0.05s = 0.2s ceiling
    # plus a small scheduler slack.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_warmup_silent_success_when_reranker_provider_missing() -> None:
    """Container without a reranker binding (legacy) → silent success."""
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=None,
        tokenizer=None,
    )
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["reranker_ok"] is True
    assert summary["reranker_ms"] == 0.0
    assert summary["tokenizer_ok"] is True
    assert summary["tokenizer_ms"] == 0.0


@pytest.mark.asyncio
async def test_warmup_tokenizer_failure_logged_does_not_raise() -> None:
    container = _StubContainer(
        embedder=_OkEmbedder(),
        llm=_OkLLM(),
        reranker=_OkReranker(),
        tokenizer=_RaisingTokenizer(),
    )
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["tokenizer_ok"] is False
    assert summary["embed_ok"] is True  # other probes unaffected
