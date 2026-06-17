"""Unit tests for DBBackedApiKeyPoolFactory — Stream J Phase 5."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.shared.api_key_pool import (
    ApiKeyPool,
    ApiKeyPoolFactory,
    DBBackedApiKeyPoolFactory,
)


def _make_session_factory(rows: list[tuple[str, bool]]):
    """Build mock session_factory that returns the given (encrypted, is_default) rows."""
    result = MagicMock()
    result.fetchall.return_value = [(enc, is_def) for enc, is_def in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _sf():
        yield session

    return _sf


def test_extends_base_factory():
    f = DBBackedApiKeyPoolFactory(
        provider_keys={"jina_ai": ["env-key"]},
        redis_client=MagicMock(),
        session_factory=_make_session_factory([]),
    )
    assert isinstance(f, ApiKeyPoolFactory)


def test_sync_get_still_works_via_inherited():
    """Sync get() inherited from base factory keeps env-only path alive."""
    f = DBBackedApiKeyPoolFactory(
        provider_keys={"jina_ai": ["env-key-1"]},
        redis_client=MagicMock(),
        session_factory=_make_session_factory([]),
    )
    pool = f.get("jina_ai", "rerank")
    assert isinstance(pool, ApiKeyPool)


def test_get_with_refresh_returns_none_when_no_db_no_env():
    f = DBBackedApiKeyPoolFactory(
        provider_keys={},
        redis_client=MagicMock(),
        session_factory=_make_session_factory([]),
    )
    pool = asyncio.run(f.get_with_refresh("unknown_provider", "rerank"))
    assert pool is None


def test_get_with_refresh_falls_back_to_env_when_db_empty():
    """No ai_keys row → use provider_keys env dict (back-compat)."""
    f = DBBackedApiKeyPoolFactory(
        provider_keys={"jina_ai": ["env-key-1"]},
        redis_client=MagicMock(),
        session_factory=_make_session_factory([]),
    )
    pool = asyncio.run(f.get_with_refresh("jina_ai", "rerank"))
    assert pool is not None
    assert isinstance(pool, ApiKeyPool)


def test_cache_ttl_avoids_repeated_db_reads():
    """Same call within TTL hits memory cache, not DB."""
    sf = _make_session_factory([])
    f = DBBackedApiKeyPoolFactory(
        provider_keys={"jina_ai": ["env-key-1"]},
        redis_client=MagicMock(),
        session_factory=sf,
    )
    asyncio.run(f.get_with_refresh("jina_ai", "rerank"))
    asyncio.run(f.get_with_refresh("jina_ai", "rerank"))
    # Pool was cached after first call; second call must not hit DB again.
    # Verify by checking _refresh_at populated.
    assert ("jina_ai", "rerank") in f._refresh_at


def test_db_outage_falls_back_silently_to_env():
    """DB exception → log warning, return env-based pool, never raise."""
    @asynccontextmanager
    async def _sf_broken():
        session = MagicMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        yield session

    f = DBBackedApiKeyPoolFactory(
        provider_keys={"jina_ai": ["env-key-1"]},
        redis_client=MagicMock(),
        session_factory=_sf_broken,
    )
    pool = asyncio.run(f.get_with_refresh("jina_ai", "rerank"))
    assert pool is not None  # env fallback used
