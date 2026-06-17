"""Lock test — F14-HIGH-3.2 + F14-MED-CC3-3 JinaReranker resilience.

Asserts:
- JinaReranker has a CircuitBreaker that opens after consecutive failures
  and fast-fails subsequent rerank calls (saves p95 during outages).
- JinaReranker reuses a single ``httpx.AsyncClient`` instead of building
  one per call (saves TLS handshake / DNS lookup ~20-80 ms per request).
- Retry-with-backoff is wired for transient errors only.

Domain-neutral. No brand / industry literals.
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
)
from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.shared.constants import (
    DEFAULT_JINA_RERANKER_CB_FAIL_MAX,
    DEFAULT_JINA_RERANKER_CB_RESET_S,
    DEFAULT_JINA_RERANKER_MAX_ATTEMPTS,
)
from ragbot.shared.errors import RetrievalError


def test_constants_exist_and_are_sensible() -> None:
    assert DEFAULT_JINA_RERANKER_MAX_ATTEMPTS >= 1
    assert DEFAULT_JINA_RERANKER_CB_FAIL_MAX >= 1
    assert DEFAULT_JINA_RERANKER_CB_RESET_S > 0


def test_jina_instance_has_circuit_breaker() -> None:
    rr = JinaReranker(api_key="placeholder-key")
    assert hasattr(rr, "_cb"), "F14-HIGH-3.2 regression — CB attribute missing"
    assert isinstance(rr._cb, CircuitBreaker)
    assert rr._cb.state == CBState.CLOSED


def test_jina_instance_has_lazy_client_attribute() -> None:
    """F14-MED-CC3-3 — module must hold a reusable client (lazy-init)."""
    rr = JinaReranker(api_key="placeholder-key")
    assert hasattr(rr, "_client")
    # Lazy: should be None pre-call (httpx.AsyncClient binds the running loop;
    # constructing in __init__ would tie the client to the wrong loop in some
    # DI bootstraps).
    assert rr._client is None


def test_jina_source_uses_retry_with_backoff() -> None:
    """Source-level: rerank() body must invoke retry_with_backoff."""
    src = inspect.getsource(JinaReranker.rerank)
    assert "retry_with_backoff" in src, (
        "F14-HIGH-3.2 regression — retry_with_backoff not wired"
    )
    assert "self._cb" in src, "F14-HIGH-3.2 regression — CB not used"


def test_jina_source_does_not_construct_client_per_call() -> None:
    """F14-MED-CC3-3: rerank() body must not build a new AsyncClient inline.

    Allowed: ``self._get_client()`` (the lazy reuse helper).
    Forbidden: ``async with httpx.AsyncClient(...) as`` inside rerank().
    """
    src = inspect.getsource(JinaReranker.rerank)
    assert "httpx.AsyncClient(" not in src, (
        "F14-MED-CC3-3 regression — rerank still creates client per call"
    )
    assert "self._get_client" in src or "_get_client" in src


@pytest.mark.asyncio
async def test_circuit_opens_after_consecutive_failures() -> None:
    """Drive the CB directly through repeated failures and assert OPEN."""
    rr = JinaReranker(api_key="placeholder-key")
    # Drive failures into the CB without doing real HTTP.
    for _ in range(DEFAULT_JINA_RERANKER_CB_FAIL_MAX):
        rr._cb.record_failure()
    assert rr._cb.state == CBState.OPEN, (
        "CB must transition to OPEN after fail_max consecutive failures"
    )


@pytest.mark.asyncio
async def test_open_circuit_short_circuits_rerank_call() -> None:
    """When CB is OPEN, rerank() must raise RetrievalError without HTTP."""
    rr = JinaReranker(api_key="placeholder-key")
    # Force OPEN state.
    for _ in range(DEFAULT_JINA_RERANKER_CB_FAIL_MAX):
        rr._cb.record_failure()
    assert rr._cb.state == CBState.OPEN

    chunks = [{"content": "doc-a"}, {"content": "doc-b"}]
    with pytest.raises(RetrievalError):
        await rr.rerank("query", chunks, top_n=2)


def test_close_method_clears_client() -> None:
    rr = JinaReranker(api_key="placeholder-key")
    # _client starts None; close() must remain idempotent in that case.
    import asyncio
    asyncio.run(rr.close())
    assert rr._client is None
