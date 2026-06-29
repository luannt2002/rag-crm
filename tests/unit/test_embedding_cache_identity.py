"""F10 embed-cache identity tests — provider is part of the cache key.

The key shape is ``ragbot:emb:{provider}:{model}:{dim}:{hash}``. These pin:
  1. provider appears as its own segment in the key,
  2. two providers with the SAME model + dim resolve to DIFFERENT keys
     (no cross-provider vector poisoning),
  3. the key is byte-identical when provider + model + dim are unchanged,
  4. an unsupplied provider falls back to an explicit sentinel (not dropped),
  5. the read/write round-trip is scoped by provider (a write under one
     provider is invisible to a read under another).
"""

from __future__ import annotations

import pytest

from ragbot.shared.embedding_cache import (
    _CACHE_PREFIX,
    _MISSING,
    _cache_key,
    get_cached_embedding,
    set_cached_embedding,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value


def test_cache_key_includes_provider_segment() -> None:
    key = _cache_key("hello", model="model-a", dim=1024, provider="prov-x")
    assert key.startswith(_CACHE_PREFIX)
    body = key[len(_CACHE_PREFIX):]
    # Shape: <provider>:<model>:<dim>:<hash>
    segments = body.split(":")
    assert len(segments) == 4, f"expected 4 segments, got {segments}"
    assert segments[0] == "prov-x", "provider must be the first key segment"
    assert segments[1] == "model-a"
    assert segments[2] == "1024"


def test_two_providers_same_model_dim_differ() -> None:
    k_x = _cache_key("hello", model="shared-model", dim=1024, provider="prov-x")
    k_y = _cache_key("hello", model="shared-model", dim=1024, provider="prov-y")
    assert k_x != k_y, (
        "same model+dim under different providers MUST NOT collide "
        "(cross-provider vector poisoning)"
    )
    # Sanity: only the provider segment differs.
    assert k_x.split(":")[3:] == k_y.split(":")[3:]


def test_cache_key_byte_identical_when_identity_unchanged() -> None:
    a = _cache_key("the same text", model="m", dim=512, provider="p")
    b = _cache_key("the same text", model="m", dim=512, provider="p")
    assert a == b, "identical provider+model+dim+text MUST be deterministic"


def test_changing_only_provider_changes_key() -> None:
    base = _cache_key("q", model="m", dim=8, provider="p1")
    swapped = _cache_key("q", model="m", dim=8, provider="p2")
    assert base != swapped


def test_missing_provider_uses_explicit_sentinel_not_dropped() -> None:
    # No provider supplied -> explicit sentinel segment, never an empty/
    # collapsed key. Default-call key must equal an explicit-sentinel call.
    defaulted = _cache_key("q", model="m", dim=8)
    explicit = _cache_key("q", model="m", dim=8, provider=_MISSING)
    assert defaulted == explicit
    body = defaulted[len(_CACHE_PREFIX):]
    assert body.split(":")[0] == _MISSING
    # 4 segments preserved even with the sentinel provider.
    assert len(body.split(":")) == 4


def test_empty_provider_string_falls_back_to_sentinel() -> None:
    key = _cache_key("q", model="m", dim=8, provider="")
    body = key[len(_CACHE_PREFIX):]
    assert body.split(":")[0] == _MISSING


def test_missing_model_emits_warning_not_silent() -> None:
    # "missing-model must be explicit": building a key with the model sentinel
    # must emit an observable structlog warning rather than silently bucketing.
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        _cache_key("q", model="", dim=8, provider="p")
    assert any(
        e.get("event") == "embedding_cache_model_missing" for e in logs
    ), "missing model must produce an observable warning, not a silent bucket"


async def test_round_trip_scoped_by_provider() -> None:
    redis = _FakeRedis()
    vec = [0.11, 0.22, 0.33]
    await set_cached_embedding(redis, "hi", vec, provider="prov-x", model="m", dim=3)

    # Same provider -> hit, exact vector back.
    hit = await get_cached_embedding(redis, "hi", provider="prov-x", model="m", dim=3)
    assert hit == vec

    # Different provider, same model+dim+text -> miss (no cross-provider serve).
    miss = await get_cached_embedding(redis, "hi", provider="prov-y", model="m", dim=3)
    assert miss is None
