"""_build_pipeline_config now uses 1 batched get_many() Redis MGET.

Pins:
- get_many() called exactly once with all 30 keys.
- get_int / get_float / get NOT called individually for system_config keys.
- Returned dict has the same shape as the prior implementation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ragbot.interfaces.http.routes.test_chat import (
    _PIPELINE_CFG_KEYS,
    _build_pipeline_config,
)


def _make_cfg_svc(values: dict | None = None) -> AsyncMock:
    """Mock SystemConfigService with a recording ``get_many``."""
    svc = AsyncMock()
    svc.get_many = AsyncMock(return_value=values or {})
    # Track prior single-key reads — should NOT be invoked.
    svc.get_int = AsyncMock()
    svc.get_float = AsyncMock()
    svc.get = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_build_pipeline_config_uses_single_get_many_call():
    cfg = _make_cfg_svc()
    bot = SimpleNamespace(bot_name="b1", oos_answer_template=None)

    await _build_pipeline_config(cfg, bot)

    assert cfg.get_many.await_count == 1
    requested = list(cfg.get_many.await_args.args[0])
    assert requested == list(_PIPELINE_CFG_KEYS)


@pytest.mark.asyncio
async def test_build_pipeline_config_does_not_use_prior_single_reads():
    cfg = _make_cfg_svc()
    bot = SimpleNamespace(bot_name="b1", oos_answer_template=None)

    await _build_pipeline_config(cfg, bot)

    assert cfg.get_int.await_count == 0
    assert cfg.get_float.await_count == 0
    assert cfg.get.await_count == 0


@pytest.mark.asyncio
async def test_build_pipeline_config_applies_defaults_for_missing_keys():
    cfg = _make_cfg_svc(values={})  # all missing → defaults
    bot = SimpleNamespace(bot_name="b1", oos_answer_template=None)

    out = await _build_pipeline_config(cfg, bot)

    # Wave M3.3-B 2026-05-20: missing-key fallback now uses constants
    # (DEFAULT_TOP_K=20, DEFAULT_RERANK_TOP_N=7). Pre-fix literal ``5``
    # silently regressed Z2 migration 0057's seed value of 7.
    from ragbot.shared.constants import DEFAULT_RERANK_TOP_N, DEFAULT_TOP_K
    assert out["top_k"] == DEFAULT_TOP_K
    assert out["rerank_top_n"] == DEFAULT_RERANK_TOP_N
    assert out["condense_history_limit"] == 6
    assert out["grade_chunk_preview"] == 500
    assert out["reflect_answer_preview"] == 500
    assert out["crag_fallback_count"] == 2
    assert out["max_grade_retries"] == 1
    assert out["max_reflect_retries"] == 1
    assert out["graph_recursion_limit"] == 50
    assert out["guardrail_leak_shingle_size"] == 12
    assert out["reranker_enabled"] is True
    assert out["citation_marker_required"] is False
    assert out["embedding_model"] == "unknown"
    # bot_name comes from SimpleNamespace
    assert out["bot_name"] == "b1"


@pytest.mark.asyncio
async def test_build_pipeline_config_respects_redis_overrides():
    cfg = _make_cfg_svc(values={
        "rag_top_k": 50,
        "rag_rerank_top_n": 10,
        "embedding_model": "voyage-multilingual-2",
        "pipeline_condense_history_limit": 12,
        "reranker_enabled": False,
        "citation_marker_required": True,
    })
    bot = SimpleNamespace(bot_name="alpha", oos_answer_template=None)

    out = await _build_pipeline_config(cfg, bot)

    assert out["top_k"] == 50
    assert out["rerank_top_n"] == 10
    assert out["embedding_model"] == "voyage-multilingual-2"
    assert out["condense_history_limit"] == 12
    assert out["reranker_enabled"] is False
    assert out["citation_marker_required"] is True


@pytest.mark.asyncio
async def test_pipeline_cfg_keys_contains_no_duplicates():
    """Sanity — duplicate key in batch list = wasted MGET slot."""
    assert len(_PIPELINE_CFG_KEYS) == len(set(_PIPELINE_CFG_KEYS))
