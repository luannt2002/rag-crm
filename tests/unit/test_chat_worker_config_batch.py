"""Finding #2 perf invariant — chat_worker pulls system_config via a single
``SystemConfigService.get_many`` round-trip, NOT 65 sequential ``get*`` awaits.

The earlier implementation issued 65 ``await _cfg_svc.get(...)`` calls per
chat request. Each was a Redis hit (warm) or a Redis miss + DB SELECT
(cold) — measured at 60–80 ms of cumulative latency. The fix introduces
``SystemConfigService.get_many`` which fetches all keys in one Redis
MGET + at most one ``SELECT key, value FROM system_config WHERE key =
ANY(:keys)``.

This test pins the perf invariant: the chat-worker config bundle reads
ONE batch, and the batch returns the same per-key values as the
previous sequential calls would have. Regression here means the next
ship reintroduces N round-trips.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ragbot.application.services.system_config_service import (
    CACHE_PREFIX,
    SystemConfigService,
)


class _FakeRedis:
    """Minimal in-memory Redis double exposing ``get`` / ``mget`` / ``set``.

    Counts MGET vs GET so the perf invariant assertion can prove the
    chat-worker only issued ONE multi-key fetch instead of N singles.
    """

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        # Values stored already JSON-encoded so the service's ``json.loads``
        # path runs identically against the fake.
        self._store: dict[str, str] = {}
        if values:
            for k, v in values.items():
                self._store[CACHE_PREFIX + k] = json.dumps(v)
        self.mget_calls = 0
        self.get_calls = 0
        self.set_calls = 0
        self.del_calls = 0

    async def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self._store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls += 1
        return [self._store.get(k) for k in keys]

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls += 1
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self.del_calls += 1
        self._store.pop(key, None)


class _NoOpSessionFactory:
    """Session factory that never executes — for the all-cache-hit happy path.

    ``SystemConfigService.get_many`` must NOT touch the DB when every
    requested key sits in Redis. Wiring an explosive session factory
    makes that contract enforceable: a DB hit raises immediately.
    """

    def __call__(self):  # pragma: no cover — exercised only on regression
        raise AssertionError(
            "get_many fell through to DB despite full Redis hit — "
            "perf invariant broken",
        )


def test_get_many_uses_single_mget_when_all_cached() -> None:
    """All keys cached → exactly one ``MGET``, zero per-key ``GET``, zero DB.

    Pins the ``get_many`` fast path: one round-trip regardless of how
    many keys the chat-worker passes.
    """
    values = {f"key_{i}": i for i in range(65)}
    redis = _FakeRedis(values=values)
    svc = SystemConfigService(
        session_factory=_NoOpSessionFactory(),
        redis_client=redis,
    )

    keys = list(values.keys())
    out = asyncio.run(svc.get_many(keys))

    # Result correctness: every key resolves to its cached value.
    assert out == values, f"missing keys; got {sorted(out.keys())}"
    # Perf invariant: single MGET, no per-key GET, no SET.
    assert redis.mget_calls == 1, f"expected 1 MGET; got {redis.mget_calls}"
    assert redis.get_calls == 0, f"expected 0 GET; got {redis.get_calls}"
    assert redis.set_calls == 0, f"unexpected cache writes: {redis.set_calls}"


def test_get_many_empty_keys_short_circuits() -> None:
    """Calling with ``[]`` must not even hit Redis."""
    redis = _FakeRedis()
    svc = SystemConfigService(
        session_factory=_NoOpSessionFactory(),
        redis_client=redis,
    )
    out = asyncio.run(svc.get_many([]))
    assert out == {}
    assert redis.mget_calls == 0
    assert redis.get_calls == 0


def test_get_many_dedups_repeated_keys() -> None:
    """If the caller passes the same key twice, MGET still goes once and
    the duplicate doesn't appear in the result twice (dict semantics)."""
    values = {"a": 1, "b": 2}
    redis = _FakeRedis(values=values)
    svc = SystemConfigService(
        session_factory=_NoOpSessionFactory(),
        redis_client=redis,
    )
    out = asyncio.run(svc.get_many(["a", "b", "a", "b", "a"]))
    assert out == {"a": 1, "b": 2}
    assert redis.mget_calls == 1


def test_chat_worker_config_keys_bundle_is_complete() -> None:
    """The chat-worker's ``_CHAT_CONFIG_KEYS`` tuple covers every key the
    old sequential path used to fetch. Regression guard against future
    additions silently slipping back into per-key ``await get(...)``.
    """
    # Import locally so this test stays cheap when the module fails to import.
    from ragbot.interfaces.workers.chat_worker import _CHAT_CONFIG_KEYS

    # The historical 65-call set, captured from the pre-fix audit. Each
    # entry MUST stay in the batch tuple — adding a new key elsewhere
    # without registering here would re-introduce a serial round-trip.
    required = {
        "rag_rerank_top_n",
        "grounding_check_enabled",
        "graph_rag_default_mode",
        "rag_top_k",
        "embedding_dimension",
        "pipeline_condense_history_limit",
        "pipeline_reflect_answer_preview",
        "pipeline_crag_fallback_count",
        "pipeline_max_grade_retries",
        "pipeline_max_reflect_retries",
        "pipeline_cache_similarity_threshold",
        "pipeline_graph_recursion_limit",
        "reranker_model",
        "permission_filtering_enabled",
        "permission_default_public",
        "late_chunking_enabled",
        "prompt_compression_enabled",
        "prompt_compression_max_chars_per_chunk",
        "whole_doc_enabled",
        "whole_doc_threshold_chars",
        "parent_child_enabled",
        "graph_rag_max_hops",
        "vietnamese_preprocessing_enabled",
        "vietnamese_abbreviations",
        "bm25_use_cover_density",
        "bm25_normalization_flags",
        "grounding_check_threshold",
        "chat_max_history",
        "rag_max_documents",
        "prompt_max_tokens",
        "diacritic_restoration_enabled",
        "diacritic_restoration_use_model",
        "autocut_enabled",
        "autocut_min_gap_ratio",
        "reranker_min_score",
        "reranker_min_score_active",
        "reranker_min_score_bypass",
        "rerank_filter_strategy",
        "rerank_cliff_gap_ratio",
        "rerank_cliff_absolute_floor",
        "rerank_cliff_min_keep",
        "embedding_query_prefix",
        "semantic_cache_ttl_s",
        "crag_min_fallback_score",
        "multi_query_enabled",
        "multi_query_n_variants",
        "multi_query_max_variants",
        "multi_query_timeout_s",
        "multi_query_model",
        "generation_temperature",
        "skip_rewrite_intents",
        "skip_reflect_intents",
        "mmr_similarity_threshold",
        "mmr_lambda",
        "pipeline_merge_condense_router",
        "refuse_short_circuit_enabled",
        "rerank_skip_intents",
        "pipeline_timeout_s",
        "callback_max_retries",
        "callback_timeout_s",
        "callback_verify_ssl",
        "callback_hmac_secret",
    }
    missing = required - set(_CHAT_CONFIG_KEYS)
    assert not missing, (
        f"chat-worker batch tuple lost {sorted(missing)} — these will revert "
        "to N sequential ``await get(...)`` calls if not re-added"
    )


def test_chat_worker_has_no_sequential_cfg_awaits() -> None:
    """Source-level perf guard: chat_worker.py must NOT issue
    ``await _cfg_svc.get*`` calls outside the single batched ``get_many``.

    Counts each token via the file source so a regression PR that
    forgets to route a new key through the batch fails this test
    BEFORE landing on prod.
    """
    from pathlib import Path

    from ragbot.interfaces.workers import chat_worker

    # ``chat_worker`` was split into a package — concatenate every sub-module
    # so the perf guard sees all ``_cfg_svc.get*`` callsites.
    _pkg_dir = Path(chat_worker.__file__).parent
    source = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(_pkg_dir.glob("*.py"))
    )
    # Allow exactly one batched call — the ``get_many`` site.
    batch_count = source.count("_cfg_svc.get_many")
    sequential_count = source.count("await _cfg_svc.get")
    assert batch_count == 1, (
        f"expected exactly 1 ``_cfg_svc.get_many`` call; got {batch_count}"
    )
    # ``await _cfg_svc.get`` matches both ``get`` and ``get_many``; the
    # single match must be the batch call, so subtract it.
    serial = sequential_count - batch_count
    assert serial == 0, (
        f"found {serial} sequential ``await _cfg_svc.get*`` calls in "
        "chat_worker.py — Finding #2 regression"
    )
