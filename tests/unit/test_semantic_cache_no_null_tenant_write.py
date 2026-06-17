"""semantic_cache must NEVER write a NULL-tenant row.

Pre-fix: `PgSemanticCache.store(...)` coerced a falsy `record_tenant_id`
to NULL via ``str(record_tenant_id) if record_tenant_id else None``.
The read-path drops ``OR record_tenant_id IS NULL`` so today's reads
never match those rows, but a future relax (e.g. "platform-wide cache")
would resurrect them as a cross-tenant leak. Latent bomb. Fix:
skip-and-warn on None.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from ragbot.application.ports.cache_port import CachedResponse
from ragbot.infrastructure.cache import semantic_cache as _sc_module
from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache


class _RecordingSession:
    """Stand-in that captures every executed statement."""

    def __init__(self, log: list[Any]) -> None:
        self._log = log

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self._log.append((stmt, params))

        class _R:
            rowcount = 1

        return _R()

    async def commit(self) -> None:
        return None


def _make_cache() -> tuple[PgSemanticCache, list[Any]]:
    statements: list[Any] = []

    def _factory() -> _RecordingSession:
        return _RecordingSession(statements)

    return PgSemanticCache(_factory), statements  # type: ignore[arg-type]


def _payload(record_tenant_id: Any) -> dict[str, Any]:
    return {
        "query": "hello world",
        "query_embedding": [0.1, 0.2, 0.3],
        "response": CachedResponse(
            answer="hi",
            citations=[],
            model_name="test-model",
            cached_at_ts=1700000000,
        ),
        "record_tenant_id": record_tenant_id,
        "record_bot_id": uuid4(),
        "workspace_id": "ws-default",
        "bot_version": "v1",
        "corpus_version": "c1",
    }


# ---------------------------------------------------------------------------
# 1. NULL tenant → write skipped, warning logged, NO INSERT executed.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_skipped_when_tenant_none() -> None:
    """NULL tenant → write skipped, warning logged via structlog,
    NO INSERT executed. We patch the module-level structlog logger so
    we can assert the warning emitted regardless of structlog config."""
    cache, statements = _make_cache()

    with patch.object(_sc_module, "logger") as mock_logger:
        await cache.store(**_payload(record_tenant_id=None))

    assert statements == [], (
        "semantic_cache.store inserted a row with NULL tenant — "
        "regression of HIGH-1; cross-tenant leak risk"
    )
    # Loud log so missing-tenant misses are visible in metrics.
    mock_logger.warning.assert_called_once()
    msg = mock_logger.warning.call_args.args[0]
    assert "semantic_cache.store skipped" in msg, (
        f"expected explicit skip warning, got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# 2. Non-null tenant → INSERT proceeds with the real tenant value bound.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_succeeds_when_tenant_provided() -> None:
    cache, statements = _make_cache()
    tenant_id = uuid4()

    await cache.store(**_payload(record_tenant_id=tenant_id))

    assert len(statements) == 1, "expected exactly 1 INSERT"
    _stmt, params = statements[0]
    assert params["tid"] == str(tenant_id), (
        "tenant id must be bound exactly — never coerced to None"
    )
    assert params["tid"] is not None
