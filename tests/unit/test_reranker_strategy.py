"""Reranker Strategy Pattern + DI registry — unit tests.

Coverage:
- NullReranker returns chunks unchanged with retrieval order preserved.
- Registry default falls back to NullReranker when provider is empty/unknown.
- Registry resolves "litellm" to LiteLLMReranker.
- Registry's ``list_providers()`` is sorted + includes the baseline strategies.
- Each strategy exposes ``get_provider_name()`` for audit-log tagging.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.reranker import (
    LiteLLMReranker,
    NullReranker,
    build_reranker,
    list_providers,
)


@pytest.mark.asyncio
async def test_null_reranker_returns_chunks_in_retrieval_order() -> None:
    rr = NullReranker()
    chunks = [
        {"id": "a", "content": "first", "score": 0.7},
        {"id": "b", "content": "second", "score": 0.5},
        {"id": "c", "content": "third", "score": 0.3},
    ]
    out = await rr.rerank("any query", chunks, top_n=2)

    # Order preserved (no sorting), top_n applied, scores untouched.
    assert [c["id"] for c in out] == ["a", "b"]
    assert out[0]["score"] == pytest.approx(0.7)
    assert out[1]["score"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_null_reranker_handles_empty_input() -> None:
    rr = NullReranker()
    assert await rr.rerank("q", [], top_n=5) == []
    assert await rr.health_check() is True


def test_registry_default_provider_is_null() -> None:
    # Empty / None / explicit "null" all yield NullReranker.
    assert isinstance(build_reranker(""), NullReranker)
    assert isinstance(build_reranker(None), NullReranker)
    assert isinstance(build_reranker("null"), NullReranker)


def test_registry_unknown_provider_falls_back_to_null() -> None:
    rr = build_reranker("definitely-not-a-provider-name")
    assert isinstance(rr, NullReranker)


def test_registry_resolves_litellm_strategy() -> None:
    # LiteLLMReranker constructs without contacting Cohere — health_check is
    # the only path that reaches the network. So this is safe in unit tests.
    rr = build_reranker("litellm", model="cohere/rerank-v3.5")
    assert isinstance(rr, LiteLLMReranker)


def test_list_providers_returns_sorted_baseline() -> None:
    providers = list_providers()
    # Stable sorted output keeps tests deterministic.
    assert providers == sorted(providers)
    # Baseline strategies always present.
    assert "null" in providers
    assert "litellm" in providers


def test_provider_names_match_registry_keys() -> None:
    # Each strategy advertises the same key it is registered under so audit
    # logs and the registry stay aligned even when someone adds a provider.
    assert NullReranker.get_provider_name() == "null"
    assert LiteLLMReranker.get_provider_name() == "litellm"


def test_registry_case_insensitive_provider_lookup() -> None:
    # Operators sometimes uppercase config; the registry normalises to lower.
    rr = build_reranker("LITELLM", model="cohere/rerank-v3.5")
    assert isinstance(rr, LiteLLMReranker)
