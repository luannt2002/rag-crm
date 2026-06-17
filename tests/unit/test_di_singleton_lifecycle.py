"""Verify embedder + reranker DI providers are Singleton scope.

Background (Q16/Q17 + live diag 2026-05-18): pre-fix the ``embedder``
and ``reranker`` containers were ``providers.Factory`` — every chat
turn called ``container.embedder()`` and got a freshly-constructed
adapter, which in turn constructed a fresh ``httpx.AsyncClient`` (TLS
handshake + DNS lookup) on the first ``_get_client`` call. Flipping
to Singleton + the asyncio-lock guarded lazy-init means every request
in the process reuses one connection pool.

Test surface:

- ``container.embedder() is container.embedder()`` — same instance.
- ``container.reranker() is container.reranker()`` — same instance.
- ZeroEntropy embedder constructor now installs the lazy-init lock
  (regression guard against the race window that motivates the
  Singleton flip).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from ragbot.bootstrap import Container
from ragbot.infrastructure.embedding.zeroentropy_embedder import (
    ZeroEntropyEmbedder,
)


def _container() -> Container:
    """Build a Container without ``bootstrap()`` (avoid Redis/DB IO)."""
    return Container()


def test_embedder_is_singleton_returns_same_instance() -> None:
    """Singleton scope must return the same adapter on subsequent calls."""
    c = _container()
    try:
        a = c.embedder()
        b = c.embedder()
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"embedder build failed (env missing): {exc}")
    assert a is b, (
        "embedder must be Singleton — Factory scope leaks a new "
        "httpx.AsyncClient per request (TLS+DNS handshake cost)"
    )


def test_reranker_is_singleton_returns_same_instance() -> None:
    """Singleton scope must return the same adapter on subsequent calls."""
    c = _container()
    try:
        a = c.reranker()
        b = c.reranker()
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"reranker build failed (env missing): {exc}")
    assert a is b, (
        "reranker must be Singleton — Factory scope rebuilds the "
        "AsyncClient on every chat turn"
    )


def test_zeroentropy_embedder_constructs_client_lock() -> None:
    """ZE embedder lazy-init lock guards the double-checked client read.

    Without the lock the Singleton flip would surface a race window: N
    concurrent first-callers each see ``self._client is None`` and each
    instantiate a fresh AsyncClient → connection-pool fragmentation +
    leaked clients.
    """
    e = ZeroEntropyEmbedder()
    assert isinstance(e._client_lock, asyncio.Lock)


def test_zeroentropy_embedder_get_client_uses_double_checked_lock() -> None:
    """Source-level guard so future refactors cannot drop the lock."""
    src = inspect.getsource(ZeroEntropyEmbedder._get_client)
    assert "self._client_lock" in src, (
        "_get_client must guard the lazy init behind self._client_lock — "
        "Singleton scope would otherwise allow a race window where N "
        "concurrent first-callers each instantiate a fresh AsyncClient"
    )
    # Double-checked: outer ``if self._client is None`` + inner ``if
    # self._client is None`` after acquiring the lock.
    assert src.count("self._client is None") >= 2, (
        "double-checked locking pattern missing — outer read + inner "
        "re-check inside the lock"
    )


def test_bootstrap_module_declares_singleton_scope_for_perf_adapters() -> None:
    """Audit guard — bootstrap.py must wire embedder + reranker as
    Singletons.

    Without this guard a future ``providers.Factory(...)`` regression
    would slip through silently (no runtime symptom apart from extra
    handshake latency).
    """
    import ragbot.bootstrap as _bootstrap  # noqa: PLC0415
    src = inspect.getsource(_bootstrap)
    # ``embedder = providers.Singleton(`` and same for reranker.
    assert "embedder = providers.Singleton(" in src, (
        "embedder must be declared as providers.Singleton(...) in "
        "bootstrap.py — Factory leaks a new httpx.AsyncClient per call"
    )
    assert "reranker = providers.Singleton(" in src, (
        "reranker must be declared as providers.Singleton(...) in "
        "bootstrap.py — same TLS-handshake amortisation rationale"
    )
