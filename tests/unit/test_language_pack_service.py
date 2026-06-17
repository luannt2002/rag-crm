"""Tests for ``LanguagePackService`` — the cache-first prompt resolver.

We exercise the four-layer fallback chain end-to-end with fakes:

    cache hit → DB row (lang) → DB row (default lang) → in-memory pack

…and the whole-pack ``get_pack`` path including default-language
merging so a partially translated language still produces a complete
prompt set without crashing orchestration.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.application.services.language_pack_service import (
    LanguagePackService,
    _decode_pack,
    _encode_pack,
)
from ragbot.shared.constants import LANGUAGE_PACK_PROMPT_KEYS


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.gets = 0
        self.sets = 0
        self.fail_get = False
        self.fail_set = False

    async def get(self, key: str) -> bytes | None:
        self.gets += 1
        if self.fail_get:
            raise OSError("boom")
        return self.store.get(key)

    async def set(self, key: str, value: bytes, *, ex: int | None = None) -> None:
        self.sets += 1
        if self.fail_set:
            raise OSError("boom")
        self.store[key] = value


class _FakeRepo:
    def __init__(self, table: dict[tuple[str, str], str]) -> None:
        self.table = dict(table)
        self.get_calls = 0
        self.list_calls = 0

    async def get_pack(self, code: str, prompt_key: str) -> str | None:
        self.get_calls += 1
        return self.table.get((code, prompt_key))

    async def list_pack(self, code: str) -> dict[str, str]:
        self.list_calls += 1
        return {k: v for (c, k), v in self.table.items() if c == code}


# ---------------------------------------------------------------------------
# get(language, prompt_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_db_value_and_caches() -> None:
    repo = _FakeRepo({("vi", "generator"): "vi-text"})
    redis = _FakeRedis()
    svc = LanguagePackService(repo=repo, redis_client=redis)

    out = await svc.get("vi", "generator")
    assert out == "vi-text"
    assert redis.sets == 1, "value should be cached after DB read"

    # Second call hits cache (no extra DB read)
    out2 = await svc.get("vi", "generator")
    assert out2 == "vi-text"
    assert repo.get_calls == 1


@pytest.mark.asyncio
async def test_get_falls_back_to_default_language() -> None:
    repo = _FakeRepo({("en", "generator"): "en-fallback"})
    svc = LanguagePackService(repo=repo, redis_client=_FakeRedis(), default_language="en")

    out = await svc.get("vi", "generator")
    # vi row missing → en row served
    assert out == "en-fallback"


@pytest.mark.asyncio
async def test_get_falls_back_to_inmemory_when_db_empty() -> None:
    repo = _FakeRepo({})
    svc = LanguagePackService(repo=repo, redis_client=_FakeRedis())

    out = await svc.get("vi", "generator")
    # In-memory pack ships a non-empty Vietnamese generator prompt.
    assert "context" in out.lower() or "tài liệu" in out.lower()


@pytest.mark.asyncio
async def test_get_returns_empty_string_for_unknown_prompt_key() -> None:
    repo = _FakeRepo({})
    svc = LanguagePackService(repo=repo, redis_client=_FakeRedis())
    assert await svc.get("vi", "this_key_does_not_exist") == ""


@pytest.mark.asyncio
async def test_get_survives_redis_outage() -> None:
    repo = _FakeRepo({("vi", "grader"): "ok"})
    redis = _FakeRedis()
    redis.fail_get = True
    redis.fail_set = True
    svc = LanguagePackService(repo=repo, redis_client=redis)
    # No exception, returns DB value despite Redis being broken.
    assert await svc.get("vi", "grader") == "ok"


# ---------------------------------------------------------------------------
# get_pack(language)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pack_returns_full_canonical_keyset() -> None:
    table = {
        ("vi", "generator"): "g",
        ("vi", "grader"): "ga",
        # rest of vi keys missing on purpose → must fall through
    }
    svc = LanguagePackService(
        repo=_FakeRepo(table),
        redis_client=_FakeRedis(),
    )
    pack = await svc.get_pack("vi")
    # Every canonical key resolves (DB + default-merge + inmemory chain).
    for key in LANGUAGE_PACK_PROMPT_KEYS:
        assert key in pack
    # DB-supplied keys take precedence
    assert pack["generator"] == "g"
    assert pack["grader"] == "ga"


@pytest.mark.asyncio
async def test_get_pack_merges_default_language_for_partial_translation() -> None:
    # Spanish only translates 'generator', leaves the rest missing.
    table = {
        ("es", "generator"): "es-gen",
        # Default language fully populated
        ("vi", "generator"): "vi-gen",
        ("vi", "grader"): "vi-gra",
        ("vi", "understand"): "vi-und",
        ("vi", "condense"): "vi-con",
        ("vi", "rewriter"): "vi-rew",
        ("vi", "reflector"): "vi-ref",
        ("vi", "decompose"): "vi-dec",
        ("vi", "greeting_answer"): "",
    }
    svc = LanguagePackService(
        repo=_FakeRepo(table),
        redis_client=_FakeRedis(),
        default_language="vi",
    )
    pack = await svc.get_pack("es")
    # es override wins
    assert pack["generator"] == "es-gen"
    # vi defaults fill the gaps
    assert pack["grader"] == "vi-gra"
    assert pack["decompose"] == "vi-dec"


@pytest.mark.asyncio
async def test_get_pack_caches_whole_blob() -> None:
    table = {("vi", "generator"): "g"}
    redis = _FakeRedis()
    repo = _FakeRepo(table)
    svc = LanguagePackService(repo=repo, redis_client=redis)
    p1 = await svc.get_pack("vi")
    list_calls_after_first = repo.list_calls
    p2 = await svc.get_pack("vi")
    # Second call served from cache → no additional list_pack.
    assert repo.list_calls == list_calls_after_first
    assert p1 == p2


# ---------------------------------------------------------------------------
# Encoding round-trip (whole-pack cache value)
# ---------------------------------------------------------------------------


def test_encode_decode_pack_roundtrip() -> None:
    payload = {"generator": "x", "grader": "with\nnewline", "decompose": ""}
    raw = _encode_pack(payload)
    assert _decode_pack(raw) == payload


def test_decode_pack_handles_empty_blob() -> None:
    assert _decode_pack(b"") == {}


def test_decode_pack_skips_malformed_records() -> None:
    # Build a value with a record missing the field separator.
    raw = b"key1\x1fvalue1\x1ebroken_no_sep\x1ekey2\x1fvalue2"
    out = _decode_pack(raw)
    assert out == {"key1": "value1", "key2": "value2"}
